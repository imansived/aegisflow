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
    ScenarioType.HARD_NEGATIVE: 1.5,
}

MIN_GAP_S = 300  # quiet time between two episodes on one device


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
            modalities, variant = spec.modalities, ""
            if stype is ScenarioType.HARD_NEGATIVE:
                if rng.random() < 0.5:
                    variant = "firmware_update"
                else:
                    variant, modalities, mag = "authorized_visit", frozenset({Modality.SPATIAL}), 1.0
            drafts.append((start, device_id, stype, length, modalities, mag, spec.is_malicious, variant))

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
        )
        for i, (start, dev, stype, length, modalities, mag, malicious, variant) in enumerate(drafts, 1)
    ]
