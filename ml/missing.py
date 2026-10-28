"""Experiment D106: graceful degradation when signals go missing (protocol: docs/preregistration/D106-missing-signals.md).

Arms (all share the frozen per-modality detectors, soft scores and 1 s half-life; only the fusion layer differs):
  A_F1      the current learned fusion, a missing signal read as 0, F1's threshold
  A_budget  the same model, re-thresholded once on complete signals to the alert budget
  B         one learned fusion per availability pattern (fitted on that pattern's signals only), per-pattern thresholds
  C_ws      weighted sum renormalised over available signals, per-pattern thresholds
  C_nor     noisy-OR over available signals, per-pattern thresholds
  network_only  reference

  python -m ml.missing --mode dry      # fit 10-15, thresholds 16-17, evaluate 18-19 (validation seeds; may fix bugs only)
  python -m ml.missing --mode final    # THE run: fit 10-15, thresholds 16-19, evaluate 300-309; refuses to run twice
"""

import argparse
import json
import subprocess
import time
from pathlib import Path

import numpy as np
from scipy import stats

from backend.correlation.fusion import LearnedFusion
from .audit import agg, episode_rows, paired, seed_metrics
from .validate import FP_BUDGET, Combined, balanced_subsample, hold, load_combined

HALF_LIFE = 1.0
BUDGET = 3.0  # false alert onsets per device per day on ordinary time (pre-registered)
TICK_S = 5
F1_LEARNED_THR = 0.9831  # frozen F1 threshold of the learned fusion (data/results/f1.log)
ROWS = Path("data/rows")
OUT = Path("data/audit")
GUARD = Path("data/results/D106_TEST_SEEDS_USED")
PREREG_COMMIT = "4733306"
FIT = list(range(10, 16))
MODS = ["spatial", "network", "device", "proximity"]
HEAD = {"weak_correlated", "staged"}
FULL = 15  # bitmask with every signal available (bit i = MODS[i])

# which signals each episode touches (for the anomaly-correlated mask)
TOUCH = {
    "spatial": [0], "network": [1], "hardware": [2], "proximity": [3], "combined": [0, 1, 2, 3],
    "weak_correlated": [0, 1, 2], "staged": [0, 1, 2],
}
TOUCH_VARIANT = {"firmware_update": [1, 2], "authorized_visit": [0]}


# ------------------------------------------------------------------------------------- masks ------


def bursts(n: int, stationary: float, mean_len: float, rng: np.random.Generator) -> np.ndarray:
    """A two-state Markov on/off sequence of length n: `stationary` fraction of ticks 'off' (missing), mean off-run mean_len."""
    p_end = 1.0 / mean_len
    p_start = p_end * stationary / (1.0 - stationary)
    out = np.zeros(n, bool)
    i, off = 0, rng.random() < stationary
    while i < n:
        run = int(rng.geometric(p_end if off else p_start))
        if off:
            out[i:i + run] = True
        i += run
        off = not off
    return out


def make_mask(c: Combined, cond: str) -> np.ndarray:
    """(N, 4) bool, True = that signal is unavailable at that row. Deterministic in (condition, seed, device)."""
    n = len(c.ts)
    miss = np.zeros((n, 4), bool)
    if cond == "complete":
        return miss
    if cond.startswith("single:") or cond.startswith("pair:"):
        for m in cond.split(":")[1].split("+"):
            miss[:, MODS.index(m)] = True
        return miss
    cid = {"intermittent": 1, "correlated": 2}[cond]
    order = np.argsort(c.device, kind="stable")
    dev = c.device[order]
    cuts = np.flatnonzero(np.diff(dev)) + 1
    for rows in np.split(order, cuts):
        d = int(c.device[rows[0]])
        for m in range(4):
            rng = np.random.default_rng(np.random.SeedSequence([cid, int(c.seed[rows[0]]), d, m]))
            miss[rows, m] = bursts(len(rows), 0.20 if cond == "intermittent" else 0.02, 12.0, rng)
    if cond == "correlated":
        rng = np.random.default_rng(np.random.SeedSequence([2, 999]))
        for g, meta in enumerate(c.ep_meta):
            touched = TOUCH_VARIANT.get(meta["variant"]) if not meta["malicious"] else TOUCH.get(meta["scenario"])
            if not touched:
                continue
            rows = np.flatnonzero(c.episode == g)
            for m in touched:
                if rng.random() < 0.6:  # the whole signal is unavailable for the whole episode
                    miss[rows, m] = True
    return miss


