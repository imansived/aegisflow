"""Experiment E1 (manual §5.5): does keeping weak evidence close the late-vs-early-fusion gap?

Runs exactly the pre-registered design on VALIDATION seeds 10-19 and refuses any other seed.

  python -m ml.validate --rows data/rows --out data/results/e1.json
"""

import argparse
import json
from dataclasses import dataclass
from pathlib import Path

import numpy as np
from sklearn.metrics import average_precision_score

from backend.correlation.fusion import (
    MODALITIES, LearnedFusion, MaxFusion, NoisyOr, SingleSignal, WeightedSum,
)
from contracts.scenarios import ScenarioType

from .dataset import SCENARIO_CODES, RowSet, hold_offline
from .iforest import EarlyFusionIF
from .supervised import EarlyFusionSupervised

SEEDS = list(range(10, 20))
FOLDS = [
    {"name": "A", "fit": [10, 11, 12], "thr": [13, 14], "eval": [15, 16, 17, 18, 19]},
    {"name": "B", "fit": [15, 16, 17], "thr": [18, 19], "eval": [10, 11, 12, 13, 14]},
]
FP_BUDGET = 0.001  # each system may alert on at most 0.1% of ordinary ticks
HALF_LIFE = 60.0
BOOT = 10_000
NEG_CAP = 600_000  # cap on negative training rows (balanced class weights make this harmless)
SWEEP = [15.0, 30.0, 60.0, 120.0, 300.0]


# --------------------------------------------------------------------------- data ------


@dataclass
class Combined:
    seed: np.ndarray
    ts: np.ndarray
    device: np.ndarray  # int32, UNIQUE across seeds (seed_index * 100 + device), so holds never mix runs
    hard: np.ndarray
    soft: np.ndarray
    feat: np.ndarray
    label: np.ndarray
    usable: np.ndarray
    ordinary: np.ndarray
    episode: np.ndarray  # global episode id, -1 = none
    ep_meta: list[dict]


def load_combined(rows_dir: Path) -> Combined:
    parts, meta, ep_off = [], [], 0
    for k, s in enumerate(SEEDS):
        rs = RowSet.load(rows_dir / f"seed{s}.npz")
        assert rs.seed == s, f"{s}: file holds seed {rs.seed}"
        n = len(rs)
        ep = np.where(rs.episode >= 0, rs.episode + ep_off, -1).astype(np.int32)
        usable = ~rs.ignore & ~rs.learning
        parts.append((
            np.full(n, s, np.int16), rs.ts, rs.device.astype(np.int32) + 100 * k, rs.raw, rs.raw_soft, rs.feat,
            rs.label, usable, usable & (rs.scenario == -1), ep,
        ))
        for e in rs.episodes:
            meta.append({"seed": s, "scenario": e.scenario.value, "variant": e.variant, "magnitude": e.magnitude,
                         "malicious": e.is_malicious, "start": e.start.timestamp()})
        ep_off += len(rs.episodes)
    cat = [np.concatenate(c) for c in zip(*parts)]
    return Combined(*cat, ep_meta=meta)


def hold(c: Combined, x: np.ndarray, half_life: float) -> np.ndarray:
    return hold_offline(c.ts, c.device, x.astype(np.float64), half_life)


# ------------------------------------------------------------------------ statistics --


def boot_ci(x: np.ndarray, rng: np.random.Generator) -> tuple[float, float, float]:
    if len(x) == 0:
        return float("nan"), float("nan"), float("nan")
    idx = rng.integers(0, len(x), (BOOT, len(x)))
    m = x[idx].mean(axis=1)
    return float(x.mean()), float(np.percentile(m, 2.5)), float(np.percentile(m, 97.5))


def paired_diff_ci(a: np.ndarray, b: np.ndarray, rng: np.random.Generator) -> tuple[float, float, float]:
    return boot_ci(a.astype(float) - b.astype(float), rng)


# ------------------------------------------------------------------------- systems -----


def balanced_subsample(c: Combined, idx: np.ndarray, rng: np.random.Generator) -> np.ndarray:
    pos, neg = idx[c.label[idx] == 1], idx[c.label[idx] == 0]
    if len(neg) > NEG_CAP:
        neg = rng.choice(neg, NEG_CAP, replace=False)
    return np.sort(np.concatenate([pos, neg]))


