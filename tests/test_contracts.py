from datetime import datetime, timedelta, timezone

import pytest
from pydantic import ValidationError

from contracts.geo import haversine_m, point_in_polygon
from contracts.scenarios import SCENARIOS, Episode, Modality, ScenarioType
from contracts.site import ZONES, ZONES_BY_NAME, SITE_ORIGIN, ZoneKind, zone_of
from contracts.telemetry import Telemetry

UTC = timezone.utc


def valid(**over):
    base = dict(
        device_id="D-042",
        timestamp=datetime(2026, 10, 7, 14, 30, 21, tzinfo=UTC),
        latitude=18.5204,
        longitude=73.8567,
        battery=63,
        cpu_usage=42,
        memory_usage=55,
        temperature=48,
        uptime_s=3600,
        network_connections=7,
        bytes_sent=42190,
        bytes_received=82910,
        remote_endpoints=["10.0.0.5"],
        nearby_devices=["D-011", "D-019"],
    )
    base.update(over)
    return base


# ---- telemetry -------------------------------------------------------------------------


def test_valid_telemetry_parses():
    assert Telemetry(**valid()).device_id == "D-042"


@pytest.mark.parametrize(
    "bad",
    [
        dict(battery=101),
        dict(battery=-1),
        dict(latitude=91),
        dict(longitude=181),
        dict(device_id="X-1"),
        dict(bytes_sent=-5),
        dict(nearby_devices=["D-042"]),  # near itself
        dict(nearby_devices=["bogus"]),
        dict(timestamp=datetime(2026, 10, 7, 14, 30, 21)),  # naive
        dict(surprise_field=1),  # extra field
    ],
)
def test_bad_telemetry_rejected(bad):
    with pytest.raises(ValidationError):
        Telemetry(**valid(**bad))


def test_telemetry_is_immutable():
    t = Telemetry(**valid())
    with pytest.raises(ValidationError):
        t.battery = 1


def test_telemetry_has_no_label_field():
    # Guard for manual §3.14: labels must never ride along inside telemetry.
    assert not {"label", "is_malicious", "scenario", "zone"} & set(Telemetry.model_fields)


# ---- geometry --------------------------------------------------------------------------


def test_haversine_known_distance():
    # One degree of latitude is ~111.2 km.
    d = haversine_m((0.0, 0.0), (1.0, 0.0))
    assert d == pytest.approx(111_195, rel=0.001)


def test_haversine_zero_and_symmetric():
    a, b = (18.52, 73.85), (18.53, 73.86)
    assert haversine_m(a, a) == 0
    assert haversine_m(a, b) == pytest.approx(haversine_m(b, a))


def test_point_in_polygon_square():
    sq = [(0, 0), (0, 2), (2, 2), (2, 0)]
    assert point_in_polygon((1, 1), sq)
    assert not point_in_polygon((3, 1), sq)
    assert not point_in_polygon((1, -1), sq)


def test_point_in_concave_polygon():
    # An "L" shape; the notch at (1.5, 1.5) is outside.
    ell = [(0, 0), (0, 2), (1, 2), (1, 1), (2, 1), (2, 0)]
    assert point_in_polygon((0.5, 0.5), ell)
    assert point_in_polygon((0.5, 1.5), ell)
    assert not point_in_polygon((1.5, 1.5), ell)


# ---- site ------------------------------------------------------------------------------


def test_zone_centres_resolve_to_themselves():
    for z in ZONES:
        assert zone_of(z.center) is z


def test_outside_point_has_no_zone():
    assert zone_of((SITE_ORIGIN[0] + 1, SITE_ORIGIN[1])) is None


def test_zones_do_not_overlap():
    for a in ZONES:
        for b in ZONES:
            if a is not b:
                assert not b.contains(a.center)


def test_exactly_one_restricted_zone():
    assert [z.name for z in ZONES if z.kind is ZoneKind.RESTRICTED] == ["ServerRoom"]
    assert ZONES_BY_NAME["ServerRoom"].kind is ZoneKind.RESTRICTED


