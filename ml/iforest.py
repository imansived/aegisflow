"""Early fusion: ONE Isolation Forest over all features at once (manual §3.5, §3.9).

This is the control the late-fusion engine must beat to claim that the *architecture* adds
anything over simply feeding every feature to a strong generic model.

Fairness rules, so the comparison means something:
  * Inputs are the SAME per-device-standardised evidence the detectors use (z-scores, not raw
    values; raw values would ignore each device's own baseline).
  * It gets the SAME time memory: the decayed-peak hold with the same half-life.
  * It is trained UNSUPERVISED on all usable rows, with no labels, so the few attack rows are
    in its training data as they would be in real life. (Trained on clean data only, it could
    not see rule features such as `in_restricted`: a column that is always 0 in training gives
    a tree nothing to split on, so a 1 at test time is not isolated. Found in Phase 4, §4.4.)
"""

import numpy as np

from .dataset import hold_offline


class EarlyFusionIF:
    name = "early_fusion_iforest"

    def __init__(self, half_life_s: float = 60.0, n_estimators: int = 200, seed: int = 0):
        self.half_life_s = half_life_s
        self.n_estimators = n_estimators
        self.seed = seed
        self._model = None
        self._ref: np.ndarray | None = None  # sorted anomaly scores on the training data

    def _inputs(self, ts, device, feat):
        # Only "bigger = more unusual" is meaningful for these features, so clip negatives.
        return hold_offline(ts, device, np.clip(feat, 0.0, None), self.half_life_s)

    def fit(self, ts: np.ndarray, device: np.ndarray, feat: np.ndarray, train_mask: np.ndarray) -> "EarlyFusionIF":
        return self.fit_held(self._inputs(ts, device, feat)[train_mask])

    def score(self, ts: np.ndarray, device: np.ndarray, feat: np.ndarray) -> np.ndarray:
        """Percentile of the training anomaly scores, in [0, 1]. A ranking, not a probability."""
        return self.score_held(self._inputs(ts, device, feat))

    # Variants that take inputs already passed through the time-memory hold, so callers that
    # have many seeds can compute the (slow) hold once.
    def fit_held(self, X: np.ndarray) -> "EarlyFusionIF":
        from sklearn.ensemble import IsolationForest

        self._model = IsolationForest(n_estimators=self.n_estimators, random_state=self.seed).fit(X)
        self._ref = np.sort(-self._model.score_samples(X))  # higher = more anomalous
        return self

    def score_held(self, X: np.ndarray) -> np.ndarray:
        a = -self._model.score_samples(X)
        return np.searchsorted(self._ref, a, side="right") / len(self._ref)
