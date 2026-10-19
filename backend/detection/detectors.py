"""The four per-modality detectors (manual §3.8).

Each takes one event (plus the device's own history) and returns a score in [0, 1] and the
evidence behind it. They share one pattern:

    1. compute features
    2. score each against the device's baseline (None while still learning)
    3. the detector's score is the MAX over its features (any one extreme feature is enough)
    4. learn from the observation only if it was not anomalous (poison resistance)
"""

from abc import ABC, abstractmethod
from collections import defaultdict, deque
from math import log1p

from contracts.scenarios import Modality
from contracts.site import ZoneKind

from .config import DetectionConfig
from .stats import BaselineSet, ramp, soft_ramp
from .types import Context, DetectorResult

# (name, value, min_std, sided)
Item = tuple[str, float, float, str]

# Hard cap on every sliding window. Windows are trimmed by TIME, so if a device's clock ever goes backwards
# nothing would be trimmed and each event would cost more than the last (a 57x slowdown was measured, manual
# §4.9). A cap makes the cost bounded whatever the timestamps say. 1000 entries covers a 5-minute window at
# 3 events/s, above the ingestion rate limit.
MAX_WINDOW_EVENTS = 1000


def _window() -> deque:
    return deque(maxlen=MAX_WINDOW_EVENTS)


class Detector(ABC):
    modality: Modality

    def __init__(self, cfg: DetectionConfig):
        self.cfg = cfg
        self._baselines: dict[str, BaselineSet] = defaultdict(lambda: BaselineSet(cfg))

    @abstractmethod
    def step(self, ctx: Context) -> tuple[DetectorResult, dict[str, float]]:
        """Return (result, raw features) for this event."""

    def reset_windows(self, device_id: str) -> None:
        """Forget a device's TIME-based state (windows, run counters) after its clock jumped backwards.

        Learned knowledge (baselines, zone occupancy, known endpoints) is kept: it describes behaviour,
        not time. Default: nothing to clear."""

    # ---- shared mechanics ------------------------------------------------------------

    def _score_items(self, bs: BaselineSet, bucket: str, items: list[Item]):
        """Returns (hard score, learning, evidence, soft score) for these features."""
        best, soft, learning, ev = 0.0, 0.0, False, {}
        for name, x, min_std, sided in items:
            z = bs.z(bucket, name, x, min_std, sided)
            if z is None:
                learning = True
                continue
            ev[name] = round(z, 2)
            best = max(best, ramp(z, self.cfg.z_lo, self.cfg.z_hi))
            soft = max(soft, soft_ramp(z, self.cfg.soft_sigma))
        return best, learning, ev, soft

    def _learn_items(self, bs: BaselineSet, bucket: str, items: list[Item], score: float) -> None:
        for name, x, _min_std, _sided in items:
            # Always learn while cold; once ready, only learn from non-anomalous data.
            if not bs.ready(bucket, name) or score < self.cfg.learn_gate:
                bs.learn(bucket, name, x)


# =============================================================================== spatial


class _SpatialState:
    def __init__(self) -> None:
        self.restricted_run = 0
        self.bucket: str | None = None
        self.bucket_since = 0.0
        self.occ: dict[str, dict[str, int]] = defaultdict(lambda: defaultdict(int))
        self.occ_total: dict[str, int] = defaultdict(int)


