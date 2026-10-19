"""Final evaluation F1 (manual §5.7): the one run on the test seeds.

Stage 1 uses VALIDATION seeds only: fit the trainable systems on seeds 10-15 and set every system's
threshold on ordinary rows of seeds 16-19. Stage 2 scores test seeds 100-109 with those frozen models.

  python -m ml.final_eval --dry-run     # pipeline check on validation seeds; cannot touch test seeds
  python -m ml.final_eval               # THE run; refuses if the guard file exists

The guard file is written when results are first printed, so a crash before that leaves the seeds usable.
"""

import argparse
import json
import platform
import subprocess
from datetime import datetime
from pathlib import Path

import numpy as np
from sklearn.metrics import average_precision_score

from backend.correlation.fusion import MODALITIES, LearnedFusion, MaxFusion, NoisyOr, SingleSignal, WeightedSum

from .iforest import EarlyFusionIF
from .supervised import EarlyFusionSupervised
from .validate import (
    FP_BUDGET, Combined, balanced_subsample, boot_ci, episode_groups, hold, load_combined, paired_diff_ci,
)

HALF_LIFE = 1.0  # frozen by E2 (manual §5.6)
FIT, THR, TEST = list(range(10, 16)), list(range(16, 20)), list(range(100, 110))
DRY = {"fit": list(range(10, 16)), "thr": [16, 17], "test": [18, 19]}
FROZEN_COMMIT = "9b3c7ef"  # the F1 pre-registration commit; generator and detectors are frozen at it
GUARD = Path("data/results/F1_TEST_SEEDS_USED")
TICK_S = 5

MAG_BINS = {
    "weak_correlated": [(0.05, 0.10), (0.10, 0.15), (0.15, 0.20), (0.20, 0.25)],
    "staged": [(0.05, 0.10), (0.10, 0.15), (0.15, 0.20), (0.20, 0.25)],
    "combined": [(0.2, 0.4), (0.4, 0.6), (0.6, 0.8), (0.8, 1.01)],
}
MAG_SYSTEMS = ["max", "network_only", "noisy_or", "learned", "early_supervised"]


# --------------------------------------------------------------------------- stage 1 ---


def fit_stage(rows_dir: Path, fit_seeds: list[int], thr_seeds: list[int]):
    """Fit on `fit_seeds`, thresholds on `thr_seeds`. Returns (scorers, thresholds). Validation data only."""
    assert all(s < 100 for s in fit_seeds + thr_seeds), "stage 1 must use validation seeds only"
    rng = np.random.default_rng(0)
    c = load_combined(rows_dir, fit_seeds + thr_seeds)
    eff = hold(c, c.soft, HALF_LIFE)
    held = hold(c, np.clip(c.feat, 0.0, None), HALF_LIFE)
    fit_idx = np.flatnonzero(np.isin(c.seed, fit_seeds) & c.usable)
    sub = balanced_subsample(c, fit_idx, rng)

    scorers: dict = {}
    for m in MODALITIES:
        f = SingleSignal(m)
        scorers[f.name] = lambda e, h, f=f: f.score_batch(e)
    for name, f in (("max", MaxFusion()), ("weighted_sum", WeightedSum()), ("noisy_or", NoisyOr())):
        scorers[name] = lambda e, h, f=f: f.score_batch(e)
    lf = LearnedFusion().fit(eff[sub], c.label[sub])
    scorers["learned"] = lambda e, h: lf.score_batch(e)
    forest = EarlyFusionIF(HALF_LIFE).fit_held(held[rng.choice(fit_idx, min(300_000, len(fit_idx)), replace=False)])
    scorers["early_iforest"] = lambda e, h: forest.score_held(h)
    gbm = EarlyFusionSupervised(HALF_LIFE).fit_held(held[sub], c.label[sub])
    scorers["early_supervised"] = lambda e, h: gbm.score_held(h)

    thr_idx = np.flatnonzero(np.isin(c.seed, thr_seeds) & c.ordinary)
    thr = {n: float(np.quantile(fn(eff[thr_idx], held[thr_idx]), 1 - FP_BUDGET)) for n, fn in scorers.items()}
    return scorers, thr


# --------------------------------------------------------------------------- stage 2 ---


