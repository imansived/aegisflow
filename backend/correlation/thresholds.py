"""Availability-aware alert thresholds (manual §5.11: D106 found the gain, D107 validates this implementation).

The fusion is fitted on four signals. When a device reports one as unavailable, its column is held at 0 and the fused
score is compared with a threshold calibrated for exactly that combination of available signals, so that the false
alert rate stays on budget instead of silently falling (and recall with it). The zero is a stated, calibrated input
for that pattern, not an assumption that the missing signal was normal.

A pattern with no validated threshold gets a conservative fallback, never an invented number: no anomaly alert is
raised for it and the device is reported as Degraded with the reason (an unvalidated alert rate is worse than an
honest "cannot judge"). The same applies when no signal at all is available.
"""

import json
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path

from contracts.scenarios import Modality

BIT = {Modality.SPATIAL: 0, Modality.NETWORK: 1, Modality.DEVICE: 2, Modality.PROXIMITY: 3}
FULL = 15  # all four signals available


def pattern_of(unavailable: Iterable[Modality]) -> int:
    """Bitmask of AVAILABLE signals: bit set = available. 15 = complete, 0 = nothing available."""
    lost = set(unavailable)
    return sum(1 << b for m, b in BIT.items() if m not in lost)


def describe(pattern: int) -> str:
    return "+".join(m.value for m, b in BIT.items() if pattern >> b & 1) or "none"


@dataclass(frozen=True)
class Operating:
    raise_at: float
    clear_at: float
    false_per_day: float | None = None  # false alert onsets per device-day measured at calibration
    rows: int | None = None  # ordinary rows the calibration used

    def bands(self) -> tuple[float, float, float]:
        """(low, medium, high) severity bands, derived exactly as the runtime derives them from the alert thresholds."""
        return 0.5 * self.clear_at, self.clear_at, self.raise_at

    def severity(self, score: float) -> str:
        low, medium, high = self.bands()
        return "high" if score >= high else "medium" if score >= medium else "low" if score >= low else "normal"


class ThresholdTable:
    """Validated operating points by availability pattern. Only patterns that were calibrated AND reached the
    alert budget are present; anything else is unknown and takes the conservative fallback."""

    def __init__(self, entries: dict[int, Operating], meta: dict | None = None):
        self.entries = {p: o for p, o in entries.items() if 0 < p <= FULL}
        self.meta = meta or {}

    def lookup(self, pattern: int) -> Operating | None:
        return self.entries.get(pattern)

    def fallback_reason(self, pattern: int) -> str:
        if pattern == 0:
            return "no signal is available"
        return f"no validated threshold for the available signals ({describe(pattern)})"

    @classmethod
    def from_dict(cls, d: dict) -> "ThresholdTable":
        entries = {}
        for k, v in d["patterns"].items():
            if v.get("validated"):
                entries[int(k)] = Operating(float(v["raise_at"]), float(v["clear_at"]), v.get("false_per_day"), v.get("rows"))
        return cls(entries, {k: v for k, v in d.items() if k != "patterns"})

    @classmethod
    def load(cls, path: Path) -> "ThresholdTable | None":
        try:
            return cls.from_dict(json.loads(Path(path).read_text()))
        except (OSError, ValueError, KeyError):
            return None