def conditions() -> list[str]:
    single = [f"single:{m}" for m in MODS]
    pairs = [f"pair:{MODS[i]}+{MODS[j]}" for i in range(4) for j in range(i + 1, 4)]
    return ["complete"] + single + pairs + ["intermittent", "correlated"]


# ----------------------------------------------------------------------------------- scoring ------


def zero_missing(eff: np.ndarray, miss: np.ndarray) -> np.ndarray:
    return np.where(miss, 0.0, eff)


def pattern_of(miss: np.ndarray) -> np.ndarray:
    return ((~miss) * (1 << np.arange(4))).sum(axis=1).astype(np.int16)


def score_ws(E: np.ndarray, p: int) -> np.ndarray:
    cols = [i for i in range(4) if p >> i & 1]
    return np.clip(E[:, cols].mean(axis=1), 0, 1)  # equal weights renormalised over the available signals


def score_nor(E: np.ndarray, p: int) -> np.ndarray:
    cols = [i for i in range(4) if p >> i & 1]
    return 1.0 - np.prod(1.0 - np.clip(E[:, cols], 0, 1), axis=1)  # a missing signal contributes no evidence


class Onsets:
    """Fast alert-onset counting on ordinary time for threshold calibration."""

    def __init__(self, c: Combined):
        self.c = c
        self.order = np.argsort(c.device, kind="stable")
        dev = c.device[self.order]
        self.newdev = np.concatenate([[True], dev[1:] != dev[:-1]])
        self.ord_sorted = c.ordinary[self.order]
        self.usable_sorted = c.usable[self.order]
        self.days = c.ordinary.sum() * TICK_S / 86_400

    def per_day(self, s: np.ndarray, thr: float) -> float:
        h = (np.nan_to_num(s[self.order], nan=-1.0) > thr) & self.usable_sorted
        prev = np.concatenate([[False], h[:-1]])
        on = h & (~prev | self.newdev) & self.ord_sorted
        return float(on.sum() / self.days)


def calibrate(on: Onsets, s: np.ndarray, budget: float = BUDGET) -> float:
    """Smallest threshold whose ordinary-time false onsets per device-day do not exceed the budget."""
    ordv = s[on.c.ordinary]
    qs = 1.0 - np.logspace(-0.5, -6.0, 56)
    cands = np.unique(np.quantile(ordv, qs))
    for t in cands:  # ascending: the first that fits is the smallest
        if on.per_day(s, t) <= budget:
            return float(t)
    return float(cands[-1])


# --------------------------------------------------------------------------------------- fit -----


