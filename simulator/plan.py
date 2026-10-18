"""Decide WHEN anomalies happen: the ground-truth plan (manual §3.14)."""

from datetime import timedelta

from contracts.scenarios import SCENARIOS, Episode, Modality, ScenarioType

from .config import TAG_PLAN, SimConfig, child_rng

DEFAULT_WEIGHTS: dict[ScenarioType, float] = {
    ScenarioType.SPATIAL: 1.0,
    ScenarioType.NETWORK: 1.0,
    ScenarioType.HARDWARE: 1.0,
    ScenarioType.PROXIMITY: 1.0,
    ScenarioType.COMBINED: 1.5,
    ScenarioType.WEAK_CORRELATED: 1.5,
    ScenarioType.STAGED: 2.0,
    ScenarioType.HARD_NEGATIVE: 1.5,
}

MIN_GAP_S = 300  # quiet time between two episodes on one device


def staged_shape(rng) -> tuple[str, float, dict[str, tuple[float, float]]]:
    """Timing of one STAGED episode: (variant, length in s, per-modality active windows).

    cascade: spatial from 0 s, network 10-40 s later, device 10-50 s after that; all persist to
             the end (180-420 s total). Closest to the spec's demo.
    pulses:  each of spatial -> network -> device is active only in its own 40-90 s window,
             separated by 0-30 s gaps, so no two deviations ever overlap. Only memory can link
             them: an existence proof, not a realistic claim (manual §5.6).
    """
    if rng.random() < 0.5:
        net = float(rng.uniform(10, 40))
        dev = net + float(rng.uniform(10, 50))
        length = float(rng.integers(max(180, int(dev) + 90), 421))
        return "cascade", length, {"spatial": (0.0, length), "network": (net, length), "device": (dev, length)}
    d = [float(rng.uniform(40, 90)) for _ in range(3)]
    g = [float(rng.uniform(0, 30)) for _ in range(2)]
    e0 = d[0]
    s1 = e0 + g[0]
    e1 = s1 + d[1]
    s2 = e1 + g[1]
    e2 = s2 + d[2]
    return "pulses", e2, {"spatial": (0.0, e0), "network": (s1, e1), "device": (s2, e2)}


def build_plan(
    cfg: SimConfig,
    duration: timedelta,
    warmup: timedelta,
    episodes_per_device_per_day: float = 2.0,
    weights: dict[ScenarioType, float] | None = None,
    magnitude: tuple[float, float] | None = None,
) -> list[Episode]:
    """Sample non-overlapping episodes per device, none during the warm-up.

    `magnitude` overrides every scenario's range: this is the difficulty sweep (§3.13).
    The warm-up is left clean so detectors have a baseline to learn before anything happens.
    """
    rng = child_rng(cfg.seed, TAG_PLAN)
    weights = weights or DEFAULT_WEIGHTS
    types = list(weights)
    probs = [weights[t] for t in types]
    total = sum(probs)
    probs = [p / total for p in probs]

    days = (duration - warmup).total_seconds() / 86_400
    drafts: list[tuple] = []
    for device_id in cfg.device_ids():
        taken: list[tuple[float, float]] = []
        for _ in range(int(rng.poisson(episodes_per_device_per_day * days))):
            stype = types[int(rng.choice(len(types), p=probs))]
            spec = SCENARIOS[stype]
            variant, windows = "", {}
            if stype is ScenarioType.STAGED:
                variant, length, windows = staged_shape(rng)
            else:
                lo, hi = spec.duration_s_range
                length = float(rng.integers(lo, hi + 1))
            for _attempt in range(20):
                start = float(rng.uniform(warmup.total_seconds(), duration.total_seconds() - length))
                if all(start >= e + MIN_GAP_S or start + length + MIN_GAP_S <= s for s, e in taken):
                    break
            else:
                continue  # could not place it without overlap; skip rather than force it
            taken.append((start, start + length))

            mlo, mhi = magnitude or spec.magnitude_range
            mag = float(rng.uniform(mlo, mhi))
            modalities = spec.modalities
            if stype is ScenarioType.HARD_NEGATIVE:
                if rng.random() < 0.5:
                    variant = "firmware_update"
                else:
                    variant, modalities, mag = "authorized_visit", frozenset({Modality.SPATIAL}), 1.0
            drafts.append((start, device_id, stype, length, modalities, mag, spec.is_malicious, variant, windows))

    drafts.sort(key=lambda d: (d[0], d[1]))
    return [
        Episode(
            episode_id=f"E-{i:04d}",
            device_id=dev,
            scenario=stype,
            start=cfg.start + timedelta(seconds=start),
            end=cfg.start + timedelta(seconds=start + length),
            modalities=modalities,
            magnitude=round(mag, 3),
            is_malicious=malicious,
            seed=cfg.seed,
            variant=variant,
            windows_s=windows,
        )
        for i, (start, dev, stype, length, modalities, mag, malicious, variant, windows) in enumerate(drafts, 1)
    ]
