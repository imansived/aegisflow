"""Counters and latency percentiles for the processor."""

from dataclasses import dataclass, field

import numpy as np


@dataclass
class Metrics:
    processed: int = 0  # newly stored and handled
    duplicates: int = 0  # redelivered, already stored: acknowledged and skipped
    invalid: int = 0  # failed schema validation / plausibility -> dead-letter
    failed: int = 0  # handler/store raised: left pending to be retried
    dead_lettered: int = 0  # gave up after too many deliveries
    latencies_ms: list[float] = field(default_factory=list)

    def summary(self) -> dict:
        out = {
            "processed": self.processed,
            "duplicates": self.duplicates,
            "invalid": self.invalid,
            "failed": self.failed,
            "dead_lettered": self.dead_lettered,
        }
        if self.latencies_ms:
            a = np.asarray(self.latencies_ms)
            # Percentiles, not the mean: the tail is what an operator experiences (§3.15).
            out |= {f"p{q}_ms": round(float(np.percentile(a, q)), 2) for q in (50, 95, 99)}
        return out
