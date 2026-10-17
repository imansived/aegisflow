from dataclasses import dataclass, field
from datetime import datetime

from contracts.scenarios import Modality
from contracts.site import Zone
from contracts.telemetry import Telemetry


@dataclass(frozen=True)
class DetectorResult:
    modality: Modality
    score: float  # 0 = ordinary, 1 = extreme. A ranking, NOT a probability (§3.10).
    learning: bool  # True if at least one baseline is still in cold start
    evidence: dict[str, float] = field(default_factory=dict)  # feature -> z (or rule flag)
    # Same detector, but weak evidence is kept (stats.soft_ramp) instead of zeroed below z = 2.
    # Baseline learning and alerting use `score`; `soft` exists for the §5.5 experiment.
    soft: float = 0.0


@dataclass(frozen=True)
class Scores:
    """Everything the detectors concluded about one telemetry event."""

    device_id: str
    ts: datetime
    bucket: str
    results: dict[Modality, DetectorResult]
    # Raw engineered features, kept so the early-fusion Isolation Forest baseline (Phase 5)
    # can be trained on exactly what the detectors saw.
    features: dict[str, float]

    def score(self, m: Modality) -> float:
        return self.results[m].score

    def soft(self, m: Modality) -> float:
        return self.results[m].soft

    @property
    def learning(self) -> bool:
        return any(r.learning for r in self.results.values())


@dataclass(frozen=True)
class Context:
    """Per-event facts computed once and shared by all four detectors."""

    t: Telemetry
    now_s: float  # event time as epoch seconds
    bucket: str  # "work" | "off"
    dt_s: float | None  # since this device's previous event
    zone: Zone | None
    speed_mps: float | None
