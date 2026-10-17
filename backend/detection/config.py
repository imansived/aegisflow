"""Every tunable number the detectors use, in one place.

Each value here is a hyperparameter. None is a law of nature, and all must be chosen on
validation data (never test data) before the final evaluation (manual §3.11, §5).
"""

from dataclasses import dataclass


@dataclass(frozen=True)
class DetectionConfig:
    # --- baselines -------------------------------------------------------------------
    # Per (device, time-of-day bucket, feature): no scoring until this many samples exist.
    # 720 ticks at 5 s = 1 hour. This is the cold-start rule (§3.3).
    min_samples: int = 720
    # Welford (exact running mean/variance) for the first `warm_n` samples, then an
    # exponentially weighted update with window `ew_window` so the baseline can adapt.
    warm_n: int = 3000
    ew_window: int = 8000
    # Poison resistance: once a baseline is ready, learn from an observation only if the
    # detector's score for it is below this. Otherwise an attack teaches "abnormal = normal".
    learn_gate: float = 0.5

    # --- z-score -> score ramp -------------------------------------------------------
    z_lo: float = 2.0  # at or below: score 0
    z_hi: float = 6.0  # at or above: score 1
    # Width of the "soft" score (stats.soft_ramp). Fixed in advance (manual §5.5); not tuned.
    soft_sigma: float = 3.0

    # --- spatial ---------------------------------------------------------------------
    rare_frac: float = 0.08  # a zone occupied less than this fraction of time is "rare"
    # A never-visited ALLOWED zone scores at most this. Deliberately below 0.5: unusual
    # location alone should not raise an alert (restricted-zone entry does); it is
    # contributing evidence for the correlation engine.
    rare_max: float = 0.4
    # Zone-occupancy needs far more data than a mean/variance: a device only visits a couple
    # of zones per hour. 3000 ticks = ~4 hours in that time-of-day bucket.
    occ_min_samples: int = 3000
    # Right after the working-hours boundary people are legitimately walking about, so the
    # rarity rule is paused (the restricted-zone rule never is).
    boundary_grace_s: float = 1800
    # Speed is a PHYSICAL plausibility rule, not a learned baseline: walking is ~1.4 m/s and
    # GPS jitter alone can look like ~3 m/s, so anything near 5+ m/s is implausible on foot.
    speed_lo_mps: float = 5.0
    speed_hi_mps: float = 15.0

    # --- network ---------------------------------------------------------------------
    endpoint_known_count: int = 3  # contacts needed before an endpoint is "known"
    endpoint_count_gap_s: float = 600  # ...and each counts at most once per 10 minutes
    endpoint_learn_s: float = 3600  # unfamiliar-rate baseline starts after this long
    unfamiliar_window_s: float = 60
    unfamiliar_min_std: float = 1.0  # benign novel endpoints do occur; one or two a minute are normal
    bytes_min_std: float = 0.3  # on log1p(bytes)
    conns_min_std: float = 1.0

    # --- device ----------------------------------------------------------------------
    # CPU/temperature are averaged over this window before scoring: a single-tick spike is
    # normal, a sustained rise is not. Without it the single-signal baseline would be a strawman.
    smooth_window_s: float = 60
    cpu_min_std: float = 4.0
    temp_min_std: float = 2.0
    drain_min_std: float = 1.5  # percentage points per hour
    drain_window_s: float = 300
    charging_threshold: float = -1.0  # drain below this = charging; feature skipped

    # --- proximity -------------------------------------------------------------------
    unenrolled_min_std: float = 0.5
    persist_min_std: float = 20.0  # seconds