def fit_arms(fit_seeds: list[int], thr_seeds: list[int]) -> dict:
    c = load_combined(ROWS, fit_seeds + thr_seeds)
    eff = hold(c, c.soft, HALF_LIFE)
    rng = np.random.default_rng(0)
    fit_idx = np.flatnonzero(np.isin(c.seed, fit_seeds) & c.usable)
    sub = balanced_subsample(c, fit_idx, rng)  # identical to F1's fit_stage (same seeds, same rng start)
    t_idx = np.flatnonzero(np.isin(c.seed, thr_seeds))
    ct = Combined(*(getattr(c, f)[t_idx] for f in ("seed", "ts", "device", "hard", "soft", "feat", "label", "usable", "ordinary", "episode")),
                  ep_meta=[])
    et = eff[t_idx]
    on = Onsets(ct)

    A = LearnedFusion().fit(eff[sub], c.label[sub])
    arms: dict = {"A": A, "thr": {}, "B": {}, "calibration": {}}
    arms["thr"]["A_budget"] = calibrate(on, A.score_batch(et))
    arms["thr"]["network_only"] = calibrate(on, et[:, 1])
    for p in range(1, 16):
        cols = [i for i in range(4) if p >> i & 1]
        mask = np.zeros(4, bool); mask[cols] = True
        Es = np.where(mask, eff[sub], 0.0)  # signals absent from this pattern are held at a constant 0 and carry no weight
        lf = LearnedFusion().fit(Es, c.label[sub])
        arms["B"][p] = lf
        Et = np.where(mask, et, 0.0)
        arms["thr"][("B", p)] = calibrate(on, lf.score_batch(Et))
        arms["thr"][("C_ws", p)] = calibrate(on, score_ws(Et, p))
        arms["thr"][("C_nor", p)] = calibrate(on, score_nor(Et, p))
    arms["calibration_onsets"] = {
        "A_budget": on.per_day(A.score_batch(et), arms["thr"]["A_budget"]),
        "A_F1": on.per_day(A.score_batch(et), F1_LEARNED_THR),
    }
    print("threshold seeds: false onsets/device-day at the calibrated thresholds:", {k: round(v, 2) for k, v in arms["calibration_onsets"].items()}, flush=True)
    return arms


# ------------------------------------------------------------------------------------ evaluate ---


def evaluate(c: Combined, arms: dict, cond: str, eff: np.ndarray, eps: dict) -> dict:
    miss = make_mask(c, cond)
    pat = pattern_of(miss)
    pat[~c.usable] = -1
    use = c.usable
    E = zero_missing(eff, miss)
    blind = use & (pat == 0)
    margins: dict[str, np.ndarray] = {}

    sA = np.full(len(c.ts), np.nan, np.float32)
    sA[use] = arms["A"].score_batch(E[use])
    margins["A_F1"] = sA - F1_LEARNED_THR
    margins["A_budget"] = sA - arms["thr"]["A_budget"]
    for name in ("B", "C_ws", "C_nor"):
        m = np.full(len(c.ts), np.nan, np.float32)
        for p in range(1, 16):
            rows = np.flatnonzero(pat == p)
            if not len(rows):
                continue
            Ep = E[rows]
            s = arms["B"][p].score_batch(Ep) if name == "B" else score_ws(Ep, p) if name == "C_ws" else score_nor(Ep, p)
            m[rows] = s - arms["thr"][(name, p)]
        margins[name] = m
    mn = np.full(len(c.ts), np.nan, np.float32)
    ok = use & ~miss[:, 1]  # a single detector cannot score without its own signal
    mn[ok] = eff[ok, 1] - arms["thr"]["network_only"]
    margins["network_only"] = mn

    res = {}
    for name, mg in margins.items():
        per = seed_metrics(c, mg, 0.0, eps, HEAD)
        for sd, d in per.items():
            rows = (c.seed == sd) & use
            d["blind_fraction"] = float(blind[(c.seed == sd)].sum() / max(1, rows.sum()))
        res[name] = per
    return res


# ----------------------------------------------------------------------------------- verdict ------


