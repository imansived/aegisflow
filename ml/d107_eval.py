"""D107 evaluation (protocol: docs/preregistration/D107-availability-implementation.md).

  python -m ml.d107 evaluate            # final: seeds 400-409; refuses to run twice
  python -m ml.d107_eval --dry          # pipeline check on validation seed 18 only (data/d107_dry); no guard, no claims
"""

import argparse
import json
import subprocess
from pathlib import Path

import numpy as np

from backend.correlation.engine import FROZEN_CORRELATION, CorrelationEngine
from backend.correlation.fusion import MODALITIES, LearnedFusion, NoisyOr
from backend.correlation.thresholds import ThresholdTable
from backend.detection.types import DetectorResult, Scores
from contracts.scenarios import Episode
from datetime import datetime, timezone

from .audit import agg, episode_rows, paired, seed_metrics
from .d107 import CONDS, DESIGN, E2E_SUSTAINED_SEEDS, GUARD, MODS, OUT, PREREG_COMMIT, SEEDS, SUSTAINED, TABLE, Masker
from .missing import BUDGET, FIT, fit_arms
from .dataset import hold_offline
from .validate import Combined, hold

HEAD = {"weak_correlated", "staged"}
F1_THR = 0.9831


def path_of(root: Path, cond: str, seed: int) -> Path:
    return root / cond.replace(":", "_").replace("+", "-") / f"seed{seed}.npz"


def available_bits(cond: str) -> int:
    lost = set(cond.split(":")[1].split("+")) if cond.startswith(("single:", "pair:")) else set()
    return sum(1 << i for i, m in enumerate(MODS) if m not in lost)


def load(root: Path, cond: str, seeds: list[int], e2e_sustained: list[int]):
    """Rows for one condition over seeds: the end-to-end file when one exists, else the complete run masked."""
    parts, meta, ep_off, E_all, pat_all, runs = [], [], 0, [], [], {}
    for k, sd in enumerate(seeds):
        f = path_of(root, cond, sd)
        derived = not f.exists()
        z = np.load(f if not derived else path_of(root, "complete", sd), allow_pickle=False)
        runs[sd] = {"derived": derived, "alerts": [json.loads(x) for x in z["alerts"] if str(x)], "t_warm": float(z["t_warm"]),
                    "opened_on_fallback": int(z["opened_on_fallback"]) if not derived else 0}
        soft = z["soft"].astype(np.float64)
        if derived:
            bits = available_bits(cond)
            for i in range(4):
                if not bits >> i & 1:
                    soft[:, i] = 0.0
            pat = np.full(len(soft), bits, np.int16)
        else:
            pat = z["avail"].astype(np.int16)
        n = len(soft)
        eps = [Episode.model_validate_json(str(s)) for s in z["episodes"]]
        usable = ~z["ignore"] & ~z["learning"]
        ep = np.where(z["episode"] >= 0, z["episode"] + ep_off, -1).astype(np.int32)
        parts.append((np.full(n, sd, np.int16), z["ts"], z["dev"].astype(np.int32) + 100 * k, z["hard"], soft.astype(np.float32),
                      np.zeros((n, 1), np.float32), z["label"], usable, usable & (z["scenario"] == -1), ep))
        for e in eps:
            meta.append({"seed": sd, "scenario": e.scenario.value, "variant": e.variant, "magnitude": e.magnitude,
                         "malicious": e.is_malicious, "start": e.start.timestamp(), "device": e.device_id, "end": e.end.timestamp()})
        ep_off += len(eps)
        E_all.append(soft)
        pat_all.append(pat)
    cat = [np.concatenate(c) for c in zip(*parts)]
    c = Combined(*cat, ep_meta=meta)
    return c, np.concatenate(E_all), np.concatenate(pat_all), runs


def product_metrics(c: Combined, runs: dict) -> dict[int, dict]:
    out = {}
    for sd, r in runs.items():
        if r["derived"]:
            continue
        opened = [e for e in r["alerts"] if e["event"] == "opened" and datetime.fromisoformat(e["started"]).timestamp() >= r["t_warm"]]
        eps = [m for m in c.ep_meta if m["seed"] == sd and m["malicious"]]
        hit = sum(any(e["device_id"] == m["device"] and m["start"] <= datetime.fromisoformat(e["started"]).timestamp() <= m["end"] + 60 for e in opened) for m in eps)
        days = 20 * 39 / 24
        out[sd] = {"alerts_opened_per_device_day": len(opened) / days, "malicious_episodes_with_an_alert": hit / max(1, len(eps))}
    return out


