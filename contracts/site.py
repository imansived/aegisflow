"""The simulated site: a small campus with named zones.

Coordinates are around the spec's example location (18.5204, 73.8567). One thousandth of a
degree of latitude is about 111 m, so every zone here is a few tens of metres across.
"""

from dataclasses import dataclass
from enum import Enum

from .geo import LatLon, point_in_polygon

SITE_ORIGIN: LatLon = (18.5204, 73.8567)


class ZoneKind(str, Enum):
    ALLOWED = "allowed"
    RESTRICTED = "restricted"


@dataclass(frozen=True)
class Zone:
    name: str
    kind: ZoneKind
    polygon: tuple[LatLon, ...]

    def contains(self, point: LatLon) -> bool:
        return point_in_polygon(point, list(self.polygon))

    @property
    def center(self) -> LatLon:
        lats = [p[0] for p in self.polygon]
        lons = [p[1] for p in self.polygon]
        return (sum(lats) / len(lats), sum(lons) / len(lons))


def _box(south: float, west: float, north: float, east: float) -> tuple[LatLon, ...]:
    return ((south, west), (south, east), (north, east), (north, west))


# Layout (north is up). Zones do not overlap.
#
#   +----------+----------+
#   |  Office  | Meeting  |     Lab sits south of Office.
#   +----------+----------+     ServerRoom is the restricted zone, east of the campus.
#   |   Lab    |
#   +----------+                [ ServerRoom ]
O_LAT, O_LON = SITE_ORIGIN

ZONES: tuple[Zone, ...] = (
    Zone("Office", ZoneKind.ALLOWED, _box(O_LAT, O_LON, O_LAT + 0.0004, O_LON + 0.0004)),
    Zone("MeetingRoom", ZoneKind.ALLOWED, _box(O_LAT, O_LON + 0.0004, O_LAT + 0.0004, O_LON + 0.0008)),
    Zone("Lab", ZoneKind.ALLOWED, _box(O_LAT - 0.0004, O_LON, O_LAT, O_LON + 0.0004)),
    Zone("ServerRoom", ZoneKind.RESTRICTED, _box(O_LAT - 0.0004, O_LON + 0.0010, O_LAT, O_LON + 0.0014)),
)

ZONES_BY_NAME = {z.name: z for z in ZONES}


def zone_of(point: LatLon) -> Zone | None:
    """The zone containing the point, or None if it is outside every zone."""
    for z in ZONES:
        if z.contains(point):
            return z
    return None
