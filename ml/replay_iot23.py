"""R1: replay real IoT-23 device traces through the unchanged research-v1 system (protocol: docs/preregistration/R1-iot23-replay.md).

  python -m ml.replay_iot23 --group dev|held|all [--stress] [--out data/audit/r1_replay.json]

Only the network signal exists in this dataset. The other three are explicit nulls, so the real pipeline runs in its network-only availability
pattern and the dashboard state is Degraded throughout. Nothing here validates location, device-health or proximity detection or fusion.
"""

import argparse
import ipaddress
import json
import subprocess
from collections import defaultdict
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
from scipy import stats
from sklearn.metrics import roc_auc_score

from backend.api.runtime import build_snapshot
from backend.correlation.engine import FROZEN_CORRELATION
from backend.correlation.fusion import LearnedFusion
from backend.correlation.thresholds import ThresholdTable
from backend.services.alerts import AlertConfig
from backend.services.pipeline import Pipeline
from contracts.telemetry import SIGNAL_FIELDS, Telemetry

from .missing import bursts

TICK = 5
ROOT = Path("data/real/iot23")
DESIGN, TABLE = Path("models/frozen_design.json"), Path("models/availability_thresholds.json")
F1_THR = 0.9831
GROUPS = {
    "dev": ["CTU-Honeypot-Capture-4-1", "CTU-Honeypot-Capture-5-1", "CTU-IoT-Malware-Capture-42-1", "CTU-IoT-Malware-Capture-20-1",
            "CTU-IoT-Malware-Capture-21-1", "CTU-IoT-Malware-Capture-8-1", "CTU-IoT-Malware-Capture-34-1"],
    "held": ["CTU-IoT-Malware-Capture-3-1", "CTU-IoT-Malware-Capture-1-1", "CTU-IoT-Malware-Capture-9-1.head", "CTU-IoT-Malware-Capture-49-1.head"],
}


# ----------------------------------------------------------------------------------------- the adapter ----


def parse(path: Path) -> dict:
    """Zeek conn.log.labeled -> columns. A line cut short by a byte limit is dropped."""
    ts, orig, resp, sent, recv, mal = [], [], [], [], [], []
    with open(path, encoding="utf-8", errors="ignore") as fh:
        for line in fh:
            if line.startswith("#") or not line.endswith("\n"):
                continue
            p = line.rstrip("\n").split("\t")
            if len(p) < 21:
                continue
            tail = p[20].split() if len(p) == 21 else p[20:]
            label = (tail[1] if len(tail) > 1 else p[-1]).lower()
            num = lambda x: 0 if x in ("-", "") else int(float(x))  # noqa: E731
            ts.append(float(p[0])); orig.append(p[2]); resp.append(p[4]); sent.append(num(p[9])); recv.append(num(p[10])); mal.append(label != "benign")
    return {"ts": np.array(ts), "orig": np.array(orig), "resp": np.array(resp), "sent": np.array(sent, np.int64), "recv": np.array(recv, np.int64), "mal": np.array(mal)}


def is_private_v4(h: str) -> bool:
    try:
        a = ipaddress.ip_address(h)
        return a.version == 4 and a.is_private
    except ValueError:
        return False


