"""Independent check on REAL, time-ordered IoT traffic: Stratosphere IoT-23, run through AegisFlow's actual network
detector (manual §5.9).

What this can and cannot validate
---------------------------------
IoT-23 holds Zeek conn.log records for real IoT devices, some infected with real malware, with per-flow labels. It
supports exactly ONE of AegisFlow's four signals: network (bytes sent, bytes received, connection counts, remote
endpoints). It has no location, no device-health counters and no proximity data, so it cannot test fusion at all, nor
the spatial, device or proximity detectors. What it CAN test is the thing the simulator can never show: the real
`NetworkDetector` and the frozen soft-score calibration against real, bursty, heavy-tailed traffic that nobody
designed to match the detector's assumptions. Specifically: (1) how many false alert onsets per device-day the frozen
network threshold produces on benign real traffic, (2) whether malicious windows score higher than benign ones
(AUROC), and (3) whether it flags rare malicious activity after a long benign baseline.

Method: the dominant internal host of each capture is the one device. Flows are bucketed into 5-second telemetry
ticks (bytes sent = sum of orig_bytes, received = sum of resp_bytes, connections = flow count, endpoints = distinct
responder addresses; ticks with no flows are real zero ticks). The other telemetry fields are constants, so only the
network score is meaningful. The detector learns online and unsupervised, exactly as deployed; labels are used only
to score. The frozen network_only threshold from F1 (0.3271 on the soft score) is applied without change. A window
is malicious if any flow starting in it is labelled malicious. Working-hours buckets use the site's timezone, which
means nothing for these devices: another way this is not a like-for-like test.

  python -m ml.realdata_iot23
"""

import json
import sys
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
from sklearn.metrics import roc_auc_score

from backend.detection.engine import DetectionEngine
from contracts.telemetry import Telemetry

FROZEN_NETWORK_THR = 0.3271  # data/results/f1.log, network_only threshold at the 0.1% row false-positive budget
TICK = 5
CAPTURES = [
    ("CTU-Honeypot-Capture-4-1", "benign Philips HUE"),
    ("CTU-Honeypot-Capture-5-1", "benign Somfy door lock"),
    ("CTU-IoT-Malware-Capture-42-1", "infected, 6 malicious flows"),
    ("CTU-IoT-Malware-Capture-20-1", "infected, 16 malicious flows"),
    ("CTU-IoT-Malware-Capture-21-1", "infected, 14 malicious flows"),
    ("CTU-IoT-Malware-Capture-8-1", "infected, mostly malicious"),
    ("CTU-IoT-Malware-Capture-34-1", "infected Mirai, DDoS + C&C"),
    ("CTU-IoT-Malware-Capture-3-1", "infected, mostly malicious"),
    ("CTU-IoT-Malware-Capture-1-1", "infected, half malicious, 112 h"),
]


def parse(path: Path):
    flows = []
    with open(path, encoding="utf-8", errors="ignore") as fh:
        for line in fh:
            if line.startswith("#"):
                continue
            p = line.rstrip("\n").split("\t")
            if len(p) < 21:
                continue
            tail = p[20].split() if len(p) == 21 else p[20:]
            label = (tail[1] if len(tail) > 1 else p[-1]).lower()
            num = lambda x: 0 if x in ("-", "") else int(float(x))  # noqa: E731
            flows.append((float(p[0]), p[2], p[4], num(p[9]), num(p[10]), label != "benign"))
    return flows


