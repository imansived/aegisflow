"""A device's identity: the stable habits that define its 'normal'."""

from dataclasses import dataclass

import numpy as np

from contracts.site import ZONES, ZoneKind

# One shared pool of endpoints. Anomalous contacts are drawn from the SAME pool (just ones
# this device never uses), so an anomalous endpoint cannot be spotted by its format - a
# generator artifact a detector could exploit without learning anything (manual §3.13).
ENDPOINT_POOL: tuple[str, ...] = tuple(
    [f"10.0.0.{i}" for i in range(1, 31)] + [f"198.51.100.{i}" for i in range(1, 11)]
)

ALLOWED_ZONES = tuple(z.name for z in ZONES if z.kind is ZoneKind.ALLOWED)


@dataclass(frozen=True)
class DeviceProfile:
    device_id: str
    home_zone: str
    zone_weights: dict[str, float]  # probability of choosing each allowed zone next
    dwell_min: dict[str, float]  # mean minutes per visit
    known_endpoints: tuple[str, ...]
    drain_pct_per_h: float
    cpu_base: float
    mem_base: float
    conn_mean: float
    bytes_sent_median: float  # per 5 s
    bytes_recv_median: float

    @property
    def unusual_zone(self) -> str:
        """The allowed zone this device visits least: where 'unusual location' sends it."""
        return min(self.zone_weights, key=self.zone_weights.get)


def make_profile(device_id: str, rng: np.random.Generator) -> DeviceProfile:
    home = str(rng.choice(["Office", "Lab"], p=[0.7, 0.3]))
    others = [z for z in ALLOWED_ZONES if z != home]
    split = rng.dirichlet([2.0] * len(others)) * 0.35
    weights = {home: 0.65, **{z: float(w) for z, w in zip(others, split)}}
    dwell = {home: 90.0, "MeetingRoom": 30.0, "Lab": 45.0, "Office": 90.0}
    known = tuple(rng.choice(ENDPOINT_POOL, size=6, replace=False))
    return DeviceProfile(
        device_id=device_id,
        home_zone=home,
        zone_weights=weights,
        dwell_min=dwell,
        known_endpoints=known,
        drain_pct_per_h=float(rng.uniform(5.0, 9.0)),
        cpu_base=float(rng.uniform(20, 45)),
        mem_base=float(rng.uniform(35, 65)),
        conn_mean=float(rng.uniform(5, 12)),
        bytes_sent_median=float(rng.uniform(25_000, 60_000)),
        bytes_recv_median=float(rng.uniform(50_000, 110_000)),
    )
