"""R2 evaluation driver (protocol: docs/preregistration/R2-sparse-network-detector.md).

  python -m ml.r2_eval dev          # debugging on the DEVELOPMENT captures only: no thresholds, no claims
  python -m ml.r2_eval validate     # calibrate every arm's threshold on UNSW 16-10-12, choose the candidate, write data/audit/r2_validation.json
  python -m ml.r2_eval test         # THE test run; refuses to run twice (guard file); reads thresholds and the choice from the committed validation result
"""

import json
import sys
from dataclasses import replace
from pathlib import Path

import numpy as np
from sklearn.metrics import roc_auc_score

from backend.correlation.engine import FROZEN_CORRELATION
from backend.correlation.fusion import LearnedFusion
from backend.correlation.thresholds import ThresholdTable
from backend.services.alerts import AlertConfig
from backend.services.pipeline import Pipeline

from .r2_adapters import device_traces, iot23_trace, read_csv_zip, read_pcap_targz
from .r2_detectors import score_windowed_rate, score_zero_inflated
from .replay_iot23 import telemetry

TICK = 5
BURN = 1440  # 2 h
BUDGET = 3.0
GRID = np.round(np.arange(0.0, 1.0001, 0.005), 3)
CACHE = Path("data/r2/cache")
GUARD = Path("data/results/R2_TEST_USED")
ROOT = Path("data/real/iot23")
DEV = ["CTU-Honeypot-Capture-4-1", "CTU-Honeypot-Capture-5-1", "CTU-IoT-Malware-Capture-42-1", "CTU-IoT-Malware-Capture-20-1", "CTU-IoT-Malware-Capture-21-1",
       "CTU-IoT-Malware-Capture-8-1", "CTU-IoT-Malware-Capture-34-1"]
VAL_ATTACK = ["CTU-IoT-Malware-Capture-3-1", "CTU-IoT-Malware-Capture-1-1", "CTU-IoT-Malware-Capture-9-1.head", "CTU-IoT-Malware-Capture-49-1.head",
              "CTU-IoT-Malware-Capture-8-1", "CTU-IoT-Malware-Capture-20-1", "CTU-IoT-Malware-Capture-21-1", "CTU-IoT-Malware-Capture-34-1"]
ARMS = ["R1-recal", "A", "B"]


# ---------------------------------------------------------------------------------- per-trace scoring ----


def run_v1(t: dict) -> dict:
    """The research-v1 network detector through the real pipeline: its soft score, its unfamiliar-endpoint z, its learning flag, the shipped-threshold hot flag."""
    design = json.loads(Path("models/frozen_design.json").read_text())
    lf = LearnedFusion.from_dict(design["fusion"]["learned"])
    thr = design["thresholds"]["learned"]
    cfg = AlertConfig(raise_at=thr, clear_at=max(0.0, thr - 0.08), clear_after_s=60.0)
    corr = replace(FROZEN_CORRELATION, low=0.5 * cfg.clear_at, medium=cfg.clear_at, high=cfg.raise_at)
    st = {"s": None, "f": None}
    pipe = Pipeline(["D-001"], lf, None, corr_cfg=corr, alert_cfg=cfg, table=ThresholdTable.load(Path("models/availability_thresholds.json")))
    pipe.detection.sink = lambda s: st.update(s=s)
    pipe.on_fused = lambda f: st.update(f=f)
    n = t["n"]
    soft, zunf, learn, hot = np.zeros(n), np.full(n, np.nan), np.zeros(n, bool), np.zeros(n, bool)
    for i in range(n):
        pipe.process(telemetry(t, i, False))
        s, f = st["s"], st["f"]
        r = next(v for m, v in s.results.items() if m.value == "network")
        soft[i], learn[i] = r.soft, s.learning
        if "unfamiliar_1m" in r.evidence:
            zunf[i] = r.evidence["unfamiliar_1m"]
        hot[i] = bool(f.alertable and f.operating is not None and f.score >= f.operating.raise_at)
    return {"soft_v1": soft, "z_unf": zunf, "learning": learn, "hot_shipped": hot}


def arrays(t: dict) -> dict:
    f = CACHE / (t["id"].replace("/", "__") + ".npz")
    if f.exists():
        z = np.load(f, allow_pickle=False)
        return {k: z[k] for k in z.files}
    v1 = run_v1(t)
    z_unf = np.nan_to_num(v1["z_unf"], nan=0.0)
    a = {**v1, "soft_A": score_zero_inflated(t["sent"], t["recv"], t["conns"], z_unf), "soft_B": score_windowed_rate(t["sent"], t["recv"], t["conns"], z_unf),
         "mal": t["mal"].astype(bool) if t["mal"] is not None else np.zeros(t["n"], bool), "active": t["conns"] > 0}
    CACHE.mkdir(parents=True, exist_ok=True)
    np.savez_compressed(f, **a)
    return a