def onsets_per_device_day(c: Combined, s: np.ndarray, thr: float) -> float:
    """Alert onsets per device per day on ordinary time (manual §5.7)."""
    order = np.argsort(c.device, kind="stable")
    dev = c.device[order]
    cuts = np.flatnonzero(np.diff(dev)) + 1
    onsets = 0
    for rows in np.split(order, cuts):
        hot, o = s[rows] > thr, c.ordinary[rows]
        onsets += int((hot[1:] & o[1:] & ~hot[:-1] & o[:-1]).sum())
    device_days = int(c.ordinary.sum()) * TICK_S / 86_400
    return onsets / device_days


def test_stage(rows_dir: Path, test_seeds: list[int], scorers: dict, thr: dict) -> tuple[Combined, dict]:
    c = load_combined(rows_dir, test_seeds)
    eff = hold(c, c.soft, HALF_LIFE)
    held = hold(c, np.clip(c.feat, 0.0, None), HALF_LIFE)
    use = np.flatnonzero(c.usable)
    n_ep = len(c.ep_meta)

    order = np.argsort(c.episode, kind="stable")
    sorted_ep = c.episode[order]
    ep_rows = {}
    for g in range(n_ep):
        lo, hi = np.searchsorted(sorted_ep, g), np.searchsorted(sorted_ep, g, side="right")
        rows = np.sort(order[lo:hi])
        ep_rows[g] = rows[c.usable[rows]]

    res = {}
    for name, fn in scorers.items():
        s = np.full(len(c.ts), np.nan, np.float32)
        s[use] = fn(eff[use], held[use])
        r = {"det": np.zeros(n_ep, bool), "ev": np.zeros(n_ep, bool), "ttd": np.full(n_ep, np.nan)}
        for g in range(n_ep):
            rows = ep_rows[g]
            if len(rows) == 0:
                continue
            r["ev"][g] = True
            hit = np.flatnonzero(s[rows] > thr[name])
            if len(hit):
                r["det"][g] = True
                r["ttd"][g] = c.ts[rows[hit[0]]] - c.ep_meta[g]["start"]
        ordi = np.flatnonzero(c.ordinary)
        r["ap"] = float(average_precision_score(c.label[use], s[use]))
        r["fp"] = float((s[ordi] > thr[name]).mean())
        r["onsets"] = onsets_per_device_day(c, s, thr[name])
        res[name] = r
    return c, res


# --------------------------------------------------------------------------- report ----


def recall_ci(r: dict, idx: np.ndarray, rng):
    idx = idx[r["ev"][idx]]
    return boot_ci(r["det"][idx].astype(float), rng) + (len(idx),)


def build_report(c: Combined, res: dict, rng) -> dict:
    g = episode_groups(c)
    g["weak_or_staged"] = np.union1d(g["weak_correlated"], g["staged"])
    cols = ["spatial", "network", "hardware", "proximity", "combined", "weak_correlated", "staged"]
    table = {}
    for name, r in res.items():
        row = {"ap": r["ap"], "fp_rate": r["fp"], "onsets_per_device_day": r["onsets"]}
        for k in cols + ["weak_or_staged", "firmware_update", "authorized_visit"]:
            m, lo, hi, n = recall_ci(r, g[k], rng)
            row[k] = {"recall": m, "lo": lo, "hi": hi, "n": n}
        det = g["malicious_all"]
        t = r["ttd"][det][r["det"][det]]
        row["mean_ttd_s"] = float(np.nanmean(t)) if len(t) else float("nan")
        row["macro_recall"] = float(np.mean([row[k]["recall"] for k in cols]))
        table[name] = row

    def cmp(label, a, b):
        idx = g["weak_or_staged"]
        idx = idx[res[a]["ev"][idx] & res[b]["ev"][idx]]
        d, lo, hi = paired_diff_ci(res[a]["det"][idx], res[b]["det"][idx], rng)
        return {"label": label, "a": a, "b": b, "diff": d, "lo": lo, "hi": hi, "n": int(len(idx))}

    primary = [
        cmp("P1 learned late fusion vs max of detectors (independent detection)", "learned", "max"),
        cmp("P2 learned late fusion vs network-only (best single detector on validation)", "learned", "network_only"),
        cmp("P3 learned late fusion vs supervised early fusion", "learned", "early_supervised"),
        cmp("P4 learned late fusion vs Isolation Forest (unsupervised early fusion)", "learned", "early_iforest"),
        cmp("P5 noisy-OR (no labels) vs max of detectors", "noisy_or", "max"),
    ]
    curves = {}
    for scen, bins in MAG_BINS.items():
        mags = np.array([e["magnitude"] for e in c.ep_meta])
        curves[scen] = {}
        for lo_b, hi_b in bins:
            idx = g[scen][(mags[g[scen]] >= lo_b) & (mags[g[scen]] < hi_b)]
            curves[scen][f"{lo_b:.2f}-{min(hi_b, 1.0):.2f}"] = {
                "n": int(len(idx)),
                **{s: float(res[s]["det"][idx[res[s]["ev"][idx]]].mean()) if len(idx) else float("nan") for s in MAG_SYSTEMS},
            }
    return {"table": table, "primary": primary, "magnitude_curves": curves}