def run_capture(path: Path) -> dict:
    flows = parse(path)
    host = max(set(f[1] for f in flows), key=lambda h: sum(1 for f in flows if f[1] == h))
    fl = [f for f in flows if f[1] == host]
    t0, t1 = min(f[0] for f in fl), max(f[0] for f in fl)
    n_ticks = int((t1 - t0) // TICK) + 1
    bs, br, nc = np.zeros(n_ticks, np.int64), np.zeros(n_ticks, np.int64), np.zeros(n_ticks, np.int64)
    mal = np.zeros(n_ticks, bool)
    ends: dict[int, set] = defaultdict(set)
    for ts, _h, resp, sent, recv, is_mal in fl:
        k = int((ts - t0) // TICK)
        bs[k] += sent; br[k] += recv; nc[k] += 1
        ends[k].add(resp)
        mal[k] |= is_mal
    eng = DetectionEngine(["D-001"])
    soft = np.zeros(n_ticks); hard = np.zeros(n_ticks); learning = np.zeros(n_ticks, bool)
    for k in range(n_ticks):
        t = Telemetry(
            device_id="D-001", timestamp=datetime.fromtimestamp(t0 + k * TICK, tz=timezone.utc), latitude=18.5206, longitude=73.8569,
            battery=80.0, cpu_usage=10.0, memory_usage=20.0, temperature=35.0, uptime_s=k * TICK,
            network_connections=int(min(nc[k], 100000)), bytes_sent=int(bs[k]), bytes_received=int(br[k]),
            remote_endpoints=sorted(ends[k])[:100], nearby_devices=[],
        )
        s = eng.process(t)
        r = s.results["network"]
        soft[k], hard[k], learning[k] = r.soft, r.score, r.learning
    active = nc > 0
    use = ~learning
    out = {
        "host": host, "hours": round((t1 - t0) / 3600, 1), "flows": len(fl), "windows": n_ticks, "active_windows": int(active.sum()),
        "malicious_windows": int(mal.sum()), "usable_fraction": float(use.mean()),
    }
    ben = use & ~mal
    out["benign_active_over_threshold"] = float((soft[ben & active] > FROZEN_NETWORK_THR).mean()) if (ben & active).any() else float("nan")
    hot = (soft > FROZEN_NETWORK_THR) & use
    prev = np.r_[False, hot[:-1]]
    onsets_benign = int((hot & ~prev & ~mal).sum())
    benign_days = ben.sum() * TICK / 86_400
    out["false_onsets_per_day"] = onsets_benign / benign_days if benign_days else float("nan")
    m = use & mal
    out["malicious_usable"] = int(m.sum())
    if m.any():
        out["malicious_flagged"] = float((soft[m] > FROZEN_NETWORK_THR).mean())
        sel = use & active
        if (sel & mal).any() and (sel & ~mal).any():
            out["auroc_active_windows"] = float(roc_auc_score(mal[sel], soft[sel]))
            out["auroc_hard_score"] = float(roc_auc_score(mal[sel], hard[sel]))
        # episode view: contiguous malicious runs (gap <= 60 s) counted once
        idx = np.flatnonzero(m)
        runs = np.split(idx, np.flatnonzero(np.diff(idx) > 12) + 1) if len(idx) else []
        out["malicious_episodes"] = len(runs)
        out["episodes_flagged"] = sum(bool(hot[r].any()) for r in runs)
    return out


def main() -> None:
    results = {}
    print(f"{'capture':32s} {'hours':>6s} {'active':>7s} {'mal win':>8s} {'use%':>5s} {'benign>thr':>10s} {'FP/day':>7s} {'mal flagged':>11s} {'AUROC':>6s} {'episodes':>9s}")
    only = set(sys.argv[2:])
    for name, desc in CAPTURES:
        if only and name not in only:
            continue
        p = Path("data/real/iot23") / f"{name}.conn.log.labeled"
        if not p.exists():
            continue
        r = run_capture(p)
        results[name] = {"description": desc, **r}
        print(f"{name:32s} {r['hours']:6.1f} {r['active_windows']:7d} {r['malicious_usable']:8d} {100*r['usable_fraction']:5.0f} "
              f"{r['benign_active_over_threshold']:10.3f} {r['false_onsets_per_day']:7.1f} "
              f"{r.get('malicious_flagged', float('nan')):11.2f} {r.get('auroc_active_windows', float('nan')):6.2f} "
              f"{str(r.get('episodes_flagged', '-')) + '/' + str(r.get('malicious_episodes', '-')):>9s}   {desc}", flush=True)
    Path("data/audit").mkdir(parents=True, exist_ok=True)
    Path(sys.argv[1] if len(sys.argv) > 1 else "data/audit/iot23.json").write_text(json.dumps(results, indent=1))


if __name__ == "__main__":
    main()