# ---- scenarios -------------------------------------------------------------------------


def test_every_scenario_type_is_defined():
    assert set(SCENARIOS) == set(ScenarioType)


def test_hard_negative_is_benign_but_touches_signals():
    s = SCENARIOS[ScenarioType.HARD_NEGATIVE]
    assert not s.is_malicious and len(s.modalities) >= 2


def test_malicious_scenarios_touch_a_modality():
    for s in SCENARIOS.values():
        if s.is_malicious:
            assert s.modalities


def test_weak_correlated_is_multimodal_and_low_magnitude():
    s = SCENARIOS[ScenarioType.WEAK_CORRELATED]
    assert len(s.modalities) >= 2 and s.magnitude_range[1] <= 0.5


def _episode(**over):
    t0 = datetime(2026, 10, 7, tzinfo=UTC)
    base = dict(
        episode_id="E-1",
        device_id="D-042",
        scenario=ScenarioType.SPATIAL,
        start=t0,
        end=t0 + timedelta(minutes=2),
        modalities=frozenset({Modality.SPATIAL}),
        magnitude=0.5,
        is_malicious=True,
        seed=7,
    )
    base.update(over)
    return base


def test_staged_scenario_is_weak_multimodal_and_comparable_to_weak_correlated():
    s, w = SCENARIOS[ScenarioType.STAGED], SCENARIOS[ScenarioType.WEAK_CORRELATED]
    assert s.is_malicious and s.magnitude_range == w.magnitude_range  # same effect sizes: only timing differs
    assert s.modalities == frozenset({Modality.SPATIAL, Modality.NETWORK, Modality.DEVICE})


def test_episode_windows_validate_and_gate_modalities():
    t0 = datetime(2026, 10, 7, tzinfo=UTC)
    ep = Episode(**_episode(
        scenario=ScenarioType.STAGED, end=t0 + timedelta(seconds=300),
        modalities=frozenset({Modality.SPATIAL, Modality.NETWORK}),
        windows_s={"spatial": (0, 100), "network": (150, 250)},
    ))
    at = lambda s: ep.active_modalities(t0 + timedelta(seconds=s))
    assert at(50) == {Modality.SPATIAL}
    assert at(125) == frozenset()  # the gap: nothing active, but the episode is still running
    assert at(200) == {Modality.NETWORK}
    assert at(300) == frozenset() and at(-1) == frozenset()  # outside the episode
    assert Episode.model_validate_json(ep.model_dump_json()) == ep  # windows survive the JSON round trip


def test_modality_without_a_window_is_active_for_the_whole_episode():
    t0 = datetime(2026, 10, 7, tzinfo=UTC)
    ep = Episode(**_episode(end=t0 + timedelta(seconds=300)))
    assert ep.active_modalities(t0 + timedelta(seconds=299)) == {Modality.SPATIAL}


@pytest.mark.parametrize(
    "bad",
    [{"spatial": (50, 50)}, {"spatial": (80, 20)}, {"spatial": (-5, 20)}, {"spatial": (0, 9999)}, {"warp": (0, 10)}],
)
def test_bad_windows_are_rejected(bad):
    t0 = datetime(2026, 10, 7, tzinfo=UTC)
    with pytest.raises(ValidationError):
        Episode(**_episode(end=t0 + timedelta(seconds=300), windows_s=bad))


def test_episode_valid():
    assert Episode(**_episode()).is_malicious


def test_episode_rejects_backwards_span():
    t0 = datetime(2026, 10, 7, tzinfo=UTC)
    with pytest.raises(ValidationError):
        Episode(**_episode(end=t0 - timedelta(seconds=1)))


def test_episode_rejects_naive_times():
    with pytest.raises(ValidationError):
        Episode(**_episode(start=datetime(2026, 10, 7), end=datetime(2026, 10, 8)))
