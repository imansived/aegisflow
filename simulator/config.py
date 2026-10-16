"""Simulation parameters and reproducible random streams."""

from dataclasses import dataclass, field
from datetime import datetime

import numpy as np

from contracts.site import SITE_TZ

# Stream tags: each purpose gets its own random stream so that changing one (say, adding a
# device) does not shift the numbers drawn by another. See manual §3.18.
TAG_DEVICE = 1
TAG_PLAN = 2
TAG_PROFILE = 3


def child_rng(seed: int, tag: int, index: int = 0) -> np.random.Generator:
    return np.random.default_rng(np.random.SeedSequence([seed, tag, index]))


@dataclass(frozen=True)
class SimConfig:
    seed: int = 1
    n_devices: int = 50
    # Monday 2026-10-05 00:00 site time.
    start: datetime = datetime(2026, 10, 5, 0, 0, tzinfo=SITE_TZ)
    tick_s: int = 5

    # Realism knobs ------------------------------------------------------------------
    gps_sigma_m: float = 3.0  # GPS jitter, 1 sigma
    walk_speed_mps: float = 1.4
    proximity_radius_m: float = 8.0  # "nearby" = within this distance (a BLE-like range)
    work_start_hour: int = 9
    work_end_hour: int = 18

    # Natural noise: benign oddities that exist with NO attack. Without these every
    # deviation would be an attack and the detectors' false-positive rate would be fake.
    p_cpu_spike_per_tick: float = 0.003
    p_novel_endpoint_per_tick: float = 0.002

    # Reserved id range for devices that are not in the fleet (rogue/unknown).
    rogue_id_range: tuple[int, int] = field(default=(900, 999))

    def device_ids(self) -> list[str]:
        if self.n_devices > 899:
            raise ValueError("n_devices must be <= 899 (900-999 are reserved for rogue ids)")
        return [f"D-{i:03d}" for i in range(1, self.n_devices + 1)]
