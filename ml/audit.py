"""Audit of the frozen design (manual §5.8): per-seed variability and a hostile look at the simulator.

Everything here is EXPLORATORY and POST-HOC. The pre-registered experiments (E1, E2, F1) are not re-run and their
seeds are not re-spent: the models are fitted exactly as F1 fitted them (validation seeds 10-19 only), then scored on
(a) the spent test seeds 100-109, only to look at per-seed spread and at metrics F1 did not report (alert-level
precision, F1, false alarms per device-day), and (b) fresh stress conditions built on seeds 200+ by `ml.stress_build`.
No design decision is revisited; nothing here can change a frozen threshold or model.

  python -m ml.audit --part all
"""

import argparse
import json
import pickle
import time
from pathlib import Path

import numpy as np
from scipy import stats
from sklearn.metrics import average_precision_score

from .final_eval import FIT, HALF_LIFE, THR, TICK_S, fit_stage
from .stress_build import CONDITIONS, N_SEEDS
from .validate import FP_BUDGET, Combined, hold, load_combined

ROWS = Path("data/rows")
STRESS = Path("data/stress")
OUT = Path("data/audit")
SYSTEMS = ["spatial_only", "network_only", "device_only", "proximity_only", "max", "weighted_sum", "noisy_or", "learned",
           "early_iforest", "early_supervised"]
SINGLES = ["spatial_only", "network_only", "device_only", "proximity_only"]
# which z-feature columns belong to which modality (for removing a modality from the early-fusion controls too)
Z_OF = {"network": [0, 1, 2, 3], "device": [4, 5, 6], "proximity": [7, 8], "spatial": [9, 10, 11]}
MOD_IDX = {"spatial": 0, "network": 1, "device": 2, "proximity": 3}


# ----------------------------------------------------------------------------------- scoring ------


def score_all(c: Combined, scorers: dict, drop: str | None = None) -> dict[str, np.ndarray]:
    """Score every usable row with every system. `drop` removes one modality's information (sensor missing)."""
    eff = hold(c, c.soft, HALF_LIFE)
    held = hold(c, np.clip(c.feat, 0.0, None), HALF_LIFE)
    if drop:
        eff[:, MOD_IDX[drop]] = 0.0
        held[:, Z_OF[drop]] = 0.0
    use = np.flatnonzero(c.usable)
    out = {}
    for name, fn in scorers.items():
        s = np.full(len(c.ts), np.nan, np.float32)
        s[use] = fn(eff[use], held[use])
        out[name] = s
    return out


def onset_rows(c: Combined, hot: np.ndarray) -> np.ndarray:
    order = np.argsort(c.device, kind="stable")
    dev, h = c.device[order], hot[order]
    prev = np.concatenate([[False], h[:-1]])
    newdev = np.concatenate([[True], dev[1:] != dev[:-1]])
    return order[h & (~prev | newdev)]


def episode_rows(c: Combined) -> dict[int, np.ndarray]:
    order = np.argsort(c.episode, kind="stable")
    sorted_ep = c.episode[order]
    out = {}
    for g in range(len(c.ep_meta)):
        lo, hi = np.searchsorted(sorted_ep, g), np.searchsorted(sorted_ep, g, side="right")
        rows = np.sort(order[lo:hi])
        out[g] = rows[c.usable[rows]]
    return out


def family(meta: dict) -> str:
    """Group label of an episode, used for per-group recall. Malicious unseen-combination episodes group by shape."""
    v, sc = meta["variant"], meta["scenario"]
    if not meta["malicious"]:
        return v or "benign"
    if v.startswith("pair_") and v.endswith("_weak"): return "pair_weak"
    if v.startswith("pair_") and v.endswith("_moderate"): return "pair_moderate"
    if v.startswith("triple_"): return "triple_weak"
    if v.startswith("all_four"): return "all_four_weak"
    if v.startswith("cascade_"): return "cascade_unseen_order"
    return sc


