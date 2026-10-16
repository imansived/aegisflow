"""Geometry helpers (manual §3.4)."""

from math import asin, cos, radians, sin, sqrt

EARTH_RADIUS_M = 6_371_000.0

LatLon = tuple[float, float]


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
