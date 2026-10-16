"""Geometry helpers (manual §3.4)."""

from math import asin, cos, pi, radians, sin, sqrt

EARTH_RADIUS_M = 6_371_000.0

LatLon = tuple[float, float]

# Metres per degree of latitude on a sphere of EARTH_RADIUS_M (consistent with haversine).
M_PER_DEG_LAT = 2 * pi * EARTH_RADIUS_M / 360


def to_local_m(p: LatLon, origin: LatLon) -> tuple[float, float]:
    """(lat, lon) -> (x east, y north) metres from origin. Flat approximation, site scale only."""
    x = (p[1] - origin[1]) * M_PER_DEG_LAT * cos(radians(origin[0]))
    y = (p[0] - origin[0]) * M_PER_DEG_LAT
    return x, y


def from_local_m(xy: tuple[float, float], origin: LatLon) -> LatLon:
    lat = origin[0] + xy[1] / M_PER_DEG_LAT
    lon = origin[1] + xy[0] / (M_PER_DEG_LAT * cos(radians(origin[0])))
    return lat, lon


def haversine_m(a: LatLon, b: LatLon) -> float:
    """Great-circle distance in metres between two (lat, lon) points."""
    lat1, lon1 = map(radians, a)
    lat2, lon2 = map(radians, b)
    h = sin((lat2 - lat1) / 2) ** 2 + cos(lat1) * cos(lat2) * sin((lon2 - lon1) / 2) ** 2
    return 2 * EARTH_RADIUS_M * asin(sqrt(h))


def point_in_polygon(point: LatLon, polygon: list[LatLon]) -> bool:
    """Ray casting: cast a ray east from the point, count edge crossings; odd = inside.

    Treats lat/lon as planar, which is fine at site scale (tens of metres), not across
    continents. Points exactly on an edge may land either side; the geofence detector
    adds hysteresis so that does not cause flicker.
    """
    lat, lon = point
    inside = False
    n = len(polygon)
    for i in range(n):
        lat1, lon1 = polygon[i]
        lat2, lon2 = polygon[(i + 1) % n]
        # Does this edge straddle the point's latitude?
        if (lat1 > lat) != (lat2 > lat):
            # Longitude where the edge crosses that latitude.
            cross_lon = lon1 + (lat - lat1) / (lat2 - lat1) * (lon2 - lon1)
            if lon < cross_lon:
                inside = not inside
    return inside