def build_systems(c: Combined, fit_idx: np.ndarray, hard_eff, soft_eff, held, rng) -> dict:
    """Fit every system on the fit seeds. Returns name -> scorer(rows) -> scores."""
    systems: dict = {}
    fit_use = fit_idx[c.usable[fit_idx]]
    fit_sub = balanced_subsample(c, fit_use, rng)
    for tag, eff in (("hard", hard_eff), ("soft", soft_eff)):
        stateless = {f"{m.value}_only": SingleSignal(m) for m in MODALITIES}
        stateless |= {"max": MaxFusion(), "weighted_sum": WeightedSum(), "noisy_or": NoisyOr()}
        for name, f in stateless.items():
            systems[f"{name}/{tag}"] = (lambda rows, f=f, eff=eff: f.score_batch(eff[rows]))
        lf = LearnedFusion().fit(eff[fit_sub], c.label[fit_sub])
        systems[f"learned/{tag}"] = (lambda rows, lf=lf, eff=eff: lf.score_batch(eff[rows]))
    forest = EarlyFusionIF(HALF_LIFE).fit_held(held[rng.choice(fit_use, min(300_000, len(fit_use)), replace=False)])
    systems["early_iforest"] = lambda rows: forest.score_held(held[rows])
    gbm = EarlyFusionSupervised(HALF_LIFE).fit_held(held[fit_sub], c.label[fit_sub])
    systems["early_supervised"] = lambda rows: gbm.score_held(held[rows])
    return systems


def evaluate(c: Combined, systems: dict, folds=FOLDS) -> dict:
    """Per system: thresholds from the threshold seeds, results on the evaluation seeds."""
    n_ep = len(c.ep_meta)
    out = {name: {"det": np.zeros(n_ep, bool), "ttd": np.full(n_ep, np.nan), "ev": np.zeros(n_ep, bool),
                  "ap": [], "fp": 0, "ord": 0, "thr": []} for name in systems[folds[0]["name"]]}
    ep_rows = {}
    order = np.argsort(c.episode, kind="stable")
    sorted_ep = c.episode[order]
    for g in range(n_ep):
        lo, hi = np.searchsorted(sorted_ep, g), np.searchsorted(sorted_ep, g, side="right")
        rows = np.sort(order[lo:hi])
        ep_rows[g] = rows[c.usable[rows]]

    for fold in folds:
        thr_idx = np.flatnonzero(np.isin(c.seed, fold["thr"]) & c.ordinary)
        eval_idx = np.flatnonzero(np.isin(c.seed, fold["eval"]) & c.usable)
        eval_ord = np.flatnonzero(np.isin(c.seed, fold["eval"]) & c.ordinary)
        eval_eps = {g for g, m in enumerate(c.ep_meta) if m["seed"] in fold["eval"]}
        for name, scorer in systems[fold["name"]].items():
            thr = float(np.quantile(scorer(thr_idx), 1 - FP_BUDGET))
            s_eval = scorer(eval_idx)
            r = out[name]
            r["thr"].append(thr)
            r["ap"].append(float(average_precision_score(c.label[eval_idx], s_eval)))
            r["fp"] += int((scorer(eval_ord) > thr).sum())
            r["ord"] += len(eval_ord)
            score_of = dict(zip(eval_idx.tolist(), s_eval.tolist()))
            for g in eval_eps:
                rows = ep_rows[g]
                if len(rows) == 0:
                    continue
                sc = np.fromiter((score_of[i] for i in rows.tolist()), float, len(rows))
                r["ev"][g] = True
                hit = np.flatnonzero(sc > thr)
                if len(hit):
                    r["det"][g] = True
                    r["ttd"][g] = c.ts[rows[hit[0]]] - c.ep_meta[g]["start"]
    return out


# --------------------------------------------------------------------------- report ----


def episode_groups(c: Combined) -> dict[str, np.ndarray]:
    m = c.ep_meta
    def pick(f):
        return np.array([i for i, e in enumerate(m) if f(e)], dtype=int)  # dtype: an empty group must still index
    g = {s.value: pick(lambda e, s=s: e["scenario"] == s.value) for s in
         (ScenarioType.SPATIAL, ScenarioType.NETWORK, ScenarioType.HARDWARE, ScenarioType.PROXIMITY,
          ScenarioType.COMBINED, ScenarioType.WEAK_CORRELATED)}
    g["firmware_update"] = pick(lambda e: e["variant"] == "firmware_update")
    g["authorized_visit"] = pick(lambda e: e["variant"] == "authorized_visit")
    g["malicious_all"] = pick(lambda e: e["malicious"])
    return g


