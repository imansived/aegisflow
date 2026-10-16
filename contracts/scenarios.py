"""Scenario definitions and the ground-truth label format (manual §3.13, §3.14).

Ground truth lives in `Episode` records written OUT OF BAND - never inside Telemetry.
Evaluation joins alerts to episodes by time overlap.

`ScenarioSpec.hypothesis` is what we *expect*. It is documentation, not an assertion the
code enforces: the spec insists expected responses be verified experimentally, not
hard-coded.
"""

from datetime import datetime
from enum import Enum

from pydantic import BaseModel, ConfigDict, Field, model_validator


class Modality(str, Enum):
    SPATIAL = "spatial"
    NETWORK = "network"
    DEVICE = "device"
    PROXIMITY = "proximity"


class ScenarioType(str, Enum):
    NORMAL = "normal"
    SPATIAL = "spatial"
    NETWORK = "network"
    HARDWARE = "hardware"
    PROXIMITY = "proximity"
    COMBINED = "combined"
    # Every touched signal stays individually below its own alert threshold. This is the
    # case the project's thesis claims to win, so it must be tested on purpose.
    WEAK_CORRELATED = "weak_correlated"
    # Benign episode that LOOKS like an attack: touches signals but is_malicious=False.
    HARD_NEGATIVE = "hard_negative"


class ScenarioSpec(BaseModel):
    model_config = ConfigDict(frozen=True)

    type: ScenarioType
    description: str
    modalities: frozenset[Modality]
    is_malicious: bool
    # Fraction of "how far from normal", 0 = indistinguishable, 1 = extreme.
    # Evaluation sweeps this rather than picking one convenient value.
    magnitude_range: tuple[float, float] = (0.2, 1.0)
    duration_s_range: tuple[int, int] = (60, 300)
    hypothesis: str = ""


SCENARIOS: dict[ScenarioType, ScenarioSpec] = {
    s.type: s
    for s in (
        ScenarioSpec(
            type=ScenarioType.NORMAL,
            description="Routine movement, known endpoints, normal drain, expected neighbours.",
            modalities=frozenset(),
            is_malicious=False,
            magnitude_range=(0.0, 0.0),
            hypothesis="Low scores; alerts here are false positives.",
        ),
        ScenarioSpec(
            type=ScenarioType.SPATIAL,
            description="Device enters a restricted zone.",
            modalities=frozenset({Modality.SPATIAL}),
            is_malicious=True,
            hypothesis="Spatial detector fires; others stay quiet.",
        ),
        ScenarioSpec(
            type=ScenarioType.NETWORK,
            description="Contact with unfamiliar endpoints, or a traffic spike.",
            modalities=frozenset({Modality.NETWORK}),
            is_malicious=True,
            hypothesis="Network detector fires; others stay quiet.",
        ),
        ScenarioSpec(
            type=ScenarioType.HARDWARE,
            description="Abnormal battery drain and/or CPU usage.",
            modalities=frozenset({Modality.DEVICE}),
            is_malicious=True,
            hypothesis="Device detector fires; others stay quiet.",
        ),
        ScenarioSpec(
            type=ScenarioType.PROXIMITY,
            description="Unexpected nearby devices.",
            modalities=frozenset({Modality.PROXIMITY}),
            is_malicious=True,
            hypothesis="Proximity detector fires; others stay quiet.",
        ),
        ScenarioSpec(
            type=ScenarioType.COMBINED,
            description="Several modalities deviate close together.",
            modalities=frozenset(
                {Modality.SPATIAL, Modality.NETWORK, Modality.DEVICE, Modality.PROXIMITY}
            ),
            is_malicious=True,
            hypothesis="Combined score exceeds any single detector's score. TO BE VERIFIED.",
        ),
        ScenarioSpec(
            type=ScenarioType.WEAK_CORRELATED,
            description="Several modalities deviate mildly; each alone is below its threshold.",
            modalities=frozenset({Modality.SPATIAL, Modality.NETWORK, Modality.DEVICE}),
            is_malicious=True,
            magnitude_range=(0.1, 0.4),
            hypothesis="Single detectors miss it; fusion may catch it. This is the key test.",
        ),
        ScenarioSpec(
            type=ScenarioType.HARD_NEGATIVE,
            description=(
                "Benign look-alike, e.g. a firmware update (traffic + CPU + drain) or an "
                "authorised technician visit to the server room."
            ),
            modalities=frozenset({Modality.NETWORK, Modality.DEVICE}),
            is_malicious=False,
            hypothesis="Should NOT alert. A fusion model that alerts here is double counting.",
        ),
    )
}


class Episode(BaseModel):
    """One injected span of behaviour: the ground-truth label."""

    model_config = ConfigDict(frozen=True)

    episode_id: str
    device_id: str
    scenario: ScenarioType
    start: datetime
    end: datetime
    modalities: frozenset[Modality]
    magnitude: float = Field(ge=0, le=1)
    is_malicious: bool
    seed: int  # the run seed, so the episode can be reproduced
    # Sub-type within a scenario, e.g. HARD_NEGATIVE: "firmware_update" | "authorized_visit".
    variant: str = ""

    @model_validator(mode="after")
    def _valid_span(self) -> "Episode":
        if self.start.tzinfo is None or self.end.tzinfo is None:
            raise ValueError("episode times must be timezone-aware")
        if self.end <= self.start:
            raise ValueError("episode must end after it starts")
        return self
