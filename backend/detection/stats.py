"""Online statistics (manual §3.3)."""

from math import sqrt

from .config import DetectionConfig


class RunningStat:
    """Running mean and variance in O(1) memory.

    Phase 1, n <= warm_n: Welford's algorithm, which is exact and numerically stable.
    Phase 2, n  > warm_n: exponentially weighted update, so the baseline adapts to legitimate
    drift instead of becoming an ever-slower average of all of history.
    """

    __slots__ = ("n", "mean", "m2", "var")

    def __init__(self) -> None:
        self.n = 0
        self.mean = 0.0
        self.m2 = 0.0
        self.var = 0.0

    def update(self, x: float, warm_n: int, alpha: float) -> None:
        self.n += 1
        if self.n <= warm_n:
            delta = x - self.mean
            self.mean += delta / self.n
            self.m2 += delta * (x - self.mean)  # uses the UPDATED mean; the classic bug is the old one
            self.var = self.m2 / (self.n - 1) if self.n > 1 else 0.0
        else:
            d = x - self.mean
            self.mean += alpha * d
            self.var = (1 - alpha) * (self.var + alpha * d * d)

    @property
    def std(self) -> float:
        return sqrt(self.var)


def ramp(z: float, lo: float, hi: float) -> float:
    """Map a z-score to [0, 1]: 0 up to `lo`, 1 from `hi`, linear between."""
    if z <= lo:
        return 0.0
    if z >= hi:
        return 1.0
    return (z - lo) / (hi - lo)


class BaselineSet:
    """All of one device's baselines for one detector, keyed by (bucket, feature)."""

    def __init__(self, cfg: DetectionConfig):
        self.cfg = cfg
        self._stats: dict[tuple[str, str], RunningStat] = {}

    def stat(self, bucket: str, feature: str) -> RunningStat:
        key = (bucket, feature)
        s = self._stats.get(key)
        if s is None:
            s = self._stats[key] = RunningStat()
        return s

    def ready(self, bucket: str, feature: str) -> bool:
        return self.stat(bucket, feature).n >= self.cfg.min_samples

    def z(self, bucket: str, feature: str, x: float, min_std: float, sided: str) -> float | None:
        """Standardised deviation, or None while the baseline is still learning.

        `min_std` floors the spread: a feature that is almost constant would otherwise give
        an enormous z to any tiny change. `sided` is "high" (only increases are suspicious)
        or "both".
        """
        s = self.stat(bucket, feature)
        if s.n < self.cfg.min_samples:
            return None
        z = (x - s.mean) / max(s.std, min_std)
        return abs(z) if sided == "both" else z

    def learn(self, bucket: str, feature: str, x: float) -> None:
        self.stat(bucket, feature).update(x, self.cfg.warm_n, 1.0 / self.cfg.ew_window)
