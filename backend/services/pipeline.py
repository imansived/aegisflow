"""The whole analysis chain as one callable: telemetry in, alerts out.

    Telemetry -> DetectionEngine -> CorrelationEngine -> AlertManager -> store + subscribers

It is a valid `handler` for streaming.Processor. Alerts are written to the SAME store inside
the processor's transaction, so if handling an event fails, its alert write rolls back too.
"""

from collections.abc import Callable, Iterable

from backend.correlation.engine import CorrelationConfig, CorrelationEngine, Fused
from backend.correlation.fusion import Fusion
from backend.database.store import EventStore
from backend.detection.config import DetectionConfig
from backend.detection.engine import DetectionEngine
from contracts.telemetry import Telemetry

from .alerts import AlertConfig, AlertManager


MAX_LATE_S = 120.0  # an event this much older than the device's latest is treated as late, not as a clock reset


class Pipeline:
    def __init__(
        self,
        enrolled: Iterable[str],
        fusion: Fusion,
        store: EventStore | None = None,
        *,
        det_cfg: DetectionConfig | None = None,
        corr_cfg: CorrelationConfig | None = None,
        alert_cfg: AlertConfig | None = None,
        on_alert: Callable[[dict], None] | None = None,
        on_fused: Callable[[Fused], None] | None = None,
        on_processed: Callable[[Telemetry, Fused], None] | None = None,
    ):
        self.detection = DetectionEngine(enrolled, det_cfg)
        self.correlation = CorrelationEngine(fusion, corr_cfg)
        self.alerts = AlertManager(alert_cfg, corr_cfg)
        self.store, self.on_alert, self.on_fused = store, on_alert, on_fused
        self.on_processed = on_processed  # gets the event AND its result: the live dashboard needs both
        self._last_ts: dict[str, float] = {}
        self.late_events = 0  # events dropped for arriving slightly out of order
        self.clock_resets = 0  # times a device's clock jumped far backwards and its time state was reset

    def _publish(self, ev: dict) -> None:
        if self.store:
            self.store.upsert_alert(ev)
        if self.on_alert:
            self.on_alert(ev)

    def process(self, t: Telemetry) -> Fused | None:
        """Analyse one event. Returns None if it was dropped as late.

        Detectors keep TIME-based windows, so an event older than the device's last one must not be fed in
        as if time were moving forward. A slightly late event (within MAX_LATE_S) is dropped and counted.
        A device whose clock jumped far back (a reset, or an attacker rewinding it) starts a new epoch: its
        time state is cleared, any open alert is closed, and analysis carries on. Dropping such a device's
        events forever would let it blind the monitor by setting its clock back."""
        now = t.timestamp.timestamp()
        last = self._last_ts.get(t.device_id)
        if last is not None and now < last:
            if last - now <= MAX_LATE_S:
                self.late_events += 1
                return None
            self.clock_resets += 1
            self.detection.reset_device(t.device_id)
            self.correlation.reset_device(t.device_id)
            for ev in self.alerts.reset_device(t.device_id):
                self._publish(ev)
        self._last_ts[t.device_id] = now

        fused = self.correlation.update(self.detection.process(t))
        if self.on_fused:
            self.on_fused(fused)
        if self.on_processed:
            self.on_processed(t, fused)
        for ev in self.alerts.update(fused):
            self._publish(ev)
        return fused

    def __call__(self, t: Telemetry) -> None:
        self.process(t)
