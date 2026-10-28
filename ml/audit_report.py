"""Compact, ASCII-only summary of data/audit/audit.json (manual §5.8). Prints the tables quoted in the manual."""

import json
import sys
from pathlib import Path

R = json.loads(Path("data/audit/audit.json").read_text())
KEY = ["network_only", "max", "weighted_sum", "noisy_or", "learned", "early_iforest", "early_supervised"]
TITLE = {
    "f1_perseed": "F1 test seeds 100-109 (spent; per-seed re-analysis)", "control": "control: fresh seeds, original generator",
    "missing_spatial": "missing signal: spatial removed", "missing_network": "missing signal: network removed",
    "missing_device": "missing signal: device removed", "missing_proximity": "missing signal: proximity removed",
    "unseen": "unseen anomaly combinations", "benign": "6x benign noise, 4x benign look-alikes", "noisy": "noisy sensors (GPS 10 m, jittery readings)",
    "drift": "benign drift on half the devices after hour 20", "gaps": "lost telemetry (20% of ticks + 20 min/day blackout)",
}


def line(name, s):
    return (f"  {name:17s} recall_h {s['recall_headline']['mean']:.2f}+-{s['recall_headline']['sd']:.2f} [{s['recall_headline']['min']:.2f},{s['recall_headline']['max']:.2f}]"
            f"  recall_all {s['recall']['mean']:.2f}  precision {s['precision']['mean']:.2f}  F1 {s['f1']['mean']:.2f}  false alarms/device-day {s['fp_onsets_per_device_day']['mean']:.1f}"
            f"  benign look-alikes flagged {s['benign_flag_rate']['mean']:.2f}")


for key, title in TITLE.items():
    if key not in R:
        continue
    r = R[key]
    print(f"\n=== {title}  ({r['n_seeds']} seeds)")
    for mode in ("frozen", "recalibrated") if "--both" in sys.argv else ("frozen",):
        print(f" [{mode} thresholds]")
        for n in KEY:
            print(line(n, r["systems"][n][mode]))
        for k, v in r["paired"][mode].items():
            print(f"    {k:42s} {v['diff']:+.3f}  CI [{v['lo']:+.3f},{v['hi']:+.3f}]  {v['seeds_better']}/{v['n_seeds']} seeds")
    fams = sorted({k[7:-1] for k in r["systems"]["learned"]["frozen"] if k.startswith("recall[")})
    if fams:
        print("  recall by group (frozen):")
        for f in fams:
            print(f"    {f:28s} " + "  ".join(f"{n}={r['systems'][n]['frozen'].get(f'recall[{f}]', {}).get('mean', float('nan')):.2f}" for n in ("network_only", "max", "noisy_or", "learned", "early_iforest", "early_supervised")))