def summarise(R: dict, conds: list[str]) -> dict:
    """Aggregate per-seed results into the pre-registered decision quantities."""
    arms = list(R["complete"])
    out: dict = {"arms": {}, "decision": {}}
    stress = [c for c in conds if c != "complete"]
    seeds = sorted(R["complete"]["A_budget"])
    for a in arms:
        deg_seed = {}
        for sd in seeds:
            base = R["complete"][a][sd]["recall_headline"]
            deg_seed[sd] = {"recall_headline": float(np.mean([R[c][a][sd]["recall_headline"] - base for c in stress]))}
        out["arms"][a] = {
            "complete": {k: agg(R["complete"][a], k) for k in ("recall_headline", "recall", "precision", "f1", "fp_onsets_per_device_day", "benign_flag_rate")},
            "mean_degradation_headline": agg(deg_seed, "recall_headline"),
            "max_fp_per_device_day_over_conditions": max(agg(R[c][a], "fp_onsets_per_device_day")["mean"] for c in stress),
            "macro_benign_flag": float(np.mean([agg(R[c][a], "benign_flag_rate")["mean"] for c in conds])),
            "per_condition": {c: {k: agg(R[c][a], k) for k in ("recall_headline", "recall", "precision", "f1", "fp_onsets_per_device_day", "benign_flag_rate", "blind_fraction")} for c in conds},
            "_deg_seed": {str(k): v for k, v in deg_seed.items()},
        }
    A = out["arms"]["A_budget"]
    for a in ("B", "C_ws", "C_nor"):
        X = out["arms"][a]
        deg_a = {sd: {"v": X["_deg_seed"][str(sd)]["recall_headline"]} for sd in seeds}
        deg_A = {sd: {"v": A["_deg_seed"][str(sd)]["recall_headline"]} for sd in seeds}
        diff = paired(deg_a, deg_A, "v")  # positive = less degradation than A_budget
        ii = X["max_fp_per_device_day_over_conditions"] <= 1.25 * BUDGET
        iii = X["macro_benign_flag"] - A["macro_benign_flag"] <= 0.05
        iv = X["complete"]["recall_headline"]["mean"] >= A["complete"]["recall_headline"]["mean"] - 0.03
        i = diff["diff"] >= 0.05 and diff["lo"] > 0
        corr_gap = (out["arms"][a]["per_condition"]["correlated"]["recall_headline"]["mean"]
                                 - A["per_condition"]["correlated"]["recall_headline"]["mean"])
        out["decision"][a] = {
            "degradation_advantage_vs_A_budget": diff, "i_degradation_rule": bool(i), "ii_false_alerts_le_3.75_everywhere": bool(ii),
            "iii_benign_not_worse": bool(iii), "iv_complete_not_worse": bool(iv), "all_four": bool(i and ii and iii and iv),
            "correlated_recall_minus_A_budget": corr_gap, "correlated_worse_than_A_by_more_than_0.05": bool(corr_gap < -0.05),
        }
    d = out["decision"]
    ties = {a: bool(d[a]["ii_false_alerts_le_3.75_everywhere"] and d[a]["iii_benign_not_worse"] and d[a]["iv_complete_not_worse"]
                    and out["arms"][a]["mean_degradation_headline"]["mean"] >= out["arms"]["B"]["mean_degradation_headline"]["mean"] - 0.03)
            for a in ("C_ws", "C_nor")}
    out["decision"]["prefer_C_over_B"] = ties
    out["decision"]["verdict"] = (
        "ADOPT B" if d["B"]["all_four"] and not any(ties.values()) else
        "ADOPT A SIMPLER LABEL-FREE RULE (C) in preference to B" if d["B"]["all_four"] and any(ties.values()) else
        ("C satisfies guardrails without B: consider C" if any(ties.values()) else "KEEP A UNCHANGED"))
    return out


