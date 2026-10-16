"""python -m simulator.cli --devices 50 --hours 48 --seed 1 --out data/run1"""

import argparse
from datetime import timedelta
from pathlib import Path

from .config import SimConfig
from .fleet import Fleet
from .io import write_dataset
from .plan import build_plan


def main() -> None:
    ap = argparse.ArgumentParser(description="Generate a labelled AegisFlow dataset.")
    ap.add_argument("--devices", type=int, default=50)
    ap.add_argument("--hours", type=float, default=48)
    ap.add_argument("--warmup-hours", type=float, default=12, help="clean period before any episode")
    ap.add_argument("--seed", type=int, default=1)
    ap.add_argument("--episodes-per-device-day", type=float, default=2.0)
    ap.add_argument("--magnitude", type=float, nargs=2, metavar=("LO", "HI"), help="override difficulty range")
    ap.add_argument("--out", type=Path, required=True)
    a = ap.parse_args()

    cfg = SimConfig(seed=a.seed, n_devices=a.devices)
    duration = timedelta(hours=a.hours)
    plan = build_plan(
        cfg, duration, timedelta(hours=a.warmup_hours), a.episodes_per_device_day,
        magnitude=tuple(a.magnitude) if a.magnitude else None,
    )
    n = write_dataset(a.out, cfg, plan, Fleet(cfg, plan).run(duration))
    print(f"wrote {n} telemetry rows and {len(plan)} episodes to {a.out}")


if __name__ == "__main__":
    main()
