import numpy as np
import pytest

from ml.validate import Combined, boot_ci, evaluate, paired_diff_ci, summarise


def make_combined():
    """Seed 1: 40k ordinary rows (threshold data). Seed 2: 40k ordinary rows + two episodes.

    Episode 0 scores 0 for its first 5 ticks then 1.0 (should be detected, 25 s after start).
    Episode 1 scores 0.1 throughout (should be missed).
    """
    rng = np.random.default_rng(0)
    n1, n2, k = 40_000, 40_000, 20  # enough rows that a 99.9% threshold is statistically stable
    ts = np.concatenate([np.arange(n1) * 5.0, np.arange(n2) * 5.0, 10_000 + np.arange(2 * k) * 5.0])
    seed = np.concatenate([np.full(n1, 1), np.full(n2, 2), np.full(2 * k, 2)]).astype(np.int16)
    n = len(ts)
    score = np.concatenate([rng.random(n1), rng.random(n2), [0.0] * 5 + [1.0] * (k - 5), [0.1] * k])
    episode = np.full(n, -1, np.int32)
    episode[n1 + n2: n1 + n2 + k] = 0
    episode[n1 + n2 + k:] = 1
    label = (episode >= 0).astype(np.int8)
    usable = np.ones(n, bool)
    ordinary = episode < 0
    meta = [
        {"seed": 2, "scenario": "weak_correlated", "variant": "", "magnitude": 0.1, "malicious": True, "start": 10_000.0},
        {"seed": 2, "scenario": "weak_correlated", "variant": "", "magnitude": 0.1, "malicious": True, "start": 10_000.0 + k * 5.0},
    ]
    c = Combined(seed, ts, np.zeros(n, np.int32), np.zeros((n, 4)), np.zeros((n, 4)), np.zeros((n, 3), np.float32),
                 label, usable, ordinary, episode, meta)
    return c, score


FOLD = [{"name": "X", "fit": [1], "thr": [1], "eval": [2]}]


def test_evaluate_detects_by_episode_and_measures_time_to_detect():
    c, score = make_combined()
    res = evaluate(c, {"X": {"s": lambda rows: score[rows]}}, folds=FOLD)["s"]
    assert res["ev"].tolist() == [True, True]
    assert res["det"].tolist() == [True, False]
    assert res["ttd"][0] == pytest.approx(25.0) and np.isnan(res["ttd"][1])
    assert 0.0 <= res["ap"][0] <= 1.0 and res["ord"] == 40_000
    # Expected 0.1% (40 of 40 000); allow generous sampling noise.
    assert 0.0003 <= res["fp"] / res["ord"] <= 0.0025


def test_summarise_reports_recall_with_a_confidence_interval():
    c, score = make_combined()
    res = evaluate(c, {"X": {"s": lambda rows: score[rows]}}, folds=FOLD)
    t = summarise(c, res, np.random.default_rng(0))["s"]["weak_correlated"]
    assert t["n"] == 2 and t["recall"] == 0.5 and 0.0 <= t["lo"] <= 0.5 <= t["hi"] <= 1.0


def test_episodes_outside_evaluation_seeds_are_not_counted():
    c, score = make_combined()
    folds = [{"name": "X", "fit": [2], "thr": [2], "eval": [1]}]  # seed 1 has no episodes
    res = evaluate(c, {"X": {"s": lambda rows: score[rows]}}, folds=folds)["s"]
    assert not res["ev"].any()


def test_bootstrap_ci_brackets_the_mean_and_paired_diff_detects_a_real_gap():
    rng = np.random.default_rng(1)
    x = (rng.random(200) < 0.7).astype(float)
    mean, lo, hi = boot_ci(x, rng)
    assert lo <= mean <= hi and hi - lo < 0.2
    a = np.ones(100, bool)
    b = np.zeros(100, bool)
    d, lo, hi = paired_diff_ci(a, b, rng)
    assert d == 1.0 and lo == 1.0
    same = (rng.random(100) < 0.5)
    d, lo, hi = paired_diff_ci(same, same, rng)
    assert d == 0.0 and lo == 0.0 and hi == 0.0