def seed_metrics(c: Combined, s: np.ndarray, thr: float, eps: dict[int, np.ndarray], headline: set[str]) -> dict[int, dict]:
    """Per-seed metrics for one system at one threshold."""
    hot = np.nan_to_num(s, nan=-1.0) > thr
    hot &= c.usable
    on = onset_rows(c, hot)
    ordinary_on = on[c.ordinary[on]]
    res: dict[int, dict] = {}
    seeds = np.unique(c.seed)
    by_seed: dict[int, list[int]] = {int(sd): [] for sd in seeds}
    for g, m in enumerate(c.ep_meta):
        by_seed[m["seed"]].append(g)
    for sd in seeds:
        sd = int(sd)
        g_all = [g for g in by_seed[sd] if len(eps[g])]
        det = {g: bool(hot[eps[g]].any()) for g in g_all}
        mal = [g for g in g_all if c.ep_meta[g]["malicious"]]
        ben = [g for g in g_all if not c.ep_meta[g]["malicious"]]
        head = [g for g in mal if family(c.ep_meta[g]) in headline]
        ord_rows = int(((c.seed == sd) & c.ordinary).sum())
        dev_days = ord_rows * TICK_S / 86_400
        n_fp = int((c.seed[ordinary_on] == sd).sum())
        n_ben = sum(det[g] for g in ben)
        n_det = sum(det[g] for g in mal)
        recall = n_det / max(1, len(mal))
        precision = n_det / max(1, n_det + n_fp + n_ben)
        d = {
            "recall": recall,
            "recall_headline": (sum(det[g] for g in head) / len(head)) if head else float("nan"),
            "precision": precision,
            "f1": (2 * precision * recall / (precision + recall)) if precision + recall else 0.0,
            "fp_row_rate": float(hot[(c.seed == sd) & c.ordinary].mean()) if ord_rows else float("nan"),
            "fp_onsets_per_device_day": n_fp / max(dev_days, 1e-9),
            "benign_flag_rate": n_ben / max(1, len(ben)),
            "n_mal": len(mal), "n_benign": len(ben),
        }
        for fam in sorted({family(c.ep_meta[g]) for g in g_all}):
            gs = [g for g in g_all if family(c.ep_meta[g]) == fam]
            d[f"recall[{fam}]"] = sum(det[g] for g in gs) / len(gs)
        res[sd] = d
    return res


def agg(per_seed: dict[int, dict], key: str) -> dict:
    v = np.array([d[key] for d in per_seed.values() if key in d], float)
    v = v[~np.isnan(v)]
    if not len(v):
        return {"mean": float("nan"), "sd": float("nan"), "min": float("nan"), "max": float("nan"), "n": 0}
    return {"mean": float(v.mean()), "sd": float(v.std(ddof=1)) if len(v) > 1 else 0.0, "min": float(v.min()), "max": float(v.max()), "n": int(len(v))}


def paired(a: dict[int, dict], b: dict[int, dict], key: str) -> dict:
    """Seed-level paired difference with a t-interval: seeds, not episodes, are the independent units."""
    sd = [s for s in a if s in b and key in a[s] and key in b[s] and not np.isnan(a[s][key] - b[s][key])]
    d = np.array([a[s][key] - b[s][key] for s in sd], float)
    if len(d) < 2:
        return {"diff": float(d.mean()) if len(d) else float("nan"), "lo": float("nan"), "hi": float("nan"), "n_seeds": len(d), "seeds_better": int((d > 0).sum())}
    h = stats.t.ppf(0.975, len(d) - 1) * d.std(ddof=1) / np.sqrt(len(d))
    return {"diff": float(d.mean()), "lo": float(d.mean() - h), "hi": float(d.mean() + h), "n_seeds": len(d), "seeds_better": int((d > 0).sum())}


# --------------------------------------------------------------------------------- one analysis -----


def analyse(c: Combined, scorers: dict, thr0: dict, headline: set[str], drop: str | None = None, label: str = "") -> dict:
    """All systems, both threshold modes, per-seed metrics, paired comparisons, and AP."""
    t0 = time.time()
    scores = score_all(c, scorers, drop)
    eps = episode_rows(c)
    ordi = np.flatnonzero(c.ordinary)
    use = np.flatnonzero(c.usable)
    thr_re = {n: float(np.quantile(scores[n][ordi], 1 - FP_BUDGET)) for n in scorers}
    out: dict = {"label": label, "n_rows": int(len(c.ts)), "n_seeds": int(len(np.unique(c.seed))), "systems": {}}
    persys: dict[str, dict[str, dict[int, dict]]] = {}
    for n in scorers:
        persys[n] = {"frozen": seed_metrics(c, scores[n], thr0[n], eps, headline), "recalibrated": seed_metrics(c, scores[n], thr_re[n], eps, headline)}
        out["systems"][n] = {
            "ap": float(average_precision_score(c.label[use], scores[n][use])),
            "threshold_frozen": thr0[n], "threshold_recalibrated": thr_re[n],
            **{mode: {k: agg(persys[n][mode], k) for k in persys[n][mode][next(iter(persys[n][mode]))] if k not in ("n_mal", "n_benign")} for mode in ("frozen", "recalibrated")},
        }
    # paired comparisons on the headline recall, seed-level
    best_single = max(SINGLES, key=lambda n: np.nanmean([d["recall_headline"] for d in persys[n]["frozen"].values()]))
    out["best_single_by_headline_recall"] = best_single
    out["paired"] = {}
    for mode in ("frozen", "recalibrated"):
        out["paired"][mode] = {
            "learned - network_only": paired(persys["learned"][mode], persys["network_only"][mode], "recall_headline"),
            f"learned - best_single({best_single})": paired(persys["learned"][mode], persys[best_single][mode], "recall_headline"),
            "learned - max": paired(persys["learned"][mode], persys["max"][mode], "recall_headline"),
            "noisy_or - max": paired(persys["noisy_or"][mode], persys["max"][mode], "recall_headline"),
            "learned - early_supervised": paired(persys["learned"][mode], persys["early_supervised"][mode], "recall_headline"),
            "learned - early_iforest": paired(persys["learned"][mode], persys["early_iforest"][mode], "recall_headline"),
            "F1: learned - network_only": paired(persys["learned"][mode], persys["network_only"][mode], "f1"),
            "F1: learned - max": paired(persys["learned"][mode], persys["max"][mode], "f1"),
        }
    out["_per_seed"] = {n: {m: {str(k): v for k, v in persys[n][m].items()} for m in persys[n]} for n in persys}
    out["seconds"] = round(time.time() - t0)
    return out


