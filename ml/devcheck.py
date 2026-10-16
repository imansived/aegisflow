"""DEVELOPMENT comparison of every system on dev seeds. Not the evaluation (that is Phase 5).

Trains on one dev seed, picks each system's alert threshold on that same seed's ordinary
rows, and measures on a different dev seed. Refuses seeds >= 10 (manual §5 seed protocol).

  python -m ml.devcheck --train data/rows/seed1.npz --test data/rows/seed2.npz
"""

import argparse
from pathlib import Path

import numpy as np
from sklearn.metrics import average_precision_score

from backend.correlation.fusion import (
    MaxFusion, LearnedFusion, NoisyOr, SingleSignal, WeightedSum, MODALITIES,
)
from contracts.scenarios import ScenarioType

from .dataset import SCENARIO_CODES, RowSet, apply_fusion, hold_offline
from .iforest import EarlyFusionIF
from .supervised import EarlyFusionSupervised

TARGET_FP_TICK_RATE = 0.001  # each system alerts on at most 0.1% of ordinary ticks


def usable(rs: RowSet) -> np.ndarray:
    return ~rs.ignore & ~rs.learning


def ordinary(rs: RowSet) -> np.ndarray:
    return usable(rs) & (rs.scenario == -1)


def scores_for(name, rs, eff, models):
    if name.startswith("early_fusion"):
        return models[name].score(rs.ts, rs.device, rs.feat)
    return apply_fusion(models[name], eff)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--train", type=Path, required=True)
    ap.add_argument("--test", type=Path, required=True)
    ap.add_argument("--half-life", type=float, default=60.0)
    a = ap.parse_args()
    tr, te = RowSet.load(a.train), RowSet.load(a.test)
    for rs in (tr, te):
        if rs.seed >= 10:
            raise SystemExit(f"seed {rs.seed} is reserved for validation/test; devcheck is for dev seeds 1-9")

    eff_tr = hold_offline(tr.ts, tr.device, tr.raw.astype(np.float64), a.half_life)
    eff_te = hold_offline(te.ts, te.device, te.raw.astype(np.float64), a.half_life)

    models = {f"{m.value}_only": SingleSignal(m) for m in MODALITIES}
    models |= {"max (independent)": MaxFusion(), "weighted_sum": WeightedSum(), "noisy_or": NoisyOr()}
    fit_mask = usable(tr) & (tr.label >= 0)
    models["learned"] = LearnedFusion().fit(eff_tr[fit_mask], tr.label[fit_mask])
    # Unsupervised: trained on ALL usable rows, no labels (attacks are present, as in real life).
    models["early_fusion_iforest"] = EarlyFusionIF(a.half_life).fit(tr.ts, tr.device, tr.feat, usable(tr))
    models["early_fusion_supervised"] = EarlyFusionSupervised(a.half_life).fit(tr.ts, tr.device, tr.feat, tr.label, usable(tr))

    ok = usable(te)
    hn = SCENARIO_CODES.index(ScenarioType.HARD_NEGATIVE)
    variant = np.array([te.episodes[e].variant if e >= 0 else "" for e in range(-1, len(te.episodes))])[te.episode + 1]
    groups = {
        "spatial": [ScenarioType.SPATIAL], "network": [ScenarioType.NETWORK], "hardware": [ScenarioType.HARDWARE],
        "proximity": [ScenarioType.PROXIMITY], "combined": [ScenarioType.COMBINED], "weak_corr": [ScenarioType.WEAK_CORRELATED],
    }
    print(f"train seed {tr.seed} -> test seed {te.seed}; half-life {a.half_life}s; threshold = {1 - TARGET_FP_TICK_RATE:.1%} quantile of ordinary-tick scores on the TRAIN seed")
    print(f"{'system':<26}{'AP':>6}  " + "".join(f"{g:>10}" for g in groups) + f"{'fw_upd':>8}{'auth_vis':>9}{'ord.FP%':>9}")
    for name in models:
        s_tr = scores_for(name, tr, eff_tr, models)
        s_te = scores_for(name, te, eff_te, models)
        thr = float(np.quantile(s_tr[ordinary(tr)], 1 - TARGET_FP_TICK_RATE))
        ap_score = average_precision_score(te.label[ok], s_te[ok])
        row = f"{name:<26}{ap_score:>6.3f}  "
        for g, types in groups.items():
            codes = [SCENARIO_CODES.index(t) for t in types]
            eps = {e for e in np.unique(te.episode[ok & np.isin(te.scenario, codes)])}
            hit = sum(s_te[ok & (te.episode == e)].max() > thr for e in eps)
            row += f"{hit / max(1, len(eps)):>10.2f}"
        for v in ("firmware_update", "authorized_visit"):
            eps = np.unique(te.episode[ok & (te.scenario == hn) & (variant == v)])
            hit = sum(s_te[ok & (te.episode == e)].max() > thr for e in eps)
            row += f"{hit / max(1, len(eps)):>{8 if v == 'firmware_update' else 9}.2f}"
        fp = (s_te[ordinary(te)] > thr).mean()
        print(row + f"{fp:>9.2%}")
    print(
        "\ncolumns = fraction of EPISODES detected (any tick above threshold).\n"
        "fw_upd / auth_vis = BENIGN look-alikes wrongly flagged (lower is better). auth_vis is physically\n"
        "identical to a malicious restricted-zone entry, so no score can separate them: an irreducible floor."
    )


if __name__ == "__main__":
    main()
