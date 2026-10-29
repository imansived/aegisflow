"""R2 candidate network detectors (protocol: docs/preregistration/R2-sparse-network-detector.md, section 2).

Offline, per device trace, beside (never inside) the research-v1 detector. Each takes a trace and the v1 detector's `unfamiliar_1m` z-score per tick
(unchanged, combined by max with the candidate's own volume z) and returns a score per tick: 0 during cold start, otherwise 1 - exp(-0.5 (z/3)^2) for z > 0.
Every constant below is the protocol's, none is tuned.
"""

import math
from bisect import bisect_left, insort
from collections import deque
from statistics import NormalDist

import numpy as np

from backend.detection.config import DetectionConfig
from backend.detection.stats import RunningStat, soft_ramp

CFG = DetectionConfig()
W = 12  # ticks in 60 s
COLD = 720
STORE = 17_280  # 24 h of ticks
_ND = NormalDist()


def _ppf(q: float) -> float:
    return _ND.inv_cdf(min(1 - 1e-12, max(1e-12, q)))


def _tail_binomial(n: int, p: float, k: int) -> float:
    """P(X >= k) for X ~ Binomial(n, p)."""
    return sum(math.comb(n, j) * p**j * (1 - p) ** (n - j) for j in range(k, n + 1))


def _combine(z_vol: float, z_unf: float) -> float:
    z = max(z_vol, z_unf if math.isfinite(z_unf) else 0.0)
    return soft_ramp(z, CFG.soft_sigma)


def score_zero_inflated(sent: np.ndarray, recv: np.ndarray, conns: np.ndarray, z_unf: np.ndarray) -> np.ndarray:
    """Candidate A. An activity-probability model plus a magnitude model on active ticks only."""
    n = len(sent)
    out = np.zeros(n)
    alpha = 1.0 / CFG.ew_window
    p_stat, mag_stat = RunningStat(), RunningStat()
    window: deque = deque(maxlen=W)  # (active, mag)
    n_active = 0
    for i in range(n):
        vol = int(sent[i]) + int(recv[i])
        active = vol > 0 or conns[i] > 0
        mag = math.log1p(vol)
        window.append((active, mag))
        if i >= COLD and n_active >= 50 and len(window) == W:
            p = min(0.98, max(0.02, p_stat.mean))
            k = sum(1 for a, _ in window if a)
            z_count = _ppf(1.0 - _tail_binomial(W, p, k)) if k else 0.0
            mags = [m for a, m in window if a]
            z_mag = (sum(mags) / len(mags) - mag_stat.mean) / max(mag_stat.std, 0.5) if mags else 0.0
            out[i] = _combine(max(z_count, z_mag), float(z_unf[i]))
        p_stat.update(1.0 if active else 0.0, CFG.warm_n, alpha)
        if active:
            mag_stat.update(mag, CFG.warm_n, alpha)
            n_active += 1
    return out


def score_windowed_rate(sent: np.ndarray, recv: np.ndarray, conns: np.ndarray, z_unf: np.ndarray) -> np.ndarray:
    """Candidate B. Window rates against an empirical, non-parametric baseline (upper-tail probability -> z)."""
    n = len(sent)
    out = np.zeros(n)
    vol = (sent + recv).astype(np.float64)
    c1 = np.concatenate([[0.0], np.cumsum(vol)])
    c2 = np.concatenate([[0.0], np.cumsum(conns.astype(np.float64))])

    def win(c: np.ndarray, i: int, w: int) -> float:
        return c[i + 1] - c[max(0, i + 1 - w)]

    stores = [([], deque()) for _ in range(4)]  # (sorted values, arrival order) per feature
    for i in range(n):
        feats = (win(c1, i, W) / 60.0, win(c1, i, 5 * W) / 300.0, win(c2, i, W), win(c2, i, 5 * W) / 5.0)  # bytes/s 60 s, 300 s; connections/min 60 s, 300 s
        if i >= COLD:
            z_vol = -8.0
            for x, (srt, _) in zip(feats, stores):
                q = ((len(srt) - bisect_left(srt, x)) + 1) / (len(srt) + 1)
                z_vol = max(z_vol, max(-8.0, min(8.0, _ppf(1.0 - q))))
            out[i] = _combine(z_vol, float(z_unf[i]))
        for x, (srt, arr) in zip(feats, stores):
            insort(srt, x)
            arr.append(x)
            if len(arr) > STORE:
                old = arr.popleft()
                del srt[bisect_left(srt, old)]
    return out