def report(S: dict, conds: list[str]) -> None:
    print(f"\n{'arm':13s} {'complete recall_h':>18s} {'F1':>5s} {'FA/day':>7s} {'mean degradation':>17s} {'max FA/day (any cond.)':>23s} {'macro benign flag':>18s}")
    for a, X in S["arms"].items():
        c0 = X["complete"]
        print(f"{a:13s} {c0['recall_headline']['mean']:8.2f}+-{c0['recall_headline']['sd']:4.2f}   {c0['f1']['mean']:5.2f} {c0['fp_onsets_per_device_day']['mean']:7.2f} "
              f"{X['mean_degradation_headline']['mean']:+9.3f}+-{X['mean_degradation_headline']['sd']:4.3f} {X['max_fp_per_device_day_over_conditions']:15.2f} {X['macro_benign_flag']:18.2f}")
    print("\nrecall on headline (weak+staged) by condition; false alerts per device-day in brackets; blind-time % for B")
    print(f"{'condition':26s}" + "".join(f"{a:>17s}" for a in ("A_F1", "A_budget", "B", "C_ws", "C_nor")) + f"{'blind%':>8s}")
    for cnd in conds:
        row = f"{cnd:26s}"
        for a in ("A_F1", "A_budget", "B", "C_ws", "C_nor"):
            p = S["arms"][a]["per_condition"][cnd]
            row += f"{p['recall_headline']['mean']:8.2f} ({p['fp_onsets_per_device_day']['mean']:5.1f})  "
        row += f"{100 * S['arms']['B']['per_condition'][cnd]['blind_fraction']['mean']:7.1f}"
        print(row)
    print("\nDECISION (pre-registered rules):")
    for a in ("B", "C_ws", "C_nor"):
        d = S["decision"][a]
        v = d["degradation_advantage_vs_A_budget"]
        print(f"  {a:6s} less degradation than A_budget by {v['diff']:+.3f} [{v['lo']:+.3f}, {v['hi']:+.3f}] ({v['seeds_better']}/{v['n_seeds']} seeds)  "
              f"(i){d['i_degradation_rule']} (ii){d['ii_false_alerts_le_3.75_everywhere']} (iii){d['iii_benign_not_worse']} (iv){d['iv_complete_not_worse']}  "
              f"correlated vs A_budget {d['correlated_recall_minus_A_budget']:+.3f}{'  <-- WORSE THAN A' if d['correlated_worse_than_A_by_more_than_0.05'] else ''}")
    print("  prefer-C-over-B ties:", S["decision"]["prefer_C_over_B"])
    print("  VERDICT:", S["decision"]["verdict"])


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--mode", choices=["dry", "final"], required=True)
    a = ap.parse_args()
    if a.mode == "final":
        if GUARD.exists():
            raise SystemExit(f"{GUARD} exists: the D106 test seeds were already used. Refusing to run twice.")
        fit_seeds, thr_seeds, test_seeds = FIT, [16, 17, 18, 19], list(range(300, 310))
    else:
        fit_seeds, thr_seeds, test_seeds = FIT, [16, 17], [18, 19]
        assert all(s < 100 for s in fit_seeds + thr_seeds + test_seeds), "the dry run uses validation seeds only"
    t0 = time.time()
    print(f"D106 {a.mode}: fit {fit_seeds}, thresholds {thr_seeds}, evaluate {test_seeds}", flush=True)
    arms = fit_arms(fit_seeds, thr_seeds)
    print(f"fitted in {time.time() - t0:.0f}s; loading evaluation seeds", flush=True)
    c = load_combined(ROWS, test_seeds)
    eff = hold(c, c.soft, HALF_LIFE)
    eps = episode_rows(c)
    conds = conditions()
    R: dict = {}
    for cnd in conds:
        R[cnd] = evaluate(c, arms, cnd, eff, eps)
        print(f"  {cnd:26s} done ({time.time() - t0:.0f}s)", flush=True)
    S = summarise(R, conds)
    report(S, conds)
    OUT.mkdir(parents=True, exist_ok=True)
    meta = {"mode": a.mode, "prereg_commit": PREREG_COMMIT, "seeds": {"fit": fit_seeds, "thr": thr_seeds, "test": test_seeds},
            "code_commit": subprocess.run(["git", "rev-parse", "--short", "HEAD"], capture_output=True, text=True).stdout.strip(),
            "budget": BUDGET}
    raw = {c_: {a_: {str(k): v for k, v in d.items()} for a_, d in R[c_].items()} for c_ in R}
    (OUT / f"d106_{a.mode}.json").write_text(json.dumps({"meta": meta, "summary": S, "per_seed": raw}, indent=1, default=float))
    if a.mode == "final":
        GUARD.parent.mkdir(parents=True, exist_ok=True)
        GUARD.write_text(f"D106 test seeds 300-309 scored once. Pre-registration {PREREG_COMMIT}. Code {meta['code_commit']}.\n")
    print("\nwrote", OUT / f"d106_{a.mode}.json")


if __name__ == "__main__":
    main()
