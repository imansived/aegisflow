"""Fusion functions: how four per-modality scores become one (manual §3.9).

All share one interface, `score(e) -> float in [0, 1]`, where `e` is the four *effective*
scores in the fixed order MODALITIES. That is what makes the comparison in Phase 5 fair:
every candidate sees exactly the same inputs.
"""

import math
from abc import ABC, abstractmethod
from collections.abc import Sequence

import numpy as np

from contracts.scenarios import Modality

MODALITIES: tuple[Modality, ...] = (Modality.SPATIAL, Modality.NETWORK, Modality.DEVICE, Modality.PROXIMITY)


class Fusion(ABC):
    name: str

    @abstractmethod
    def score(self, e: Sequence[float]) -> float: ...

    def score_batch(self, E: np.ndarray) -> np.ndarray:
        """Score many rows at once. Subclasses override with vectorised maths; tested equal to `score`."""
        return np.fromiter((self.score(row) for row in E), dtype=np.float64, count=len(E))

    def contributions(self, e: Sequence[float]) -> dict[Modality, float]:
        """Leave-one-out: how much does the score fall if this modality is silenced?

        Works for ANY fusion, which keeps the explanation layer independent of the maths. It is
        not a Shapley value: when modalities interact, the contributions need not sum to the
        score. For the additive weighted sum it is exact (w_i * e_i).
        """
        s = self.score(e)
        out = {}
        for i, m in enumerate(MODALITIES):
            without = list(e)
            without[i] = 0.0
            out[m] = max(0.0, s - self.score(without))
        return out


class MaxFusion(Fusion):
    """Any detector alone is enough. This is what independent detection does operationally."""

    name = "max"

    def score(self, e):
        return max(e)

    def score_batch(self, E):
        return E.max(axis=1)


class SingleSignal(Fusion):
    """One detector only: the spatial-only / network-only / device-only baselines."""

    def __init__(self, modality: Modality):
        self.idx = MODALITIES.index(modality)
        self.name = f"{modality.value}_only"

    def score(self, e):
        return e[self.idx]

    def score_batch(self, E):
        return E[:, self.idx].copy()


class WeightedSum(Fusion):
    """The spec's formula. Weights are normalised to sum to 1, so S is an average.

    Default weights are EQUAL, which is a documented placeholder, not a calibrated choice. A
    single detector at 1.0 gives only its weight, so this variant needs several detectors to
    agree before it can reach a high score.
    """

    name = "weighted_sum"

    def __init__(self, weights: dict[Modality, float] | None = None):
        w = [(weights or {}).get(m, 1.0) for m in MODALITIES]
        total = sum(w)
        self.w = [x / total for x in w]

    def score(self, e):
        return min(1.0, max(0.0, sum(w * x for w, x in zip(self.w, e))))

    def score_batch(self, E):
        return np.clip(E @ np.asarray(self.w), 0.0, 1.0)


class NoisyOr(Fusion):
    """S = 1 - prod(1 - e_i). An OR that also accumulates several weak signals.

    Assumes the signals are independent. If one benign cause moves several of them at once
    (a firmware update) it counts that cause several times: double counting (§3.9).
    """

    name = "noisy_or"

    def score(self, e):
        p = 1.0
        for x in e:
            p *= 1.0 - min(max(x, 0.0), 1.0)
        return 1.0 - p

    def score_batch(self, E):
        return 1.0 - np.prod(1.0 - np.clip(E, 0.0, 1.0), axis=1)


def pair_features(e: Sequence[float]) -> list[float]:
    """The four scores plus all six pairwise products (so interactions can be learned)."""
    f = list(e)
    for i in range(len(e)):
        for j in range(i + 1, len(e)):
            f.append(e[i] * e[j])
    return f


def pair_matrix(E: np.ndarray) -> np.ndarray:
    """Vectorised `pair_features` for many rows (same column order)."""
    n_mod = E.shape[1]
    cols = [E] + [(E[:, i] * E[:, j])[:, None] for i in range(n_mod) for j in range(i + 1, n_mod)]
    return np.hstack(cols)


class LearnedFusion(Fusion):
    """Logistic regression on the effective scores and their pairwise products.

    Fitted on LABELLED simulations from the training/validation seeds only. Because it sees
    labelled hard negatives it can learn that, say, network + device together WITHOUT
    spatial or proximity is benign, which a hand-set formula cannot. The output is a
    ranking-quality score, not a calibrated probability (§3.10).
    """

    name = "learned"

    def __init__(self) -> None:
        self.coef: np.ndarray | None = None
        self.intercept = 0.0

    def fit(self, E: np.ndarray, y: np.ndarray, C: float = 1.0) -> "LearnedFusion":
        from sklearn.linear_model import LogisticRegression

        X = pair_matrix(np.asarray(E, dtype=np.float64))
        # Attacks are rare, so weight classes; otherwise it learns to say "benign" always.
        lr = LogisticRegression(C=C, class_weight="balanced", max_iter=1000).fit(X, y)
        self.coef, self.intercept = lr.coef_[0].copy(), float(lr.intercept_[0])
        return self

    def contributions(self, e: Sequence[float]) -> dict[Modality, float]:
        """Each modality's share of the score, from the model's own additive structure.

        The logit is linear in the scores and their pairwise products, so each modality's part is exact:
        its own term plus HALF of every product term it appears in (the Shapley split for a pairwise
        polynomial). Positive parts are then scaled to sum to the score. The generic leave-one-out of
        `Fusion` fails here: this model saturates near 1, so removing one of several redundant signals
        barely moves the score and every signal would read ~0 (found in the Phase 8 demo, manual §4.9)."""
        if self.coef is None:
            raise RuntimeError("LearnedFusion must be fit() before use")
        x = np.asarray(e, dtype=np.float64)
        n = len(x)
        phi = self.coef[:n] * x
        k = n
        for i in range(n):
            for j in range(i + 1, n):
                half = 0.5 * self.coef[k] * x[i] * x[j]
                phi[i] += half
                phi[j] += half
                k += 1
        pos = np.maximum(phi, 0.0)
        total = float(pos.sum())
        if total <= 0.0:
            return {m: 0.0 for m in MODALITIES}
        s = self.score(e)
        return {m: float(s * pos[i] / total) for i, m in enumerate(MODALITIES)}

    def to_dict(self) -> dict:
        if self.coef is None:
            raise RuntimeError("nothing to save: LearnedFusion has not been fit")
        return {"coef": [float(x) for x in self.coef], "intercept": self.intercept}

    @classmethod
    def from_dict(cls, d: dict) -> "LearnedFusion":
        lf = cls()
        lf.coef, lf.intercept = np.asarray(d["coef"], dtype=np.float64), float(d["intercept"])
        return lf

    def score(self, e):
        if self.coef is None:
            raise RuntimeError("LearnedFusion must be fit() before use")
        z = float(np.dot(self.coef, pair_features(e)) + self.intercept)
        return 1.0 / (1.0 + math.exp(-max(min(z, 50.0), -50.0)))

    def score_batch(self, E):
        if self.coef is None:
            raise RuntimeError("LearnedFusion must be fit() before use")
        z = np.clip(pair_matrix(E) @ self.coef + self.intercept, -50.0, 50.0)
        return 1.0 / (1.0 + np.exp(-z))