def show(res: dict, title: str) -> None:
    print(f"\n==== {title}  ({res['n_seeds']} seeds, {res['n_rows']:,} rows)")
    for mode in ("frozen", "recalibrated"):
        print(f"  -- thresholds: {mode}   (recall_h = recall on the headline malicious groups; prec/F1 count false alarms and flagged benign look-alikes)")
        print(f"  {'system':17s} {'AP':>6s} {'recall_h':>13s} {'recall_all':>10s} {'prec':>6s} {'F1':>6s} {'FP/dev-day':>10s} {'benign flag':>11s}")
        for n in SYSTEMS:
            s = res["systems"][n][mode]
            print(f"  {n:17s} {res['systems'][n]['ap']:6.3f} {s['recall_headline']['mean']:6.2f}±{s['recall_headline']['sd']:4.2f}  {s['recall']['mean']:8.2f}  "
                  f"{s['precision']['mean']:6.2f} {s['f1']['mean']:6.2f} {s['fp_onsets_per_device_day']['mean']:10.2f} {s['benign_flag_rate']['mean']:11.2f}")
        for k, v in res["paired"][mode].items():
            print(f"     {k:46s} {v['diff']:+.3f}  95% CI [{v['lo']:+.3f}, {v['hi']:+.3f}]  better in {v['seeds_better']}/{v['n_seeds']} seeds")


# ------------------------------------------------------------------------------------- main ---------


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--part", choices=["perseed", "stress", "missing", "all"], default="all")
    a = ap.parse_args()
    OUT.mkdir(parents=True, exist_ok=True)
    print("fitting the frozen design on validation seeds only (as F1 did) ...", flush=True)
    scorers, thr0 = fit_stage(ROWS, FIT, THR)
    results: dict = {}
    BASE = {"spatial", "network", "hardware", "proximity", "combined", "weak_correlated", "staged"}
    HEAD = {"weak_correlated", "staged"}

    if a.part in ("perseed", "all"):
        c = load_combined(ROWS, list(range(100, 110)))
        r = analyse(c, scorers, thr0, HEAD, label="F1 test seeds 100-109, per-seed (exploratory, post-hoc)")
        show(r, "F1 test seeds, per-seed spread (headline = weak_correlated + staged)")
        results["f1_perseed"] = r
        del c

    if a.part in ("stress", "all"):
        for cond, first in CONDITIONS.items():
            seeds = list(range(first, first + N_SEEDS))
            if not all((STRESS / cond / f"seed{s}.npz").exists() for s in seeds):
                print(f"skip {cond}: rows not built yet"); continue
            c = load_combined(STRESS / cond, seeds)
            head = {"unseen": {"pair_weak", "pair_moderate", "triple_weak", "all_four_weak", "cascade_unseen_order"}}.get(cond, HEAD)
            r = analyse(c, scorers, thr0, head, label=cond)
            if cond == "drift":  # split by whether the device drifted
                r["note"] = "devices with an even index drift after hour 20; see drift_split"
            show(r, f"condition: {cond}")
            if cond == "unseen":
                for fam in ("pair_weak", "pair_moderate", "triple_weak", "all_four_weak", "cascade_unseen_order"):
                    print(f"     recall[{fam}] frozen: " + "  ".join(f"{n}={r['systems'][n]['frozen'].get(f'recall[{fam}]', {}).get('mean', float('nan')):.2f}" for n in ("network_only", "max", "noisy_or", "learned", "early_supervised")))
            results[cond] = r
            if cond == "control":
                # missing signals: remove one modality at a time from the fresh control runs
                for m in ("spatial", "network", "device", "proximity"):
                    rm = analyse(c, scorers, thr0, HEAD, drop=m, label=f"control, {m} missing")
                    show(rm, f"missing signal: {m} unavailable (control seeds)")
                    results[f"missing_{m}"] = rm
            del c

    (OUT / "audit.json").write_text(json.dumps(results, indent=1, default=float))
    print("\nwrote", OUT / "audit.json")


if __name__ == "__main__":
    main()