def summarise(c: Combined, res: dict, rng) -> dict:
    groups = episode_groups(c)
    table = {}
    for name, r in res.items():
        row = {"ap": float(np.mean(r["ap"])), "fp_rate": r["fp"] / max(1, r["ord"])}
        for gname, idx in groups.items():
            idx = idx[r["ev"][idx]]
            mean, lo, hi = boot_ci(r["det"][idx].astype(float), rng)
            row[gname] = {"recall": mean, "lo": lo, "hi": hi, "n": int(len(idx))}
        det = groups["malicious_all"]
        t = r["ttd"][det][r["det"][det]]
        row["mean_ttd_s"] = float(np.nanmean(t)) if len(t) else float("nan")
        table[name] = row
    return table


def print_table(table: dict, names: list[str]) -> None:
    cols = ["spatial", "network", "hardware", "proximity", "combined", "weak_correlated"]
    print(f"{'system':<22}{'AP':>6}{'FP%':>7}  " + "".join(f"{c[:9]:>10}" for c in cols) + f"{'fw_upd':>8}{'auth':>7}{'ttd_s':>8}")
    for n in names:
        t = table[n]
        print(f"{n:<22}{t['ap']:>6.3f}{t['fp_rate'] * 100:>7.2f}  "
              + "".join(f"{t[c]['recall']:>10.2f}" for c in cols)
              + f"{t['firmware_update']['recall']:>8.2f}{t['authorized_visit']['recall']:>7.2f}{t['mean_ttd_s']:>8.0f}")


def compare(label, res, groups, a, b, rng, group="weak_correlated"):
    idx = groups[group]
    idx = idx[res[a]["ev"][idx] & res[b]["ev"][idx]]
    d, lo, hi = paired_diff_ci(res[a]["det"][idx], res[b]["det"][idx], rng)
    print(f"  {label:<46} {a} - {b}: {d:+.3f}  95% CI [{lo:+.3f}, {hi:+.3f}]  (n={len(idx)} {group} episodes)")
    return {"label": label, "a": a, "b": b, "diff": d, "lo": lo, "hi": hi, "n": int(len(idx))}


def run(rows_dir: Path, out: Path) -> None:
    rng = np.random.default_rng(0)
    c = load_combined(rows_dir)
    print(f"loaded {len(c.ts):,} rows, {len(c.ep_meta)} episodes from seeds {SEEDS[0]}-{SEEDS[-1]}")
    hard_eff, soft_eff = hold(c, c.hard, HALF_LIFE), hold(c, c.soft, HALF_LIFE)
    held = hold(c, np.clip(c.feat, 0.0, None), HALF_LIFE)

    systems = {}
    for fold in FOLDS:
        fit_idx = np.flatnonzero(np.isin(c.seed, fold["fit"]))
        systems[fold["name"]] = build_systems(c, fit_idx, hard_eff, soft_eff, held, rng)
        print(f"fitted fold {fold['name']}", flush=True)
    res = evaluate(c, systems)
    table = summarise(c, res, rng)
    groups = episode_groups(c)

    names = [n for n in table if n.endswith("/hard")] + [n for n in table if n.endswith("/soft")] + ["early_iforest", "early_supervised"]
    print("\n== E1 main comparison (validation seeds, pooled over both folds; recall = episodes detected) ==")
    print_table(table, names)

    print("\n== Pre-specified comparisons (paired bootstrap, weak-correlated episodes) ==")
    comps = []
    for f in ("noisy_or", "learned"):
        comps.append(compare(f"C1 soft vs hard ({f})", res, groups, f"{f}/soft", f"{f}/hard", rng))
    best_soft = max(("noisy_or/soft", "learned/soft", "max/soft", "weighted_sum/soft"), key=lambda n: table[n]["weak_correlated"]["recall"])
    comps.append(compare("C2 best soft late fusion vs supervised early", res, groups, best_soft, "early_supervised", rng))
    comps.append(compare("C2b best soft late fusion vs unsupervised early", res, groups, best_soft, "early_iforest", rng))
    for tag in ("hard", "soft"):
        best_fuse = max((f"{n}/{tag}" for n in ("noisy_or", "learned", "weighted_sum", "max")), key=lambda n: table[n]["weak_correlated"]["recall"])
        best_single = max((f"{m.value}_only/{tag}" for m in MODALITIES), key=lambda n: table[n]["weak_correlated"]["recall"])
        comps.append(compare(f"C3 best fusion vs best single signal [{tag}]", res, groups, best_fuse, best_single, rng))

    # ---- pre-registered decision rule ----
    wins = []
    for f in ("noisy_or", "learned"):
        cmp = next(x for x in comps if x["label"] == f"C1 soft vs hard ({f})")
        fp_ok = table[f"{f}/soft"]["fp_rate"] <= 1.5 * FP_BUDGET
        wins.append((f, cmp["lo"] > 0 and fp_ok, cmp["lo"], table[f"{f}/soft"]["fp_rate"]))
    freeze = "soft" if any(w[1] for w in wins) else "hard"
    print("\n== Decision (pre-registered rule, manual §5.5) ==")
    for f, ok, lo, fp in wins:
        print(f"  {f}: CI lower bound {lo:+.3f} (needs > 0), ordinary FP {fp:.3%} (needs <= {1.5 * FP_BUDGET:.3%}) -> {'PASS' if ok else 'fail'}")
    print(f"  => FREEZE THE {freeze.upper()} DESIGN")

    # ---- secondary: half-life sweep for the frozen score type ----
    sweep, best_hl = half_life_sweep(c, freeze, SWEEP, rng)

    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps({"table": table, "comparisons": comps, "decision": freeze, "sweep": {str(k): v for k, v in sweep.items()}, "half_life": best_hl}, indent=2))
    print(f"\nwrote {out}")


