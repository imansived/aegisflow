"""POST-HOC, EXPLORATORY ablation after the D106 final run (manual §5.11): is B's gain from its per-pattern MODELS, or
only from its per-pattern THRESHOLDS?

D106's verdict (ADOPT B) is already recorded and is not revisited. This adds one arm, A_patthr: the frozen model with a
missing signal read as 0 (exactly arm A), but with a separate threshold per availability pattern calibrated to the same
alert budget. It re-scores the D106 test seeds, so it is exploratory and labelled as such.

  python -m ml.missing_ablation
"""

import json
from pathlib import Path

import numpy as np

from .audit import episode_rows, paired, seed_metrics
from .missing import (BUDGET, FIT, HEAD, ROWS, Onsets, calibrate, conditions, fit_arms, make_mask, pattern_of, zero_missing)
from .validate import hold, load_combined

HALF_LIFE = 1.0


def main() -> None:
    thr_seeds, test_seeds = [16, 17, 18, 19], list(range(300, 310))
    arms = fit_arms(FIT, thr_seeds)
    A = arms["A"]
    ct = load_combined(ROWS, thr_seeds)
    et = hold(ct, ct.soft, HALF_LIFE)
    on = Onsets(ct)
    thr_pat = {}
    for p in range(1, 16):
        mask = np.array([bool(p >> i & 1) for i in range(4)])
        thr_pat[p] = calibrate(on, A.score_batch(np.where(mask, et, 0.0)))
    c = load_combined(ROWS, test_seeds)
    eff = hold(c, c.soft, HALF_LIFE)
    eps = episode_rows(c)
    conds = conditions()
    res: dict = {}
    for cnd in conds:
        miss = make_mask(c, cnd)
        pat = pattern_of(miss)
        pat[~c.usable] = -1
        E = zero_missing(eff, miss)
        mA = np.full(len(c.ts), np.nan, np.float32)
        mB = np.full(len(c.ts), np.nan, np.float32)
        for p in range(1, 16):
            rows = np.flatnonzero(pat == p)
            if len(rows):
                mA[rows] = A.score_batch(E[rows]) - thr_pat[p]
                mB[rows] = arms["B"][p].score_batch(E[rows]) - arms["thr"][("B", p)]
        res[cnd] = {"A_patthr": seed_metrics(c, mA, 0.0, eps, HEAD), "B": seed_metrics(c, mB, 0.0, eps, HEAD)}
    seeds = sorted(res["complete"]["B"])
    stress = [x for x in conds if x != "complete"]

    def deg(arm):
        return {s: {"v": float(np.mean([res[x][arm][s]["recall_headline"] - res["complete"][arm][s]["recall_headline"] for x in stress]))} for s in seeds}

    dB, dA = deg("B"), deg("A_patthr")
    print("\nA_patthr (frozen model, missing read as 0, per-pattern thresholds) versus B (per-pattern models, per-pattern thresholds)")
    print(f"{'condition':26s} {'A_patthr':>16s} {'B':>16s}   recall_headline (false alerts per device-day)")
    for cnd in conds:
        a, b = res[cnd]["A_patthr"], res[cnd]["B"]
        fa = lambda r: np.mean([r[s]["fp_onsets_per_device_day"] for s in seeds])  # noqa: E731
        rh = lambda r: np.mean([r[s]["recall_headline"] for s in seeds])  # noqa: E731
        print(f"{cnd:26s} {rh(a):9.2f} ({fa(a):4.1f})  {rh(b):9.2f} ({fa(b):4.1f})")
    print("mean degradation  A_patthr %+.3f   B %+.3f" % (np.mean([v['v'] for v in dA.values()]), np.mean([v['v'] for v in dB.values()])))
    print("B minus A_patthr (positive = per-pattern models help beyond per-pattern thresholds):", paired(dB, dA, "v"))
    Path("data/audit/d106_ablation.json").write_text(json.dumps({"A_patthr_minus_B": paired(dB, dA, "v"), "per_condition": {c_: {a_: {str(k): v for k, v in d.items()} for a_, d in res[c_].items()} for c_ in res}}, indent=1, default=float))


if __name__ == "__main__":
    main()
