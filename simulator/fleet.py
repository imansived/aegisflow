"""All devices together. Proximity is computed here because it depends on everyone."""

from collections.abc import Iterator, Sequence
from datetime import datetime, timedelta, timezone

import numpy as np

from contracts.geo import from_local_m
from contracts.scenarios import Episode
from contracts.site import SITE_ORIGIN
from contracts.telemetry import Telemetry

from .config import TAG_DEVICE, TAG_PROFILE, SimConfig, child_rng
from .device import DeviceSim, Effect
from .profile import make_profile


class Fleet:
    def __init__(self, cfg: SimConfig, episodes: Sequence[Episode] = ()):
        self.cfg = cfg
        self.ids = cfg.device_ids()
        # Each device gets its own random streams, keyed by its index, so adding a device
        # does not change any other device's data.
        self.devices = [
            DeviceSim(make_profile(d, child_rng(cfg.seed, TAG_PROFILE, i)), child_rng(cfg.seed, TAG_DEVICE, i), cfg, cfg.start)
            for i, d in enumerate(self.ids)
        ]
        self.episodes_by_device: dict[str, list[Episode]] = {d: [] for d in self.ids}
        for ep in episodes:
            self.episodes_by_device[ep.device_id].append(ep)

    def _effects(self, device_id: str, t: datetime) -> list[Effect]:
        return [
            Effect(ep.episode_id, ep.modalities, ep.magnitude)
            for ep in self.episodes_by_device[device_id]
            if ep.start <= t < ep.end
        ]

    def ticks(self, duration: timedelta) -> Iterator[list[Telemetry]]:
        cfg = self.cfg
        dt = float(cfg.tick_s)
        n_ticks = int(duration.total_seconds() // cfg.tick_s)
        r2 = cfg.proximity_radius_m**2
        for k in range(n_ticks):
            t = cfg.start + timedelta(seconds=k * cfg.tick_s)
            readings = [d.step(t, dt, self._effects(d.p.device_id, t)) for d in self.devices]

            # Neighbours: pairwise distance between REPORTED positions (what a device could sense).
            xy = np.array([r.reported_xy for r in readings])
            d2 = ((xy[:, None, :] - xy[None, :, :]) ** 2).sum(-1)
            near = d2 < r2
            np.fill_diagonal(near, False)

            out: list[Telemetry] = []
            for i, r in enumerate(readings):
                lat, lon = from_local_m(r.reported_xy, SITE_ORIGIN)
                neighbours = [self.ids[j] for j in np.flatnonzero(near[i])] + r.rogue_neighbours
                out.append(
                    Telemetry(
                        device_id=self.ids[i],
                        timestamp=t.astimezone(timezone.utc),
                        latitude=round(lat, 7),  # ~1 cm; more digits are noise and bloat
                        longitude=round(lon, 7),
                        battery=r.battery,
                        cpu_usage=r.cpu_usage,
                        memory_usage=r.memory_usage,
                        temperature=r.temperature,
                        uptime_s=r.uptime_s,
                        network_connections=r.network_connections,
                        bytes_sent=r.bytes_sent,
                        bytes_received=r.bytes_received,
                        remote_endpoints=r.remote_endpoints,
                        nearby_devices=neighbours,
                    )
                )
            yield out

    def run(self, duration: timedelta) -> Iterator[Telemetry]:
        for batch in self.ticks(duration):
            yield from batch
