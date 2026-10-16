"""One simulated device: routine behaviour, plus anomaly 'effects' layered on top.

Anomalies are not special rows. They are EFFECTS that change how the device behaves while
an episode is active (manual §3.18). When the effect ends the device simply resumes its
routine, which is what makes the 'restore normal behaviour' demo step work.
"""

from dataclasses import dataclass
from datetime import datetime, timedelta
from math import hypot

import numpy as np

from contracts.scenarios import Modality
from contracts.site import SITE_TZ, ZONES_BY_NAME, ZoneKind
from contracts.geo import to_local_m
from contracts.site import SITE_ORIGIN

from .config import SimConfig
from .profile import ENDPOINT_POOL, DeviceProfile


@dataclass(frozen=True)
class Effect:
    episode_id: str
    modalities: frozenset[Modality]
    magnitude: float


@dataclass
class Reading:
    """What a device produced this tick, before the fleet adds lat/lon and neighbours."""

    true_xy: tuple[float, float]
    reported_xy: tuple[float, float]
    battery: float
    cpu_usage: float
    memory_usage: float
    temperature: float
    uptime_s: int
    network_connections: int
    bytes_sent: int
    bytes_received: int
    remote_endpoints: list[str]
    rogue_neighbours: list[str]


def _zone_bounds(name: str) -> tuple[float, float, float, float]:
    xs, ys = zip(*(to_local_m(p, SITE_ORIGIN) for p in ZONES_BY_NAME[name].polygon))
    return min(xs), min(ys), max(xs), max(ys)


