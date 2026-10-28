"""Export validated alert thresholds per availability pattern for the running server (manual §5.11, D107).

The shipped learned fusion (models/frozen_design.json) is kept exactly as it is. For each of the 15 non-empty
combinations of available signals, the missing columns are held at 0 and a threshold is calibrated on ORDINARY time of
VALIDATION seeds 16-19 so that false alert onsets equal the alert budget (3.0 per device per day). A pattern is marked
validated only if that budget was actually reached on enough rows; anything else is left unvalidated and the server takes
its conservative fallback for it. No test seed is touched.

  python -m ml.export_availability       # writes models/availability_thresholds.json
"""

import argparse
import json
import subprocess
from datetime import datetime, timezone
from pathlib import Path

import numpy as np

from backend.correlation.fusion import LearnedFusion
from backend.correlation.thresholds import BIT, describe

from .missing import BUDGET, Onsets, calibrate
from .validate import hold, load_combined

HALF_LIFE = 1.0
THR = [16, 17, 18, 19]
MIN_ORDINARY_ROWS = 500_000  # about 14 device-days of ordinary time per 100k rows... a pattern needs real coverage


def build(rows_dir: Path, design_path: Path) -> dict:
    design = json.loads(design_path.read_text())
    lf = LearnedFusion.from_dict(design["fusion"]["learned"])
    c = load_combined(rows_dir, THR)
    eff = hold(c, c.soft, HALF_LIFE)
    on = Onsets(c)
    n_ord = int(c.ordinary.sum())
    patterns: dict[str, dict] = {}
    for p in range(1, 16):
        mask = np.array([bool(p >> i & 1) for i in range(4)])
        s = lf.score_batch(np.where(mask, eff, 0.0))  # absent signals held at 0: calibrated for exactly this input
        thr = calibrate(on, s)
        rate = on.per_day(s, thr)
        ok = bool(rate <= BUDGET * 1.0001 and n_ord >= MIN_ORDINARY_ROWS and thr < 1.0)
        patterns[str(p)] = {
            "available": [m.value for m, b in BIT.items() if p >> b & 1], "describe": describe(p),
            "raise_at": round(float(thr), 6), "clear_at": round(float(max(0.0, thr - 0.08)), 6),
            "false_per_day": round(float(rate), 3), "rows": n_ord, "validated": ok,
        }
    try:
        head = subprocess.run(["git", "rev-parse", "HEAD"], capture_output=True, text=True, timeout=20).stdout.strip()
    except Exception:  # noqa: BLE001
        head = "unknown"
    return {
        "version": 1, "model": "learned fusion from models/frozen_design.json, unchanged",
        "budget_false_onsets_per_device_day": BUDGET, "half_life_s": HALF_LIFE, "score_type": "soft",
        "threshold_seeds": THR, "code_head": head, "created": datetime.now(timezone.utc).isoformat(),
        "note": "Calibrated on ordinary time of validation seeds 16-19 only. A missing signal's column is held at 0 and the "
                "threshold is calibrated for that exact input; patterns not listed as validated take the conservative fallback.",
        "patterns": patterns,
    }


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--rows", type=Path, default=Path("data/rows"))
    ap.add_argument("--design", type=Path, default=Path("models/frozen_design.json"))
    ap.add_argument("--out", type=Path, default=Path("models/availability_thresholds.json"))
    a = ap.parse_args()
    d = build(a.rows, a.design)
    a.out.write_text(json.dumps(d, indent=2))
    for k, v in d["patterns"].items():
        print(f"pattern {int(k):2d} {v['describe']:36s} raise {v['raise_at']:.4f}  false onsets/day {v['false_per_day']:.2f}  validated {v['validated']}")
    print("wrote", a.out)


if __name__ == "__main__":
    main()