def half_life_sweep(c: Combined, score_type: str, values: list[float], rng) -> tuple[dict, float]:
    print(f"\n== Half-life sweep ({score_type} scores) ==")
    base = c.soft if score_type == "soft" else c.hard
    sweep = {}
    for hl in values:
        eff = hold(c, base, hl)
        sys_hl = {}
        for fold in FOLDS:
            fit_use = np.flatnonzero(np.isin(c.seed, fold["fit"]) & c.usable)
            sub = balanced_subsample(c, fit_use, rng)
            lf = LearnedFusion().fit(eff[sub], c.label[sub])
            sys_hl[fold["name"]] = {
                "noisy_or": (lambda rows, eff=eff: NoisyOr().score_batch(eff[rows])),
                "learned": (lambda rows, lf=lf, eff=eff: lf.score_batch(eff[rows])),
            }
        t2 = summarise(c, evaluate(c, sys_hl), rng)
        sweep[hl] = {k: {"ap": v["ap"], "weak": v["weak_correlated"]["recall"], "fp": v["fp_rate"]} for k, v in t2.items()}
        print(f"  half-life {hl:>5.0f}s  " + "  ".join(f"{k}: AP {v['ap']:.3f} weak {v['weak_correlated']['recall']:.2f} FP {v['fp_rate']:.2%}" for k, v in t2.items()), flush=True)
    # Rule fixed in code before the data existed: highest mean AP of noisy-OR and learned; ties -> 60 s.
    best = max(values, key=lambda h: (round((sweep[h]["noisy_or"]["ap"] + sweep[h]["learned"]["ap"]) / 2, 3), h == 60.0))
    edge = " (AT THE EDGE of the swept range: the optimum may lie beyond it)" if best in (min(values), max(values)) else ""
    print(f"  => chosen half-life: {best:.0f} s{edge}")
    return sweep, best


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--rows", type=Path, default=Path("data/rows"))
    ap.add_argument("--out", type=Path, default=Path("data/results/e1.json"))
    ap.add_argument("--sweep-only", choices=["soft", "hard"], help="skip the main comparison; only sweep half-lives")
    ap.add_argument("--half-lives", type=float, nargs="+", default=SWEEP)
    a = ap.parse_args()
    if a.sweep_only:
        sweep, best = half_life_sweep(load_combined(a.rows), a.sweep_only, a.half_lives, np.random.default_rng(0))
        a.out.parent.mkdir(parents=True, exist_ok=True)
        a.out.write_text(json.dumps({"sweep": {str(k): v for k, v in sweep.items()}, "half_life": best}, indent=2))
        return
    run(a.rows, a.out)


if __name__ == "__main__":
    main()
