"""Export the frozen design for the running server: learned-fusion coefficients and alert thresholds.

Everything here is fitted on VALIDATION seeds only (fit 10-15, thresholds 16-19), exactly as stage 1 of
the final evaluation (manual §5.7), so the served model never saw the test seeds.

  python -m ml.export_design            # writes models/frozen_design.json
"""

import argparse
import json
import subprocess
from datetime import datetime, timezone
from pathlib import Path

import numpy as np

from backend.correlation.fusion import LearnedFusion, NoisyOr

from .validate import FP_BUDGET, balanced_subsample, hold, load_combined

HALF_LIFE = 1.0
FIT, THR = list(range(10, 16)), list(range(16, 20))


def build(rows_dir: Path) -> dict:
    rng = np.random.default_rng(0)
    c = load_combined(rows_dir, FIT + THR)
    eff = hold(c, c.soft, HALF_LIFE)
    fit_idx = np.flatnonzero(np.isin(c.seed, FIT) & c.usable)
    sub = balanced_subsample(c, fit_idx, rng)
    lf = LearnedFusion().fit(eff[sub], c.label[sub])
    thr_idx = np.flatnonzero(np.isin(c.seed, THR) & c.ordinary)
    q = 1 - FP_BUDGET
    try:
        head = subprocess.run(["git", "rev-parse", "HEAD"], capture_output=True, text=True, timeout=20).stdout.strip()
    except Exception:  # noqa: BLE001
        head = "unknown"
    return {
        "half_life_s": HALF_LIFE,
        "score_type": "soft",
        "fusion": {"learned": lf.to_dict()},
        "thresholds": {
            "noisy_or": float(np.quantile(NoisyOr().score_batch(eff[thr_idx]), q)),
            "learned": float(np.quantile(lf.score_batch(eff[thr_idx]), q)),
        },
        "fp_budget_ordinary_ticks": FP_BUDGET,
        "fit_seeds": FIT,
        "threshold_seeds": THR,
        "code_head": head,
        "created": datetime.now(timezone.utc).isoformat(),
        "note": "Fitted on validation seeds only; the test seeds 100-109 were never used.",
    }


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--rows", type=Path, default=Path("data/rows"))
    ap.add_argument("--out", type=Path, default=Path("models/frozen_design.json"))
    a = ap.parse_args()
    d = build(a.rows)
    a.out.parent.mkdir(parents=True, exist_ok=True)
    a.out.write_text(json.dumps(d, indent=2))
    print(f"wrote {a.out}: thresholds {d['thresholds']}")


if __name__ == "__main__":
    main()