def arm_score(a: dict, arm: str) -> np.ndarray:
    if arm == "R1-recal":
        return np.where(a["learning"], 0.0, a["soft_v1"])
    return a["soft_A"] if arm == "A" else a["soft_B"]


# --------------------------------------------------------------------------------------------- metrics ----


def rising(hot: np.ndarray) -> np.ndarray:
    prev = np.concatenate([[False], hot[:-1]])
    return hot & ~prev


def onsets(score: np.ndarray, thr: float) -> int:
    hot = score >= thr
    hot[:BURN] = False
    return int(rising(hot).sum())


def alerts(score: np.ndarray, thr: float) -> list[tuple[int, int]]:
    """An alert opens at an onset and closes once the score has stayed below (thr - 0.08) for 12 ticks (60 s)."""
    out, start, below = [], None, 0
    for i in range(BURN, len(score)):
        if start is None:
            if score[i] >= thr:
                start, below = i, 0
        else:
            below = below + 1 if score[i] < thr - 0.08 else 0
            if below >= 12:
                out.append((start, i - 11))
                start = None
    if start is not None:
        out.append((start, len(score)))
    return out


def benign_exposure(n: int) -> float:
    return max(0, n - BURN) * TICK / 86_400


def calibrate(arm_scores: list[np.ndarray]) -> float:
    exposure = sum(benign_exposure(len(s)) for s in arm_scores)
    for thr in GRID[1:]:
        if sum(onsets(s, thr) for s in arm_scores) / exposure <= BUDGET:
            return float(thr)
    return 1.0


def cluster_bootstrap(counts: np.ndarray, exposure: np.ndarray, B: int = 10_000, seed: int = 0) -> tuple[float, float, float]:
    rng = np.random.default_rng(seed)
    idx = rng.integers(0, len(counts), (B, len(counts)))
    r = counts[idx].sum(1) / exposure[idx].sum(1)
    return float(counts.sum() / exposure.sum()), float(np.percentile(r, 2.5)), float(np.percentile(r, 97.5))


def false_alarm_report(traces_arrays: list[tuple[str, dict]], arm: str, thr: float) -> dict:
    sc = [arm_score(a, arm) for _, a in traces_arrays]
    counts = np.array([onsets(s, thr) for s in sc], float)
    expo = np.array([benign_exposure(len(s)) for s in sc])
    rate, lo, hi = cluster_bootstrap(counts, expo)
    al = [alerts(s, thr) for s in sc]
    durs = [(e - s_) * TICK for a_ in al for s_, e in a_]
    return {"arm": arm, "threshold": thr, "benign_device_days": float(expo.sum()), "onsets": int(counts.sum()), "rate": rate, "ci95": [lo, hi],
            "alerts_per_device_day": sum(len(a_) for a_ in al) / float(expo.sum()), "alert_duration_s": {"mean": float(np.mean(durs)) if durs else None, "median": float(np.median(durs)) if durs else None},
            "per_device_day_rate": {i: float(c / e) for (i, _), c, e in zip(traces_arrays, counts, expo)}}


def detection_report(traces_arrays: list[tuple[str, dict]], arm: str, thr: float) -> dict:
    rows, delays, det, tot, aur = [], [], 0, 0, []
    for name, a in traces_arrays:
        sc = arm_score(a, arm)
        mal = a["mal"]
        idx = np.flatnonzero(mal)
        idx = idx[idx >= BURN]
        runs = np.split(idx, np.flatnonzero(np.diff(idx) > 12) + 1) if len(idx) else []
        d_here = 0
        for r in runs:
            hits = np.flatnonzero(sc[max(BURN, r[0] - 12):r[-1] + 1] >= thr)
            tot += 1
            if len(hits):
                det += 1
                d_here += 1
                delays.append(max(0.0, (max(BURN, r[0] - 12) + hits[0] - r[0]) * TICK))
        sel = np.zeros(len(sc), bool)
        sel[BURN:] = a["active"][BURN:]
        auc = float(roc_auc_score(mal[sel], sc[sel])) if mal[sel].any() and (~mal[sel]).any() and mal[sel].sum() >= 20 else None
        if auc is not None:
            aur.append(auc)
        rows.append({"capture": name, "episodes": len(runs), "detected": d_here, "auroc": auc})
    return {"arm": arm, "threshold": thr, "episodes": tot, "detected": det, "recall": det / tot if tot else None,
            "median_delay_s": float(np.median(delays)) if delays else None, "auroc_mean_over_captures": float(np.mean(aur)) if aur else None, "per_capture": rows}


# ----------------------------------------------------------------------------------------------- stages ----


