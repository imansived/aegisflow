"""The correlation engine: turn per-event detector scores into one explainable score.

"Several deviations close together" needs a definition of *close* (manual §3.7). Here each
modality keeps a DECAYED PEAK per device:

    e_m(t) = max( s_m(t),  e_m(t_prev) * 0.5 ** (dt / half_life) )

So a spatial alert at 14:30:00 still counts as 0.5 of itself at 14:31:00 (half-life 60 s),
and a network alert at 14:30:40 can combine with it. No window boundaries to straddle.
"""

from dataclasses import dataclass, field
from datetime import datetime

from backend.detection.types import Scores
from contracts.scenarios import Modality

from .fusion import MODALITIES, Fusion
from .thresholds import FULL, Operating, ThresholdTable, pattern_of


@dataclass(frozen=True)
class CorrelationConfig:
    half_life_s: float = 60.0  # hyperparameter: tuned on validation seeds, then frozen
    # Which detector score feeds fusion: "hard" (z <= 2 -> 0) or "soft" (keeps weak evidence).
    # The frozen design (manual §5.5-5.7) is soft + 1 s; the default stays "hard" for older code.
    score_type: str = "hard"
    # Severity bands from the spec (illustrative, configurable; manual §3.16).
    low: float = 0.30
    medium: float = 0.60
    high: float = 0.80
    # Only compute leave-one-out contributions above this score (they cost 4 extra evaluations).
    explain_above: float = 0.30


# The design frozen by validation experiments E1/E2 and used for the final test (manual §5.7).
FROZEN_CORRELATION = CorrelationConfig(half_life_s=1.0, score_type="soft")


def severity(score: float, cfg: CorrelationConfig) -> str:
    if score >= cfg.high:
        return "high"
    if score >= cfg.medium:
        return "medium"
    if score >= cfg.low:
        return "low"
    return "normal"


@dataclass(frozen=True)
class Fused:
    device_id: str
    ts: datetime
    score: float
    severity: str
    effective: dict[Modality, float]  # decayed-peak score per modality
    raw: dict[Modality, float]  # this event's detector scores
    contributions: dict[Modality, float]  # leave-one-out, empty below explain_above
    evidence: dict[Modality, dict[str, float]] = field(default_factory=dict)
    learning: bool = False
    fusion: str = ""
    # Observability (D106/D107). `unavailable` = signals the device reported as unavailable this tick. A fused score
    # computed with signals missing is never "normal" evidence: `observability` says how much was actually seen, and
    # `alertable` is False when there is no validated threshold for this combination (the conservative fallback).
    unavailable: frozenset[Modality] = frozenset()
    pattern: int = FULL
    # R3. Of the signals not observed: `unsupported` = the device never declared them (not expected, not a fault);
    # `missing` = declared but reported null; `recovering` = declared and back, judgment still withheld. `unavailable`
    # is all three together and drives the threshold pattern; `observability` is judged against what was declared.
    unsupported: frozenset[Modality] = frozenset()
    missing: frozenset[Modality] = frozenset()
    recovering: frozenset[Modality] = frozenset()
    observability: str = "full"  # "full" | "degraded" | "blind"
    alertable: bool = True
    operating: Operating | None = None  # the validated thresholds that judged this tick, when a table is in use
    fallback: str = ""  # why no threshold applied, when alertable is False


class _DeviceState:
    def __init__(self) -> None:
        self.last_ts: float | None = None
        self.e = [0.0] * len(MODALITIES)
        self.ev: list[dict[str, float]] = [{} for _ in MODALITIES]  # evidence at each peak


class CorrelationEngine:
    def __init__(self, fusion: Fusion, cfg: CorrelationConfig | None = None, table: ThresholdTable | None = None):
        self.fusion = fusion
        self.cfg = cfg or CorrelationConfig()
        self.table = table
        self._state: dict[str, _DeviceState] = {}

    def reset_device(self, device_id: str) -> None:
        """Drop a device's decayed-peak state (its clock jumped backwards, so the decay times are meaningless)."""
        self._state.pop(device_id, None)

    def update(self, scores: Scores) -> Fused:
        st = self._state.setdefault(scores.device_id, _DeviceState())
        now = scores.ts.timestamp()
        dt = 0.0 if st.last_ts is None else max(0.0, now - st.last_ts)
        st.last_ts = now
        decay = 0.5 ** (dt / self.cfg.half_life_s)

        raw = {}
        for i, m in enumerate(MODALITIES):
            res = scores.results[m]
            if not res.available:
                raw[m] = 0.0  # placeholder; availability is carried separately (never read it as a normal score)
                st.e[i] = st.e[i] * decay  # no new evidence: what was held simply fades
                continue
            v = res.soft if self.cfg.score_type == "soft" else res.score
            raw[m] = v
            held = st.e[i] * decay
            if v >= held:
                st.e[i] = v
                st.ev[i] = dict(res.evidence)  # remember WHY this is the current peak
            else:
                st.e[i] = held

        s = self.fusion.score(st.e)
        contrib = self.fusion.contributions(st.e) if s >= self.cfg.explain_above else {}

        lost = scores.unavailable
        unsupported = scores.unsupported
        missing = scores.reported_null - unsupported
        recovering = lost - scores.reported_null - unsupported
        expected_lost = missing | recovering  # what a device that declared these signals should be showing and is not
        declared = set(MODALITIES) - unsupported
        pattern = pattern_of(lost)
        operating: Operating | None = None
        alertable, fallback = True, ""
        if self.table is not None:
            operating = self.table.lookup(pattern)
            if operating is None:
                alertable, fallback = False, self.table.fallback_reason(pattern)
        elif lost:
            # No calibrated table: never judge a partly observed device with the complete-signal threshold.
            alertable, fallback = False, "no availability thresholds are loaded"
        if not alertable:
            sev = "normal"  # a placeholder: "cannot judge", reported through `observability`, not a clean bill of health
        elif operating is not None:
            sev = operating.severity(s)
        else:
            sev = severity(s, self.cfg)
        return Fused(
            device_id=scores.device_id,
            ts=scores.ts,
            score=s,
            severity=sev,
            effective={m: st.e[i] for i, m in enumerate(MODALITIES)},
            raw=raw,
            contributions=contrib,
            evidence={m: st.ev[i] for i, m in enumerate(MODALITIES) if st.e[i] > 0.0},
            learning=scores.learning,
            fusion=self.fusion.name,
            unavailable=lost,
            pattern=pattern,
            unsupported=unsupported,
            missing=missing,
            recovering=recovering,
            observability="full" if not expected_lost else "blind" if declared <= expected_lost else "degraded",
            alertable=alertable,
            operating=operating,
            fallback=fallback,
        )
