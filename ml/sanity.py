"""DEVELOPMENT sanity check: do the detectors respond to the scenarios they should?

This is NOT the evaluation (that is Phase 5, on held-out seeds with a frozen design). It is a
quick look used while building. Only DEV seeds (1-9) may be used with it, so the held-out
seeds are never seen while tuning (manual §5).

  python -m ml.sanity --seed 1 --devices 20 --hours 30
"""

import argparse
from collections import defaultdict
from datetime import timedelta

from backend.detection.engine import DetectionEngine
from contracts.scenarios import Modality, ScenarioType
from simulator.config import SimConfig
from simulator.fleet import Fleet
from simulator.plan import build_plan

THRESH = 0.5
MODS = [Modality.SPATIAL, Modality.NETWORK, Modality.DEVICE, Modality.PROXIMITY]


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--seed", type=int, default=1)
    ap.add_argument("--devices", type=int, default=20)
    # Warm-up must contain at least one FULL working day, or zone-occupancy baselines are thin
    # (manual §4.3): Monday 00:00 + 33 h = Tuesday 09:00.
    ap.add_argument("--hours", type=float, default=72)
    ap.add_argument("--warmup-hours", type=float, default=33)
    ap.add_argument("--rate", type=float, default=4.0, help="episodes per device per day")
    a = ap.parse_args()
    if a.seed >= 10:
        raise SystemExit("seeds >= 10 are reserved for validation/test; use 1-9 here")

    cfg = SimConfig(seed=a.seed, n_devices=a.devices)
    dur, warm = timedelta(hours=a.hours), timedelta(hours=a.warmup_hours)
    plan = build_plan(cfg, dur, warm, a.rate)
    engine = DetectionEngine(cfg.device_ids())

    by_dev = defaultdict(list)
    for e in plan:
        by_dev[e.device_id].append(e)
    buf = timedelta(minutes=2)
    peak: dict[tuple[str, Modality], float] = defaultdict(float)  # (episode_id, modality) -> max score
    normal_ticks = defaultdict(int)
    normal_hot = defaultdict(int)
    prev_hot: dict[tuple[str, Modality], bool] = {}
    crossings = defaultdict(int)
    learning_after_warmup = 0
    t_warm = cfg.start + warm

    for t in Fleet(cfg, plan).run(dur):
        s = engine.process(t)
        in_ep = [e for e in by_dev[t.device_id] if e.start <= t.timestamp < e.end + buf]
        for e in in_ep:
            for m in MODS:
                k = (e.episode_id, m)
                peak[k] = max(peak[k], s.score(m))
        if in_ep or t.timestamp < t_warm:
            continue
        if s.learning:
            learning_after_warmup += 1
        for m in MODS:
            hot = s.score(m) >= THRESH
            normal_ticks[m] += 1
            normal_hot[m] += hot
            if hot and not prev_hot.get((t.device_id, m)):
                crossings[m] += 1
            prev_hot[(t.device_id, m)] = hot

    print(f"seed {a.seed}: {len(plan)} episodes, threshold {THRESH}\n")
    print("Fraction of episodes where each detector reached the threshold at any time:")
    header = f"{'scenario':<22}{'n':>4}  " + "".join(f"{m.value:>11}" for m in MODS)
    print(header)
    groups = defaultdict(list)
    for e in plan:
        key = e.scenario.value + (f"/{e.variant}" if e.variant else "")
        groups[key].append(e)
    for key, eps in sorted(groups.items()):
        row = "".join(f"{sum(peak[(e.episode_id, m)] >= THRESH for e in eps) / len(eps):>11.2f}" for m in MODS)
        print(f"{key:<22}{len(eps):>4}  {row}")

    days = (a.hours - a.warmup_hours) / 24
    print("\nNormal behaviour (post-warm-up, outside episodes):")
    for m in MODS:
        print(
            f"  {m.value:<10} ticks above threshold: {normal_hot[m] / max(1, normal_ticks[m]):.4%}   "
            f"alerts/device/day: {crossings[m] / a.devices / days:.2f}"
        )
    print(f"  ticks still 'learning' after warm-up: {learning_after_warmup}")


if __name__ == "__main__":
    main()