def unsw_traces(day: str) -> list[dict]:
    csv = Path(f"data/real/unsw/{day}.csv.zip")
    if csv.exists():
        pk = read_csv_zip(csv)
    else:
        pk = read_pcap_targz(Path(f"data/real/unsw/{day}.tar.gz"))
    return device_traces(pk, day)


def iot_traces(names: list[str]) -> list[dict]:
    out = []
    for n in names:
        p = ROOT / (n.replace(".head", "") + (".head" if n.endswith(".head") else "") + ".conn.log.labeled")
        if p.exists():
            out.append(iot23_trace(p))
    return out


def stage_dev() -> None:
    print("DEVELOPMENT captures only: behaviour and debugging, no thresholds, no claims")
    for t in iot_traces(DEV):
        a = arrays(t)
        ben = ~a["mal"]
        ex = benign_exposure(t["n"])
        print(f"{t['id']:34s} ticks {t['n']:6d}  v1 shipped onsets {int(rising(a['hot_shipped']).sum()):4d}  max score A {a['soft_A'].max():.2f} B {a['soft_B'].max():.2f}  frac>=0.5 on benign:"
              f" v1 {np.mean(a['soft_v1'][ben][BURN:] >= 0.5):.4f} A {np.mean(a['soft_A'][ben][BURN:] >= 0.5):.4f} B {np.mean(a['soft_B'][ben][BURN:] >= 0.5):.4f}")


def stage_validate() -> None:
    print("calibrating on UNSW 16-10-12 (validation benign) ...", flush=True)
    ben = [(t["id"], arrays(t)) for t in unsw_traces("16-10-12")]
    atk = [(t["id"], arrays(t)) for t in iot_traces(VAL_ATTACK)]
    out = {"split": "validation", "benign_traces": len(ben), "arms": {}}
    fa_shipped = float(sum(rising(a["hot_shipped"][BURN:]).sum() for _, a in ben) / sum(benign_exposure(len(a["hot_shipped"])) for _, a in ben))
    out["R1-shipped_false_onsets_per_device_day_on_validation_benign"] = fa_shipped
    for arm in ARMS:
        thr = calibrate([arm_score(a, arm) for _, a in ben])
        out["arms"][arm] = {"false_alarms": false_alarm_report(ben, arm, thr), "detection": detection_report(atk, arm, thr)}
        d, f = out["arms"][arm]["detection"], out["arms"][arm]["false_alarms"]
        print(f"{arm:9s} thr {thr:.3f}  validation benign {f['rate']:.2f}/device-day [{f['ci95'][0]:.2f},{f['ci95'][1]:.2f}]  detection: recall {d['detected']}/{d['episodes']}  median delay {d['median_delay_s']}  AUROC {d['auroc_mean_over_captures']}")
    A, B = out["arms"]["A"]["detection"], out["arms"]["B"]["detection"]
    ra, rb = (A["recall"] or 0.0), (B["recall"] or 0.0)
    if abs(ra - rb) > 0.03:
        pick = "A" if ra > rb else "B"
    elif A["median_delay_s"] is not None and B["median_delay_s"] is not None and A["median_delay_s"] != B["median_delay_s"]:
        pick = "A" if A["median_delay_s"] < B["median_delay_s"] else "B"
    else:
        pick = "B"
    out["chosen_candidate"] = pick
    print("chosen candidate (validation only):", pick)
    Path("data/audit").mkdir(parents=True, exist_ok=True)
    Path("data/audit/r2_validation.json").write_text(json.dumps(out, indent=1, default=float))



TEST_IOT = ["7-1", "17-1", "33-1", "35-1", "36-1", "39-1", "44-1", "52-1", "60-1"]
TEST_DAYS = ["16-10-13", "16-10-14"]


def cut_head(raw: Path) -> Path:
    """First 100 MB of a conn.log.labeled, cut at a line boundary (protocol section 3)."""
    out = raw.with_name(raw.stem + ".head.conn.log.labeled")
    data = raw.read_bytes()
    out.write_bytes(data[:data.rfind(bytes([10])) + 1])
    return out


def eligible(t: dict) -> tuple[bool, str]:
    """Decided from labels alone, before any detector runs: >= 60 min (720 ticks) before the first malicious tick and >= 20 malicious ticks from there on."""
    mal = np.flatnonzero(t["mal"].astype(bool))
    if not len(mal):
        return False, "no malicious ticks"
    if mal[0] < 720:
        return False, f"first malicious tick at {mal[0]} (< 720)"
    if len(mal) < 20:
        return False, f"only {len(mal)} malicious ticks"
    return True, "eligible"