def print_report(rep: dict) -> None:
    cols = ["spatial", "network", "hardware", "proximity", "combined", "weak_correlated", "staged"]
    print(f"{'system':<18}{'AP':>6}{'FP%':>6}{'onsets/d':>9}  " + "".join(f"{c[:9]:>10}" for c in cols) + f"{'fw_upd':>8}{'auth':>6}{'ttd_s':>7}")
    for n, t in rep["table"].items():
        print(f"{n:<18}{t['ap']:>6.3f}{t['fp_rate'] * 100:>6.2f}{t['onsets_per_device_day']:>9.2f}  "
              + "".join(f"{t[c]['recall']:>10.2f}" for c in cols)
              + f"{t['firmware_update']['recall']:>8.2f}{t['authorized_visit']['recall']:>6.2f}{t['mean_ttd_s']:>7.0f}")
    print("\n== Primary comparisons (paired bootstrap, weak-correlated + staged pooled) ==")
    for p in rep["primary"]:
        print(f"  {p['label']}\n      {p['a']} - {p['b']}: {p['diff']:+.3f}  95% CI [{p['lo']:+.3f}, {p['hi']:+.3f}]  (n={p['n']})")
    print("\n== Recall against effect size ==")
    for scen, bins in rep["magnitude_curves"].items():
        print(f"  {scen}")
        print(f"    {'magnitude':<12}{'n':>5}  " + "".join(f"{s:>18}" for s in MAG_SYSTEMS))
        for b, v in bins.items():
            print(f"    {b:<12}{v['n']:>5}  " + "".join(f"{v[s]:>18.2f}" for s in MAG_SYSTEMS))


def git_head() -> str:
    try:
        return subprocess.run(["git", "rev-parse", "HEAD"], capture_output=True, text=True, timeout=20).stdout.strip()
    except Exception:  # noqa: BLE001
        return "unknown"


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true", help="validation seeds only (fit 10-15, thr 16-17, test 18-19)")
    ap.add_argument("--rows", type=Path, default=Path("data/rows"))
    a = ap.parse_args()
    fit, thr_s, test = (DRY["fit"], DRY["thr"], DRY["test"]) if a.dry_run else (FIT, THR, TEST)
    out = Path("data/results/f1_dry.json" if a.dry_run else "data/results/f1.json")

    if not a.dry_run and GUARD.exists():
        raise SystemExit(f"{GUARD} exists: the test seeds were already used. A rerun needs a new seed range (manual §5.7).")

    print(f"stage 1: fit on {fit}, thresholds on {thr_s} (validation seeds only)", flush=True)
    scorers, thr = fit_stage(a.rows, fit, thr_s)
    print("thresholds:", {k: round(v, 4) for k, v in thr.items()}, flush=True)
    print(f"stage 2: scoring {'DRY-RUN seeds' if a.dry_run else 'TEST SEEDS'} {test[0]}-{test[-1]}", flush=True)
    c, res = test_stage(a.rows, test, scorers, thr)
    rep = build_report(c, res, np.random.default_rng(0))

    if not a.dry_run:  # results are about to be seen: the seeds are now spent
        GUARD.parent.mkdir(parents=True, exist_ok=True)
        GUARD.write_text(f"test seeds used {datetime.now().isoformat()} at {git_head()}\n")
    print(f"\n== F1 results ({'DRY RUN on validation seeds 18-19, NOT a result' if a.dry_run else 'test seeds 100-109'}; "
          f"{len(c.ep_meta)} episodes) ==")
    print_report(rep)
    meta = {"frozen_commit": FROZEN_COMMIT, "head": git_head(), "python": platform.python_version(),
            "os": platform.platform(), "seeds": {"fit": fit, "thr": thr_s, "test": test}, "half_life": HALF_LIFE,
            "thresholds": thr, "dry_run": a.dry_run}
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps({"meta": meta, **rep}, indent=2, default=float))
    print(f"\nwrote {out}")


if __name__ == "__main__":
    main()
