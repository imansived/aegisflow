"""Supervised early fusion: the control that separates 'late vs early' from 'labels vs none'.

`LearnedFusion` (late fusion) is trained on attack labels. The unsupervised Isolation Forest is
not. If late fusion beat only the forest, nobody could tell whether the ARCHITECTURE helped or
simply having labels did. This model gets the same labels and the same inputs as the forest
(per-device z-scores with the same time memory) but combines them in one model rather than
per-modality detectors plus a fusion rule.
"""

import numpy as np

from .dataset import hold_offline


class EarlyFusionSupervised:
    name = "early_fusion_supervised"

    def __init__(self, half_life_s: float = 60.0, seed: int = 0):
        self.half_life_s = half_life_s
        self.seed = seed
        self._model = None

    def _inputs(self, ts, device, feat):
        return hold_offline(ts, device, np.clip(feat, 0.0, None), self.half_life_s)

    def fit(self, ts, device, feat, y, mask) -> "EarlyFusionSupervised":
        return self.fit_held(self._inputs(ts, device, feat)[mask], np.asarray(y)[mask])

    def score(self, ts, device, feat) -> np.ndarray:
        return self.score_held(self._inputs(ts, device, feat))

    def fit_held(self, X: np.ndarray, y: np.ndarray) -> "EarlyFusionSupervised":
        from sklearn.ensemble import HistGradientBoostingClassifier

        # Attacks are ~1% of rows; weight classes equally or the model learns "never attack".
        w = np.where(y == 1, 0.5 / max(1, (y == 1).sum()), 0.5 / max(1, (y == 0).sum())) * len(y)
        self._model = HistGradientBoostingClassifier(random_state=self.seed, max_iter=150).fit(X, y, sample_weight=w)
        return self

    def score_held(self, X: np.ndarray) -> np.ndarray:
        return self._model.predict_proba(X)[:, 1]