def paired_difference(a_sc: list[np.ndarray], b_sc: list[np.ndarray], thr_a: float, thr_b: float, B: int = 10_000) -> dict:
    ca = np.array([onsets(s, thr_a) for s in a_sc], float)
    cb = np.array([onsets(s, thr_b) for s in b_sc], float)
    ex = np.array([benign_exposure(len(s)) for s in a_sc])
    rng = np.random.default_rng(1)
    idx = rng.integers(0, len(ca), (B, len(ca)))
    d = (ca[idx].sum(1) - cb[idx].sum(1)) / ex[idx].sum(1)
    return {"point": float((ca.sum() - cb.sum()) / ex.sum()), "ci95": [float(np.percentile(d, 2.5)), float(np.percentile(d, 97.5))]}


def stage_test() -> None:
    if GUARD.exists():
        sys.exit("R2 test data already used (data/results/R2_TEST_USED): the test is run once")
    val = json.loads(Path("docs/results/r2_validation.json").read_text())
    thr = {a: val["arms"][a]["false_alarms"]["threshold"] for a in ARMS}
    chosen = val["chosen_candidate"]
    GUARD.parent.mkdir(parents=True, exist_ok=True)
    GUARD.write_text("R2 test run started; thresholds and the chosen candidate from docs/results/r2_validation.json")
    out = {"split": "test", "thresholds": thr, "chosen_candidate": chosen, "eligibility": {}, "arms": {}}

    ben = []
    for day in TEST_DAYS:
        print("benign day", day, flush=True)
        for t in device_traces(read_pcap_targz(Path(f"data/real/unsw/{day}.tar.gz")), day):
            ben.append((t["id"], arrays(t)))
    atk = []
    for n in TEST_IOT:
        raw = Path(f"data/real/iot23_test/CTU-IoT-Malware-Capture-{n}.raw")
        t = iot23_trace(cut_head(raw))
        ok, why = eligible(t)
        out["eligibility"][n] = why
        print("IoT-23", n, why, flush=True)
        if ok:
            atk.append((t["id"], arrays(t)))
    out["benign_traces"], out["attack_captures"] = len(ben), [n for n, _ in atk]
    out["R1-shipped_false_onsets_per_device_day"] = float(sum(rising(a["hot_shipped"][BURN:]).sum() for _, a in ben) / sum(benign_exposure(len(a["hot_shipped"])) for _, a in ben))
    for arm in ARMS:
        out["arms"][arm] = {"false_alarms": false_alarm_report(ben, arm, thr[arm]), "detection": detection_report(atk, arm, thr[arm])}
    sc = {arm: [arm_score(a, arm) for _, a in ben] for arm in ARMS}
    out["paired_chosen_minus_R1-recal"] = paired_difference(sc[chosen], sc["R1-recal"], thr[chosen], thr["R1-recal"])

    fa, det, base = out["arms"][chosen]["false_alarms"], out["arms"][chosen]["detection"], out["arms"]["R1-recal"]["detection"]
    c1 = "INCONCLUSIVE" if fa["benign_device_days"] < 20 else "PASS" if fa["ci95"][1] <= 3.75 else "INCONCLUSIVE" if fa["rate"] <= BUDGET else "FAIL"
    caps_with_eps = sum(1 for r in det["per_capture"] if r["episodes"])
    if det["episodes"] < 10 or caps_with_eps < 3:
        c2 = "INCONCLUSIVE"
    else:
        dl = (det["median_delay_s"] or 0) - (base["median_delay_s"] or 0)
        c2 = "PASS" if (base["recall"] - det["recall"] <= 0.10 and dl <= 60 and (base["auroc_mean_over_captures"] or 0) - (det["auroc_mean_over_captures"] or 0) <= 0.05) else "FAIL"
    pd_ = out["paired_chosen_minus_R1-recal"]
    c3 = "PASS" if c1 == "PASS" and c2 == "PASS" and pd_["ci95"][1] < 0 else "NOT SHOWN"
    out["claims"] = {"1_false_alarm_budget": c1, "2_detection_not_worse_than_R1-recal": c2, "3_better_than_recalibration": c3,
                     "R1-recal_passes_claim_1": out["arms"]["R1-recal"]["false_alarms"]["ci95"][1] <= 3.75 and out["arms"]["R1-recal"]["false_alarms"]["benign_device_days"] >= 20}
    for arm in ARMS:
        f, d = out["arms"][arm]["false_alarms"], out["arms"][arm]["detection"]
        print(f"{arm:9s} test benign {f['rate']:.2f}/device-day [{f['ci95'][0]:.2f},{f['ci95'][1]:.2f}] over {f['benign_device_days']:.1f} dd; recall {d['detected']}/{d['episodes']} delay {d['median_delay_s']} AUROC {d['auroc_mean_over_captures']}")
    print("R1-shipped", out["R1-shipped_false_onsets_per_device_day"])
    print("claims", out["claims"], "paired", pd_)
    Path("docs/results/r2_test.json").write_text(json.dumps(out, indent=1, default=float))


if __name__ == "__main__":
    {"dev": stage_dev, "validate": stage_validate, "test": stage_test}[sys.argv[1]]()