def to_ticks(d: dict) -> dict:
    """The adapter. The busiest private host is the device; flows are bucketed into 5 s ticks by their start time."""
    hosts, counts = np.unique(d["orig"], return_counts=True)
    host = next(h for h in hosts[np.argsort(-counts)] if is_private_v4(h))
    m = d["orig"] == host
    ts, resp, sent, recv, mal = d["ts"][m], d["resp"][m], d["sent"][m], d["recv"][m], d["mal"][m]
    t0 = float(ts.min())
    k = ((ts - t0) // TICK).astype(np.int64)
    n = int(k.max()) + 1
    ends: dict[int, set] = defaultdict(set)
    for kk, r in zip(k, resp):
        ends[int(kk)].add(r)
    return {"host": host, "t0": t0, "n": n, "sent": np.bincount(k, sent, n).astype(np.int64), "recv": np.bincount(k, recv, n).astype(np.int64),
            "conns": np.bincount(k, minlength=n).astype(np.int64), "mal": np.bincount(k, mal.astype(np.int64), n) > 0, "ends": ends}


def telemetry(t: dict, i: int, null_network: bool) -> Telemetry:
    f = dict(device_id="D-001", timestamp=datetime.fromtimestamp(t["t0"] + i * TICK, tz=timezone.utc))
    for g, names in SIGNAL_FIELDS.items():
        for name in names:
            f[name] = None  # location, device health, proximity: not in the dataset -> explicit nulls
    if not null_network:
        f.update(network_connections=int(t["conns"][i]), bytes_sent=int(t["sent"][i]), bytes_received=int(t["recv"][i]),
                 remote_endpoints=sorted(t["ends"].get(i, ()))[:100])
    return Telemetry(**f)


# -------------------------------------------------------------------------------------------- replay -----


def replay(name: str, stress: bool = False) -> dict:
    path = ROOT / (name.replace(".head", "") + (".head" if name.endswith(".head") else "") + ".conn.log.labeled")
    t = to_ticks(parse(path))
    n = t["n"]
    design = json.loads(DESIGN.read_text())
    lf = LearnedFusion.from_dict(design["fusion"]["learned"])
    thr = design["thresholds"]["learned"]
    alert_cfg = AlertConfig(raise_at=thr, clear_at=max(0.0, thr - 0.08), clear_after_s=60.0)
    corr = replace(FROZEN_CORRELATION, low=0.5 * alert_cfg.clear_at, medium=alert_cfg.clear_at, high=alert_cfg.raise_at)
    table = ThresholdTable.load(TABLE)

    null = np.zeros(n, bool)
    if stress:
        s0 = 23_760 if n > 2 * 23_760 else int(0.25 * n)
        null[s0:] = bursts(n - s0, 0.20, 12.0, np.random.default_rng(np.random.SeedSequence([7, n])))

    events, st = [], {"scores": None, "fused": None, "violations": {"alert_while_not_alertable": 0, "alert_while_blind_or_recovering": 0}}

    def on_alert(ev: dict) -> None:
        events.append({**ev, "_n_ticks_in": len(rows["score"])})
        f = st["fused"]
        if ev["event"] in ("opened", "escalated") and not f.alertable:
            st["violations"]["alert_while_not_alertable"] += 1
        if ev["event"] in ("opened", "escalated") and ("network" in {m.value for m in f.unavailable}):
            st["violations"]["alert_while_blind_or_recovering"] += 1

    pipe = Pipeline(["D-001"], lf, None, corr_cfg=corr, alert_cfg=alert_cfg, table=table, on_alert=on_alert)
    pipe.detection.sink = lambda s: st.update(scores=s)
    pipe.on_fused = lambda f: st.update(fused=f)
    rows = {k: [] for k in ("score", "soft", "learning", "hot", "pat", "obs", "alertable", "state", "recovering", "blind", "thr")}
    prev = None
    for i in range(n):
        tel = telemetry(t, i, bool(null[i]))
        pipe.process(tel)
        f, s = st["fused"], st["scores"]
        snap = build_snapshot(tel, f, prev)
        prev = snap
        r = s.results[next(m for m in s.results if m.value == "network")]
        rows["score"].append(f.score); rows["soft"].append(r.soft); rows["learning"].append(s.learning)
        rows["hot"].append(bool(f.alertable and f.operating is not None and f.score >= f.operating.raise_at))
        rows["pat"].append(f.pattern); rows["obs"].append(f.observability); rows["alertable"].append(f.alertable)
        rows["state"].append(snap["state"]); rows["recovering"].append("network" in snap["recovering"])
        rows["blind"].append(bool(null[i])); rows["thr"].append(f.operating.raise_at if f.operating else np.nan)
    R = {k: np.array(v) for k, v in rows.items()}
    return summarise(name, stress, t, R, events, st["violations"], null)


def poisson_ci(k: int, exposure: float) -> tuple[float, float]:
    if exposure <= 0:
        return float("nan"), float("nan")
    lo = stats.chi2.ppf(0.025, 2 * k) / 2 if k else 0.0
    hi = stats.chi2.ppf(0.975, 2 * (k + 1)) / 2
    return lo / exposure, hi / exposure


def onsets(hot: np.ndarray, sel: np.ndarray) -> int:
    prev = np.concatenate([[False], hot[:-1]])
    return int((hot & ~prev & sel).sum())


def summarise(name: str, stress: bool, t: dict, R: dict, events: list, viol: dict, null: np.ndarray) -> dict:
    n, mal = t["n"], t["mal"]
    judged = ~R["learning"] & R["alertable"] & ~null & ~R["recovering"]  # ticks that were actually judged by the network detector
    usable = ~R["learning"]
    ben = judged & ~mal
    days = ben.sum() * TICK / 86_400
    o_ship = onsets(R["hot"], ben)
    hot_f1 = (R["score"] > F1_THR) & judged
    o_f1 = onsets(hot_f1, ben)
    lo, hi = poisson_ci(o_ship, days)
    out = {"capture": name, "condition": "B (intermittent network loss)" if stress else "A (steady)", "host": t["host"], "ticks": n, "hours": round(n * TICK / 3600, 1),
           "active_ticks": int((t["conns"] > 0).sum()), "malicious_ticks": int(mal.sum()), "judged_benign_device_days": round(float(days), 3)}
    out["false_onsets_per_device_day_shipped"] = {"rate": o_ship / days if days else float("nan"), "count": o_ship, "ci95": [lo, hi]}
    out["false_onsets_per_device_day_f1_threshold"] = o_f1 / days if days else float("nan")
    # soft score quantiles on benign judged ticks
    sb = R["soft"][ben]
    out["soft_score_benign"] = {"p50": float(np.quantile(sb, .5)) if len(sb) else None, "p99": float(np.quantile(sb, .99)) if len(sb) else None,
                                "frac_gt_0.3": float((sb > 0.3).mean()) if len(sb) else None}
    # ranking and recall (only where malicious judged ticks exist)
    mj = judged & mal
    out["malicious_judged_ticks"] = int(mj.sum())
    sel = judged & (t["conns"] > 0)
    if mj.sum() >= 50 and (sel & ~mal).any():
        out["auroc_active_ticks"] = float(roc_auc_score(mal[sel], R["soft"][sel]))
    idx = np.flatnonzero(mj)
    runs = np.split(idx, np.flatnonzero(np.diff(idx) > 12) + 1) if len(idx) else []
    out["malicious_episodes"] = len(runs)
    out["episodes_flagged"] = int(sum(bool(R["hot"][r].any()) for r in runs))
    # alert manager view
    t_mal = np.flatnonzero(mal) * TICK + t["t0"]
    opened = [e for e in events if e["event"] == "opened"]
    def overlaps(e):
        a = datetime.fromisoformat(e["started"]).timestamp() - 60
        b = (datetime.fromisoformat(e["ended"]).timestamp() if e["ended"] else a + 600) + 60
        return bool(((t_mal >= a) & (t_mal <= b)).any())
    tp = [e for e in opened if overlaps(e)]
    dd = (usable & ~null).sum() * TICK / 86_400
    out["alerts_opened"] = {"total": len(opened), "overlapping_malicious": len(tp), "false": len(opened) - len(tp),
                            "false_per_device_day": (len(opened) - len(tp)) / dd if dd else float("nan")}
    # states: never Normal, never fallback while the network detector is judging
    st = {k: int((R["state"] == k).sum()) for k in ("normal", "degraded", "anomalous")}
    out["states"] = {**st, "ticks_blind": int((R["obs"] == "blind").sum()), "ticks_recovering_while_signal_present": int((R["recovering"] & ~null).sum()),
                     "fallback_ticks_while_network_present": int((~R["alertable"] & ~null & ~R["recovering"]).sum()),
                     "pattern_values": {int(p): int((R["pat"] == p).sum()) for p in np.unique(R["pat"])}}
    out["invariants"] = {"never_normal": st["normal"] == 0, **viol, "judged_by_validated_network_only_threshold": bool(np.allclose(np.unique(R["thr"][judged]), np.unique(R["thr"][judged])[:1]) if judged.any() else None)}
    if stress:  # recovery: ticks from the signal coming back until it is judged again, and the false-alarm rate once it is
        back = np.flatnonzero(null[:-1] & ~null[1:]) + 1
        clean = [b for b in back if b + 40 <= n and not null[b:b + 14].any()]  # the signal stays present for the whole refill
        lag = [int(np.argmax(~R["recovering"][b:b + 40])) for b in clean]
        out["recovery_lag_ticks"] = {"mean": float(np.mean(lag)) if lag else None, "min": min(lag) if lag else None, "max": max(lag) if lag else None,
                                     "expected_ticks": 11, "n": len(lag), "n_interrupted_by_a_new_outage": len(back) - len(clean)}
    # explainability
    ex = {"alerts": len(opened), "names_unavailable_signals": sum(bool(e.get("unavailable")) for e in opened),
          "explanation_text_mentions_partial_observation": sum(any(w in e["explanation"].lower() for w in ("partly", "unavailable", "not observed")) for e in opened),
          "reasons_named": sum(any(f["reasons"] for f in e["factors"]) for e in opened),
          "network_features_per_alert": {"1": 0, "2": 0, "3+": 0}}
    for e in opened:
        k = sum(1 for r in (e["factors"][0]["reasons"] if e["factors"] else []))
        ex["network_features_per_alert"]["1" if k <= 1 else "2" if k == 2 else "3+"] += 1
    def brief(e):
        return {"started": e["started"], "severity": e["severity"], "peak": e["peak_score"], "unavailable": e.get("unavailable"), "explanation": e["explanation"],
                "reasons": e["factors"][0]["reasons"] if e["factors"] else []}
    ex["examples_true"] = [brief(e) for e in tp[:10]]
    ex["examples_false"] = [brief(e) for e in opened if e not in tp][:10]
    out["explainability"] = ex
    out["_features"] = {"sent": t["sent"][ben].tolist()[:200000], "recv": t["recv"][ben].tolist()[:200000], "conns": t["conns"][ben].tolist()[:200000]} if not stress else None
    return out


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--group", choices=["dev", "held", "all"], default="all")
    ap.add_argument("--stress", action="store_true")
    ap.add_argument("--only", nargs="*")
    ap.add_argument("--out", type=Path, default=Path("data/audit/r1_replay.json"))
    a = ap.parse_args()
    names = [n for g in (["dev", "held"] if a.group == "all" else [a.group]) for n in GROUPS[g]]
    if a.only:
        names = [n for n in names if n in a.only]
    res = []
    print(f"{'capture':34s} {'cond':>5s} {'hours':>6s} {'benign dd':>9s} {'FA/dd ship':>10s} {'[95% CI]':>16s} {'FA/dd F1thr':>11s} {'alerts FA/dd':>12s} {'AUROC':>6s} {'ep':>7s}  invariants")
    for nme in names:
        if not (ROOT / (nme.replace(".head", "") + (".head" if nme.endswith(".head") else "") + ".conn.log.labeled")).exists():
            print("missing", nme); continue
        r = replay(nme, a.stress)
        res.append(r)
        s = r["false_onsets_per_device_day_shipped"]
        print(f"{nme:34s} {'B' if a.stress else 'A':>5s} {r['hours']:6.1f} {r['judged_benign_device_days']:9.2f} {s['rate']:10.1f} [{s['ci95'][0]:5.1f},{s['ci95'][1]:6.1f}] {r['false_onsets_per_device_day_f1_threshold']:11.1f} "
              f"{r['alerts_opened']['false_per_device_day']:12.1f} {r.get('auroc_active_ticks', float('nan')):6.2f} {r['episodes_flagged']}/{r['malicious_episodes']:<3d}  {r['invariants']}", flush=True)
    meta = {"code_commit": subprocess.run(["git", "rev-parse", "--short", "HEAD"], capture_output=True, text=True).stdout.strip(), "prereg": "e6c9658", "stress": a.stress}
    a.out.parent.mkdir(parents=True, exist_ok=True)
    a.out.write_text(json.dumps({"meta": meta, "captures": res}, indent=1, default=float))


if __name__ == "__main__":
    main()
