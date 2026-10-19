"""Runs all four detectors on each event. This is the Processor's `handler`."""

from collections.abc import Callable, Iterable

from contracts.geo import haversine_m
from contracts.site import is_working, zone_of
from contracts.telemetry import Telemetry

from .config import DetectionConfig
from .detectors import DeviceDetector, NetworkDetector, ProximityDetector, SpatialDetector
from .types import Context, Scores

MAX_GAP_S = 60.0  # a longer silence than this and "speed since last fix" is meaningless


class DetectionEngine:
    def __init__(
        self,
        enrolled: Iterable[str],
        cfg: DetectionConfig | None = None,
        sink: Callable[[Scores], None] | None = None,
    ):
        self.cfg = cfg or DetectionConfig()
        # A mutable set is kept by REFERENCE, so devices registered later (POST /devices) are
        # recognised as enrolled without rebuilding the engine. Anything else is frozen.
        self.enrolled = enrolled if isinstance(enrolled, set) else frozenset(enrolled)
        self.sink = sink
        self.detectors = [
            SpatialDetector(self.cfg),
            NetworkDetector(self.cfg),
            DeviceDetector(self.cfg),
            ProximityDetector(self.cfg, self.enrolled),
        ]
        self._prev: dict[str, tuple[float, float, float]] = {}  # device -> (time, lat, lon)

    def process(self, t: Telemetry) -> Scores:
        now_s = t.timestamp.timestamp()
        prev = self._prev.get(t.device_id)
        dt_s = speed = None
        if prev is not None:
            dt = now_s - prev[0]
            if 0 < dt <= MAX_GAP_S:
                dt_s = dt
                speed = haversine_m((prev[1], prev[2]), (t.latitude, t.longitude)) / dt
            elif dt > MAX_GAP_S:
                dt_s = dt
        self._prev[t.device_id] = (now_s, t.latitude, t.longitude)

        ctx = Context(
            t=t,
            now_s=now_s,
            bucket="work" if is_working(t.timestamp) else "off",
            dt_s=dt_s,
            zone=zone_of((t.latitude, t.longitude)),
            speed_mps=speed,
        )
        results, features = {}, {}
        for det in self.detectors:
            res, feats = det.step(ctx)
            results[res.modality] = res
            features |= feats
        scores = Scores(t.device_id, t.timestamp, ctx.bucket, results, features)
        if self.sink:
            self.sink(scores)
        return scores

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