class SpatialDetector(Detector):
    modality = Modality.SPATIAL

    def __init__(self, cfg: DetectionConfig):
        super().__init__(cfg)
        self._state: dict[str, _SpatialState] = defaultdict(_SpatialState)

    def reset_windows(self, device_id: str) -> None:
        s = self._state.get(device_id)
        if s:  # keep the learned zone occupancy; forget the run counter and the bucket-change clock
            s.restricted_run, s.bucket, s.bucket_since = 0, None, 0.0

    def step(self, ctx: Context):
        cfg, dev = self.cfg, ctx.t.device_id
        s, bs, bucket, zone = self._state[dev], self._baselines[dev], ctx.bucket, ctx.zone

        if s.bucket != bucket:
            s.bucket, s.bucket_since = bucket, ctx.now_s
        in_grace = ctx.now_s - s.bucket_since < cfg.boundary_grace_s

        learning, ev = False, {}
        # Speed: a physical-plausibility rule (see config), not a learned baseline.
        speed_score = 0.0
        if ctx.speed_mps is not None:
            speed_score = ramp(ctx.speed_mps, cfg.speed_lo_mps, cfg.speed_hi_mps)
            if speed_score:
                ev["speed_mps"] = round(ctx.speed_mps, 1)

        # Rule: restricted-zone entry. One tick could be GPS jitter at a boundary, so the
        # first tick scores 0.5 and two consecutive ticks score 1.0 (a debounce).
        in_restricted = zone is not None and zone.kind is ZoneKind.RESTRICTED
        s.restricted_run = s.restricted_run + 1 if in_restricted else 0
        rule = 1.0 if s.restricted_run >= 2 else 0.5 if s.restricted_run == 1 else 0.0
        if rule:
            ev["restricted_zone"] = rule

        # Rarity: how unusual is this ALLOWED zone for this device at this time of day?
        occ_ready = s.occ_total[bucket] >= cfg.occ_min_samples
        rarity, frac = 0.0, None
        if zone is not None and not in_restricted:
            if in_grace:
                pass  # people are legitimately moving about just after a boundary
            elif occ_ready:
                frac = s.occ[bucket][zone.name] / s.occ_total[bucket]
                rarity = cfg.rare_max * min(1.0, max(0.0, (cfg.rare_frac - frac) / cfg.rare_frac))
                if rarity:
                    ev["zone_rarity"] = round(rarity, 2)
            else:
                learning = True

        score = max(speed_score, rule, rarity)
        if zone is not None and not in_restricted and (not occ_ready or score < cfg.learn_gate):
            s.occ[bucket][zone.name] += 1
            s.occ_total[bucket] += 1

        feats = {
            "speed": ctx.speed_mps if ctx.speed_mps is not None else 0.0,
            "in_restricted": float(in_restricted),
            "zone_rarity": rarity,
        }
        # Spatial is rule-based, so there is no z-score to soften: soft == hard.
        return DetectorResult(self.modality, score, learning, ev, soft=score), feats


# =============================================================================== network


class _NetworkState:
    def __init__(self) -> None:
        self.first_s: float | None = None
        self.counts: dict[str, int] = defaultdict(int)
        self.last_counted: dict[str, float] = {}
        self.window: deque[tuple[float, int]] = _window()  # (time, unfamiliar endpoints that tick)
        self.recent: deque[tuple[float, float, float, float]] = _window()  # (time, log_sent, log_recv, conns)


class NetworkDetector(Detector):
    modality = Modality.NETWORK

    def __init__(self, cfg: DetectionConfig):
        super().__init__(cfg)
        self._state: dict[str, _NetworkState] = defaultdict(_NetworkState)

    def reset_windows(self, device_id: str) -> None:
        s = self._state.get(device_id)
        if s:  # keep which endpoints are known; forget the time windows and the rate-limit clocks
            s.window.clear()
            s.recent.clear()
            s.last_counted.clear()
            s.first_s = None  # the "endpoints have had time to form" timer restarts in the new epoch

    def step(self, ctx: Context):
        cfg, t, dev = self.cfg, ctx.t, ctx.t.device_id
        s, bs, bucket = self._state[dev], self._baselines[dev], ctx.bucket
        if s.first_s is None:
            s.first_s = ctx.now_s

        # An endpoint is "known" only after repeated contact spread over time, so a short
        # burst of attacker traffic can never make an endpoint known (poison resistance).
        known = {e for e, c in s.counts.items() if c >= cfg.endpoint_known_count}
        n_unfamiliar = sum(1 for e in t.remote_endpoints if e not in known)
        s.window.append((ctx.now_s, n_unfamiliar))
        while s.window and ctx.now_s - s.window[0][0] > cfg.unfamiliar_window_s:
            s.window.popleft()
        unfam = float(sum(n for _, n in s.window))

        # One-tick traffic spikes are ordinary (Poisson / lognormal tails); judge the average
        # over the last minute instead, like the device detector does.
        rec = s.recent
        rec.append((ctx.now_s, log1p(t.bytes_sent), log1p(t.bytes_received), float(t.network_connections)))
        while rec and ctx.now_s - rec[0][0] > cfg.smooth_window_s:
            rec.popleft()
        n = len(rec)
        items: list[Item] = [
            ("log_sent", sum(r[1] for r in rec) / n, cfg.bytes_min_std, "high"),
            ("log_recv", sum(r[2] for r in rec) / n, cfg.bytes_min_std, "high"),
            ("conns", sum(r[3] for r in rec) / n, cfg.conns_min_std, "high"),
        ]
        # Until the known-endpoint set has had time to form, "unfamiliar" is meaningless.
        endpoints_mature = ctx.now_s - s.first_s >= cfg.endpoint_learn_s
        if endpoints_mature:
            items.append(("unfamiliar_1m", unfam, cfg.unfamiliar_min_std, "high"))

        score, learning, ev, soft = self._score_items(bs, bucket, items)
        learning = learning or not endpoints_mature
        self._learn_items(bs, bucket, items, score)

        if not endpoints_mature or score < cfg.learn_gate:
            for e in t.remote_endpoints:
                if ctx.now_s - s.last_counted.get(e, -1e18) >= cfg.endpoint_count_gap_s:
                    s.counts[e] += 1
                    s.last_counted[e] = ctx.now_s

        feats = {name: x for name, x, _m, _s in items}
        feats.setdefault("unfamiliar_1m", unfam)
        return DetectorResult(self.modality, score, learning, ev, soft), feats


