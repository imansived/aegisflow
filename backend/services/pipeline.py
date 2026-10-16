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
    ):
        self.detection = DetectionEngine(enrolled, det_cfg)
        self.correlation = CorrelationEngine(fusion, corr_cfg)
        self.alerts = AlertManager(alert_cfg, corr_cfg)
        self.store, self.on_alert, self.on_fused = store, on_alert, on_fused

    def process(self, t: Telemetry) -> Fused:
        fused = self.correlation.update(self.detection.process(t))
        if self.on_fused:
            self.on_fused(fused)
        for ev in self.alerts.update(fused):
            if self.store:
                self.store.upsert_alert(ev)
            if self.on_alert:
                self.on_alert(ev)
        return fused

    def __call__(self, t: Telemetry) -> None:
        self.process(t)