class DeviceSim:
    def __init__(self, profile: DeviceProfile, rng: np.random.Generator, cfg: SimConfig, t0: datetime):
        self.p = profile
        self.rng = rng
        self.cfg = cfg
        self.zone = profile.home_zone
        self.pos = self._point_in(self.zone)
        self.target = self.pos
        self.dwell_until = t0
        self.battery = float(rng.uniform(60, 95))
        self.charging = False
        self.cpu = profile.cpu_base
        self.cpu_spike = 0.0
        self.mem = profile.mem_base
        self.uptime_s = int(rng.integers(3_600, 7 * 86_400))
        self._spatial_override = False
        self._rogue_ids: dict[str, list[str]] = {}

    # ---- geometry ----------------------------------------------------------------------

    def _point_in(self, zone: str) -> tuple[float, float]:
        x0, y0, x1, y1 = _zone_bounds(zone)
        mx, my = (x1 - x0) * 0.2, (y1 - y0) * 0.2  # stay clear of walls: GPS jitter is ~3 m
        return (
            float(self.rng.uniform(x0 + mx, x1 - mx)),
            float(self.rng.uniform(y0 + my, y1 - my)),
        )

    def _clamp(self, xy: tuple[float, float], zone: str) -> tuple[float, float]:
        x0, y0, x1, y1 = _zone_bounds(zone)
        mx, my = (x1 - x0) * 0.2, (y1 - y0) * 0.2
        return (min(max(xy[0], x0 + mx), x1 - mx), min(max(xy[1], y0 + my), y1 - my))

    def _move(self, dt: float) -> None:
        dx, dy = self.target[0] - self.pos[0], self.target[1] - self.pos[1]
        dist = hypot(dx, dy)
        step = self.cfg.walk_speed_mps * dt
        if dist <= step:
            self.pos = self.target
            # Arrived: fidget a little inside the zone so the device is not perfectly still.
            if self.rng.random() < 0.15:
                jitter = (self.pos[0] + self.rng.normal(0, 3), self.pos[1] + self.rng.normal(0, 3))
                self.target = self._clamp(jitter, self.zone)
        else:
            self.pos = (self.pos[0] + dx / dist * step, self.pos[1] + dy / dist * step)

    # ---- routine -----------------------------------------------------------------------

    def _working(self, t: datetime) -> bool:
        local = t.astimezone(SITE_TZ)
        return local.weekday() < 5 and self.cfg.work_start_hour <= local.hour < self.cfg.work_end_hour

    def _choose_zone(self, t: datetime) -> None:
        if self._working(t):
            names = list(self.p.zone_weights)
            probs = np.array([self.p.zone_weights[n] for n in names])
            self.zone = str(self.rng.choice(names, p=probs / probs.sum()))
            mean = self.p.dwell_min[self.zone]
            dwell_s = float(self.rng.exponential(mean * 60)) + 30
        else:
            self.zone = self.p.home_zone
            dwell_s = 3600.0
        self.target = self._point_in(self.zone)
        self.dwell_until = t + timedelta(seconds=dwell_s)

    # ---- one tick ----------------------------------------------------------------------

    def step(self, t: datetime, dt: float, effects: list[Effect]) -> Reading:
        cfg, p, rng = self.cfg, self.p, self.rng
        working = self._working(t)
        activity = 1.0 if working else 0.15

        def eff(mod: Modality) -> float:
            return max((e.magnitude for e in effects if mod in e.modalities), default=0.0)

        m_spatial = eff(Modality.SPATIAL)
        m_network = eff(Modality.NETWORK)
        m_device = eff(Modality.DEVICE)

        # --- where am I going? ---
        spatial_active = any(Modality.SPATIAL in e.modalities for e in effects)
        if spatial_active and not self._spatial_override:
            self._spatial_override = True
            # Strong deviation enters the restricted zone; mild deviation only visits an
            # allowed zone this device rarely uses ("unusual", not "forbidden").
            restricted = [z.name for z in ZONES_BY_NAME.values() if z.kind is ZoneKind.RESTRICTED]
            self.zone = restricted[0] if m_spatial >= 0.5 else p.unusual_zone
            self.target = self._point_in(self.zone)
            self.dwell_until = datetime.max.replace(tzinfo=t.tzinfo)
        elif not spatial_active and self._spatial_override:
            self._spatial_override = False
            self.dwell_until = t  # resume routine immediately
        if t >= self.dwell_until:
            self._choose_zone(t)
        self._move(dt)

        reported = (
            self.pos[0] + rng.normal(0, cfg.gps_sigma_m),
            self.pos[1] + rng.normal(0, cfg.gps_sigma_m),
        )

        # --- device health ---
        hours = dt / 3600
        if self.battery < 20:
            self.charging = True
        elif self.battery > 90 and working:
            self.charging = False
        charging = self.charging or not working
        drain = p.drain_pct_per_h * (0.4 + 0.6 * activity)
        delta = (12.0 if charging else -drain) * hours
        delta -= p.drain_pct_per_h * 4.0 * m_device * hours  # injected extra drain
        self.battery = float(np.clip(self.battery + delta + rng.normal(0, 0.01), 0, 100))

        self.cpu_spike *= 0.8
        if rng.random() < cfg.p_cpu_spike_per_tick:
            self.cpu_spike += float(rng.uniform(20, 40))
        cpu_target = p.cpu_base * (0.4 + 0.6 * activity) + self.cpu_spike + 45.0 * m_device
        self.cpu = float(np.clip(self.cpu + 0.3 * (cpu_target - self.cpu) + rng.normal(0, 2.5), 0, 100))
        self.mem = float(np.clip(self.mem + 0.05 * (p.mem_base - self.mem) + rng.normal(0, 0.5), 0, 100))
        temp = 28 + 0.25 * self.cpu + 8.0 * m_device + rng.normal(0, 0.8)
        self.uptime_s += int(dt)

        # --- network ---
        conns = int(rng.poisson(p.conn_mean * (0.3 + 0.7 * activity))) + int(round(10 * m_network))
        scale = dt / 5.0
        sent = rng.lognormal(np.log(p.bytes_sent_median * scale * activity + 1), 0.4)
        recv = rng.lognormal(np.log(p.bytes_recv_median * scale * activity + 1), 0.4)
        sent *= 1 + 14 * m_network  # exfiltration-like upload
        recv *= 1 + 4 * m_network
        endpoints = list(rng.choice(p.known_endpoints, size=min(len(p.known_endpoints), 1 + int(rng.integers(0, 3))), replace=False))
        if rng.random() < cfg.p_novel_endpoint_per_tick:
            endpoints.append(self._unfamiliar_endpoints(1)[0])  # benign novelty
        if m_network > 0:
            endpoints += self._unfamiliar_endpoints(1 + int(round(3 * m_network)))

        # --- proximity: rogue (non-fleet) devices that are physically present ---
        rogues: list[str] = []
        for e in effects:
            if Modality.PROXIMITY in e.modalities:
                if e.episode_id not in self._rogue_ids:
                    lo, hi = cfg.rogue_id_range
                    n = 1 + int(round(2 * e.magnitude))
                    ids = rng.choice(np.arange(lo, hi + 1), size=n, replace=False)
                    self._rogue_ids[e.episode_id] = [f"D-{int(i):03d}" for i in ids]
                rogues += self._rogue_ids[e.episode_id]

        return Reading(
            true_xy=self.pos,
            reported_xy=reported,
            battery=round(self.battery, 2),
            cpu_usage=round(self.cpu, 1),
            memory_usage=round(self.mem, 1),
            temperature=round(float(temp), 1),
            uptime_s=self.uptime_s,
            network_connections=conns,
            bytes_sent=int(sent),
            bytes_received=int(recv),
            remote_endpoints=sorted(set(endpoints)),
            rogue_neighbours=rogues,
        )

    def _unfamiliar_endpoints(self, n: int) -> list[str]:
        unknown = [e for e in ENDPOINT_POOL if e not in self.p.known_endpoints]
        return list(self.rng.choice(unknown, size=min(n, len(unknown)), replace=False))
