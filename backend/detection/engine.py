"""Runs all four detectors on each event. This is the Processor's `handler`."""

from collections.abc import Callable, Iterable

from contracts.geo import haversine_m
from contracts.scenarios import Modality
from contracts.site import is_working, zone_of
from contracts.telemetry import Telemetry

from .config import DetectionConfig
from .detectors import DeviceDetector, NetworkDetector, ProximityDetector, SpatialDetector
from .types import Context, DetectorResult, Scores

MAX_GAP_S = 60.0  # a longer silence than this and "speed since last fix" is meaningless


class DetectionEngine:
    def __init__(
        self,
        enrolled: Iterable[str],
        cfg: DetectionConfig | None = None,
        sink: Callable[[Scores], None] | None = None,
        declared: dict[str, frozenset[str]] | None = None,
    ):
        self.cfg = cfg or DetectionConfig()
        # A mutable set is kept by REFERENCE, so devices registered later (POST /devices) are
        # recognised as enrolled without rebuilding the engine. Anything else is frozen.
        self.enrolled = enrolled if isinstance(enrolled, set) else frozenset(enrolled)
        self.sink = sink
        # device -> the signal groups it supports (R3). A device with no entry supports all four. Kept BY REFERENCE, like
        # `enrolled`, so a device declared after start-up is recognised at once.
        self.declared = declared if declared is not None else {}
        self.ignored_undeclared = 0  # values received for a signal the device does not support (ignored)
        self.detectors = [
            SpatialDetector(self.cfg),
            NetworkDetector(self.cfg),
            DeviceDetector(self.cfg),
            ProximityDetector(self.cfg, self.enrolled),
        ]
        self._prev: dict[str, tuple[float, float, float]] = {}  # device -> (time, lat, lon) of its last GPS fix
        self._last_event: dict[str, float] = {}  # device -> time of its last event of any kind

    def process(self, t: Telemetry) -> Scores:
        now_s = t.timestamp.timestamp()
        reported_null = t.unavailable  # signal groups the device reported as unavailable this tick
        decl = self.declared.get(t.device_id)
        unsupported = frozenset() if decl is None else frozenset(m.value for m in Modality) - decl
        self.ignored_undeclared += len(unsupported - reported_null)
        lost = reported_null | unsupported  # an unsupported signal is never read, even if the device sent something
        # Time since the device's previous event (any event, with or without a GPS fix): the proximity detector's
        # persistence clock depends on it, so losing the GPS signal must not stretch it.
        last_t = self._last_event.get(t.device_id)
        self._last_event[t.device_id] = now_s
        dt_s = now_s - last_t if last_t is not None and now_s > last_t else None
        # Speed needs two fixes: only measured when this tick has a fix and the last fix was recent.
        prev, speed = self._prev.get(t.device_id), None
        if "spatial" not in lost:
            if prev is not None and 0 < now_s - prev[0] <= MAX_GAP_S:
                speed = haversine_m((prev[1], prev[2]), (t.latitude, t.longitude)) / (now_s - prev[0])
            self._prev[t.device_id] = (now_s, t.latitude, t.longitude)

        ctx = Context(
            t=t,
            now_s=now_s,
            bucket="work" if is_working(t.timestamp) else "off",
            dt_s=dt_s,
            zone=None if "spatial" in lost else zone_of((t.latitude, t.longitude)),
            speed_mps=speed,
        )
        results, features = {}, {}
        for det in self.detectors:
            if det.modality.value in lost:
                # Nothing was observed: no score, no baseline update, no features. Never a zero reading.
                results[det.modality] = DetectorResult(det.modality, 0.0, False, {}, 0.0, available=False)
                det.note_gap(t.device_id, now_s)
                continue
            res, feats = det.step(ctx)
            results[res.modality] = res
            features |= feats
        scores = Scores(t.device_id, t.timestamp, ctx.bucket, results, features,
                        frozenset(Modality(g) for g in reported_null), frozenset(Modality(g) for g in unsupported))
        if self.sink:
            self.sink(scores)
        return scores

    def reset_device(self, device_id: str) -> None:
        """A device's clock jumped backwards: clear its time-based state, keep what it has learned."""
        self._prev.pop(device_id, None)
        self._last_event.pop(device_id, None)
        for det in self.detectors:
            det.reset_windows(device_id)

    def __call__(self, t: Telemetry) -> None:
        """Lets the engine be passed straight to `Processor(handler=engine)`."""
        self.process(t)

    def replay(self, events: Iterable[Telemetry]) -> int:
        """Rebuild all detector state from stored events (manual §3.19): the recovery path."""
        n = 0
        for t in events:
            self.process(t)
            n += 1
        return n