def evaluate_condition(c, E, pat, lf, table: dict, arms: dict, eps) -> dict:
    use = c.usable
    s = np.full(len(c.ts), np.nan, np.float32)
    s[use] = lf.score_batch(E[use])
    margins = {"A_F1": s - F1_THR, "A_budget": s - table["15"]["raise_at"]}
    thr_s = np.full(len(c.ts), np.nan, np.float32)
    mB = np.full(len(c.ts), np.nan, np.float32)
    for p in range(1, 16):
        rows = np.flatnonzero(use & (pat == p))
        if not len(rows):
            continue
        ent = table.get(str(p))
        if ent and ent["validated"]:
            thr_s[rows] = ent["raise_at"]
        mB[rows] = arms["B"][p].score_batch(E[rows]) - arms["thr"][("B", p)]
    margins["S"] = s - thr_s  # NaN where the pattern is unvalidated or nothing is available: the conservative fallback
    margins["B"] = mB
    blind = use & (pat == 0)
    res = {}
    for name, mg in margins.items():
        per = seed_metrics(c, mg, 0.0, eps, HEAD)
        for sd, d in per.items():
            d["blind_fraction"] = float(blind[c.seed == sd].sum() / max(1, use[c.seed == sd].sum()))
        res[name] = per
    return res