# =============================================================================== device


class _DeviceState:
    def __init__(self) -> None:
        self.battery_hist: deque[tuple[float, float]] = _window()  # (time, battery %)
        self.recent: deque[tuple[float, float, float]] = _window()  # (time, cpu, temp)


class DeviceDetector(Detector):
    modality = Modality.DEVICE

    def __init__(self, cfg: DetectionConfig):
        super().__init__(cfg)
        self._state: dict[str, _DeviceState] = defaultdict(_DeviceState)

    def reset_windows(self, device_id: str) -> None:
        s = self._state.get(device_id)
        if s:
            s.battery_hist.clear()
            s.recent.clear()

    def step(self, ctx: Context):
        cfg, t, dev = self.cfg, ctx.t, ctx.t.device_id
        s, bs, bucket = self._state[dev], self._baselines[dev], ctx.bucket

        hist = s.battery_hist
        hist.append((ctx.now_s, t.battery))
        while hist and ctx.now_s - hist[0][0] > cfg.drain_window_s:
            hist.popleft()
        drain = None
        span = ctx.now_s - hist[0][0]
        if span >= cfg.drain_window_s * 0.8:
            d = (hist[0][1] - t.battery) / (span / 3600)  # percentage points per hour
            if d > cfg.charging_threshold:  # skip while charging: not a drain at all
                drain = d

        rec = s.recent
        rec.append((ctx.now_s, t.cpu_usage, t.temperature))
        while rec and ctx.now_s - rec[0][0] > cfg.smooth_window_s:
            rec.popleft()
        cpu_m = sum(r[1] for r in rec) / len(rec)
        temp_m = sum(r[2] for r in rec) / len(rec)

        items: list[Item] = [
            ("cpu", cpu_m, cfg.cpu_min_std, "high"),
            ("temp", temp_m, cfg.temp_min_std, "high"),
        ]
        if drain is not None:
            items.append(("drain", drain, cfg.drain_min_std, "high"))

        score, learning, ev, soft = self._score_items(bs, bucket, items)
        self._learn_items(bs, bucket, items, score)
        feats = {name: x for name, x, _m, _s in items}
        feats.setdefault("drain", drain if drain is not None else 0.0)
        return DetectorResult(self.modality, score, learning, ev, soft), feats


# ============================================================================= proximity


class _ProximityState:
    def __init__(self) -> None:
        self.persist_s = 0.0


class ProximityDetector(Detector):
    """Keys on IDENTITY, not count (manual §4.1, D26): neighbour counts are far too noisy.

    A neighbour that is not in the enrolled-device registry is "unenrolled". Brief
    unenrolled neighbours are normal (visitors); a persistent one is not.
    """

    modality = Modality.PROXIMITY

    def __init__(self, cfg: DetectionConfig, enrolled: "frozenset[str] | set[str]"):
        super().__init__(cfg)
        self.enrolled = enrolled
        self._state: dict[str, _ProximityState] = defaultdict(_ProximityState)

    def reset_windows(self, device_id: str) -> None:
        s = self._state.get(device_id)
        if s:
            s.persist_s = 0.0

    def step(self, ctx: Context):
        cfg, dev = self.cfg, ctx.t.device_id
        s, bs, bucket = self._state[dev], self._baselines[dev], ctx.bucket

        n_unenrolled = sum(1 for d in ctx.t.nearby_devices if d not in self.enrolled)
        if n_unenrolled:
            s.persist_s += ctx.dt_s or 0.0
        else:
            s.persist_s = 0.0

        items: list[Item] = [
            ("unenrolled", float(n_unenrolled), cfg.unenrolled_min_std, "high"),
            ("persist_s", s.persist_s, cfg.persist_min_std, "high"),
        ]
        score, learning, ev, soft = self._score_items(bs, bucket, items)
        self._learn_items(bs, bucket, items, score)
        feats = {"unenrolled": float(n_unenrolled), "persist_s": s.persist_s}
        return DetectorResult(self.modality, score, learning, ev, soft), feats