def conformance(root: Path, seeds: list[int], lf, table_json: dict, table: ThresholdTable) -> dict:
    """R4 and R5 on the end-to-end runs."""
    out = {"max_score_difference_float32": 0.0, "pattern_mismatches": 0, "alertable_mismatches": 0, "rows": 0, "obs_violations": 0,
           "threshold_violations": 0, "hot_on_fallback": 0, "opened_on_fallback": 0, "sustained_soft_not_identical": 0, "runs": 0}
    for sd in seeds:
        base = np.load(path_of(root, "complete", sd), allow_pickle=False)
        for cond in CONDS:
            f = path_of(root, cond, sd)
            if not f.exists():
                continue
            z = np.load(f, allow_pickle=False)
            plan = [Episode.model_validate_json(str(x)) for x in z["episodes"]]
            n = len(z["ts"])
            out["runs"] += 1
            out["rows"] += n
            E = hold_offline(z["ts"], z["dev"], z["soft"].astype(np.float64), 1.0)
            off = lf.score_batch(E).astype(np.float32)
            out["max_score_difference_float32"] = max(out["max_score_difference_float32"], float(np.abs(off - z["p_score"]).max()))
            # expected availability from the CONDITION'S DEFINITION (independent of the engine's own bookkeeping)
            t_warm = float(z["t_warm"])
            masker = Masker(cond, sd, plan, int((39 * 3600) // 5), t_warm, 5)
            names = [f"D-{d + 1:03d}" for d in z["dev"]]
            exp = np.fromiter((sum(1 << i for i, m in enumerate(MODS) if m not in masker.lost(nm, ts)) for nm, ts in zip(names, z["ts"])), np.int16, n)
            out["pattern_mismatches"] += int((exp != z["p_pattern"]).sum())
            ent_ok = np.array([bool(table_json["patterns"].get(str(p), {}).get("validated")) for p in range(16)])
            out["alertable_mismatches"] += int((ent_ok[exp] != z["p_alertable"]).sum())
            out["obs_violations"] += int(((z["p_pattern"] != 15) & (z["p_obs"] == 0)).sum())
            raise_at = np.array([table_json["patterns"].get(str(p), {}).get("raise_at", np.nan) for p in range(16)], np.float32)
            al = z["p_alertable"]
            out["threshold_violations"] += int((np.abs(z["p_thr"][al] - raise_at[z["p_pattern"][al]]) > 1e-6).sum())
            out["hot_on_fallback"] += int((z["p_hot"] & ~al).sum())
            out["opened_on_fallback"] += int(z["opened_on_fallback"])
            if cond in SUSTAINED:
                bits = available_bits(cond)
                for i in range(4):
                    if bits >> i & 1 and not (np.array_equal(z["soft"][:, i], base["soft"][:, i]) and np.array_equal(z["hard"][:, i], base["hard"][:, i])):
                        out["sustained_soft_not_identical"] += 1
    # R4(d): every pattern absent from the table takes the fallback
    miss = []
    for p in range(1, 16):
        d = json.loads(json.dumps(table_json))
        d["patterns"].pop(str(p), None)
        tab = ThresholdTable.from_dict(d)
        lost = [m for i, m in enumerate(MODALITIES) if not p >> i & 1]
        results = {m: DetectorResult(m, 0.0, False, {}, 0.0, available=m not in lost) for m in MODALITIES}
        ce = CorrelationEngine(NoisyOr(), FROZEN_CORRELATION, tab)
        f = ce.update(Scores("D-001", datetime(2026, 10, 5, tzinfo=timezone.utc), "work", results, {}))
        if (f.alertable or f.observability == "full") and p != 15:
            miss.append(p)
        if p == 15 and not f.alertable:
            miss.append(p)
    out["fallback_failures_when_pattern_removed"] = [p for p in miss if p != 15]
    out["complete_fallback_when_15_removed_is_alertable"] = 15 not in miss
    return out


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry", action="store_true")
    a = ap.parse_args()
    if a.dry:
        root, seeds, e2e = Path("data/d107_dry"), [18], [18]
    else:
        if GUARD.exists():
            raise SystemExit(f"{GUARD} exists: the D107 test seeds were already scored. Refusing to run twice.")
        root, seeds, e2e = OUT, SEEDS, E2E_SUSTAINED_SEEDS
        assert min(seeds) >= 400
    table_json = json.loads(TABLE.read_text())
    table = ThresholdTable.from_dict(table_json)
    lf = LearnedFusion.from_dict(json.loads(DESIGN.read_text())["fusion"]["learned"])
    print(f"D107 {'DRY' if a.dry else 'FINAL'}: seeds {seeds}; fitting arm B on validation seeds as D106 did", flush=True)
    arms = fit_arms(FIT, [16, 17, 18, 19]) if not a.dry else fit_arms(FIT, [16, 17])
    R, PROD = {}, {}
    for cond in CONDS:
        c, E, pat, runs = load(root, cond, seeds, e2e)
        E = hold(c, E, 1.0)  # the frozen 1 s decayed-peak, exactly what the correlation engine applies per device
        R[cond] = evaluate_condition(c, E, pat, lf, table_json["patterns"], arms, episode_rows(c))
        PROD[cond] = product_metrics(c, runs)
        print(f"  {cond:26s} scored", flush=True)
    conf = conformance(root, e2e if not a.dry else [18], lf, table_json, table)

    ARMS = ["A_F1", "A_budget", "S", "B"]
    stress = [c for c in CONDS if c != "complete"]
    sds = sorted(R["complete"]["S"])
    S: dict = {"arms": {}, "criteria": {}}
    deg = {}
    for arm in ARMS:
        deg[arm] = {sd: {"v": float(np.mean([R[c][arm][sd]["recall_headline"] - R["complete"][arm][sd]["recall_headline"] for c in stress]))} for sd in sds}
        S["arms"][arm] = {
            "complete": {k: agg(R["complete"][arm], k) for k in ("recall_headline", "recall", "precision", "f1", "fp_onsets_per_device_day", "benign_flag_rate")},
            "mean_degradation": agg(deg[arm], "v"),
            "worst_condition_false_alerts": max((agg(R[c][arm], "fp_onsets_per_device_day")["mean"], c) for c in stress),
            "macro_benign_flag": float(np.mean([agg(R[c][arm], "benign_flag_rate")["mean"] for c in CONDS])),
            "per_condition": {c: {k: agg(R[c][arm], k) for k in ("recall_headline", "recall", "precision", "f1", "fp_onsets_per_device_day", "benign_flag_rate", "blind_fraction")} for c in CONDS},
        }
    r1 = paired(deg["S"], deg["A_budget"], "v")
    r2 = paired(deg["S"], deg["B"], "v")
    s_ = S["arms"]["S"]; a_ = S["arms"]["A_budget"]
    net = "single:network"
    r6 = S["arms"]["S"]["per_condition"][net]["recall_headline"]["mean"] - S["arms"]["A_budget"]["per_condition"][net]["recall_headline"]["mean"]
    S["criteria"] = {
        "R1_benefit": {"diff": r1, "pass": bool(r1["diff"] >= 0.08 and r1["lo"] > 0)},
        "R2_not_worse_than_B": {"diff": r2, "pass": bool(r2["lo"] > -0.03)},
        "R3_guardrails": {
            "worst_false_alerts": s_["worst_condition_false_alerts"], "benign_flag_diff": s_["macro_benign_flag"] - a_["macro_benign_flag"],
            "complete_recall_diff": s_["complete"]["recall_headline"]["mean"] - a_["complete"]["recall_headline"]["mean"],
            "pass": bool(s_["worst_condition_false_alerts"][0] <= 3.75 and s_["macro_benign_flag"] - a_["macro_benign_flag"] <= 0.05
                         and s_["complete"]["recall_headline"]["mean"] - a_["complete"]["recall_headline"]["mean"] >= -0.03)},
        "R4_invariants": {"obs_violations": conf["obs_violations"], "threshold_violations": conf["threshold_violations"], "hot_on_fallback": conf["hot_on_fallback"],
                          "opened_on_fallback": conf["opened_on_fallback"], "fallback_failures": conf["fallback_failures_when_pattern_removed"],
                          "pass": bool(conf["obs_violations"] == 0 and conf["threshold_violations"] == 0 and conf["hot_on_fallback"] == 0
                                       and conf["opened_on_fallback"] == 0 and not conf["fallback_failures_when_pattern_removed"])},
        "R5_conformance": {"max_score_difference_float32": conf["max_score_difference_float32"], "pattern_mismatches": conf["pattern_mismatches"],
                           "alertable_mismatches": conf["alertable_mismatches"], "sustained_soft_not_identical": conf["sustained_soft_not_identical"], "rows": conf["rows"], "runs": conf["runs"],
                           "pass": bool(conf["max_score_difference_float32"] <= 1.2e-7 and conf["pattern_mismatches"] == 0 and conf["alertable_mismatches"] == 0
                                        and conf["sustained_soft_not_identical"] == 0)},
        "R6_network_missing_gain": {"diff": r6, "pass": bool(r6 >= 0.20)},
    }
    S["all_pass"] = all(v["pass"] for v in S["criteria"].values())
    S["product_alert_manager"] = {c: {k: float(np.mean([m[k] for m in PROD[c].values()])) for k in ("alerts_opened_per_device_day", "malicious_episodes_with_an_alert")} for c in CONDS if PROD[c]}

    print("\narm         complete recall_h  F1    FA/day   mean degradation (12 conditions)   worst-condition FA/day")
    for arm in ARMS:
        X = S["arms"][arm]
        print(f"{arm:9s} {X['complete']['recall_headline']['mean']:9.2f}+-{X['complete']['recall_headline']['sd']:4.2f}  {X['complete']['f1']['mean']:5.2f} {X['complete']['fp_onsets_per_device_day']['mean']:6.2f}   "
              f"{X['mean_degradation']['mean']:+8.3f}+-{X['mean_degradation']['sd']:5.3f}                     {X['worst_condition_false_alerts'][0]:5.2f} ({X['worst_condition_false_alerts'][1]})")
    print("\nheadline recall by condition  (false alerts per device-day)")
    print(f"{'condition':26s}" + "".join(f"{x:>16s}" for x in ARMS))
    for c in CONDS:
        print(f"{c:26s}" + "".join(f"{S['arms'][x]['per_condition'][c]['recall_headline']['mean']:8.2f} ({S['arms'][x]['per_condition'][c]['fp_onsets_per_device_day']['mean']:4.1f}) " for x in ARMS))
    print("\nCRITERIA")
    for k, v in S["criteria"].items():
        print(f"  {k:26s} {'PASS' if v['pass'] else 'FAIL'}  {json.dumps({a_: b for a_, b in v.items() if a_ != 'pass'}, default=float)[:300]}")
    print("  ALL PASS:", S["all_pass"])
    print("  product alert manager (S):", {c: tuple(round(x, 2) for x in v.values()) for c, v in list(S["product_alert_manager"].items())[:4]})

    meta = {"mode": "dry" if a.dry else "final", "prereg_commit": PREREG_COMMIT, "seeds": seeds, "budget": BUDGET,
            "code_commit": subprocess.run(["git", "rev-parse", "--short", "HEAD"], capture_output=True, text=True).stdout.strip()}
    Path("data/audit").mkdir(parents=True, exist_ok=True)
    name = "d107_dry.json" if a.dry else "d107_final.json"
    Path("data/audit", name).write_text(json.dumps({"meta": meta, "summary": S, "conformance": conf}, indent=1, default=float))
    if not a.dry:
        GUARD.parent.mkdir(parents=True, exist_ok=True)
        GUARD.write_text(f"D107 test seeds 400-409 scored once. Pre-registration {PREREG_COMMIT}. Code {meta['code_commit']}.\n")
    print("wrote", Path("data/audit", name))


if __name__ == "__main__":
    main()
