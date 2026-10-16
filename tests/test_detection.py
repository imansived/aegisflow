from datetime import datetime, timedelta, timezone

import numpy as np
import pytest

from backend.database.store import EventStore
from backend.detection.config import DetectionConfig
from backend.detection.engine import DetectionEngine
from backend.detection.stats import BaselineSet, RunningStat, ramp
from contracts.scenarios import Episode, Modality, ScenarioType
from contracts.site import SITE_ORIGIN, ZONES_BY_NAME
from contracts.geo import from_local_m
from contracts.telemetry import Telemetry
from simulator.config import SimConfig
from simulator.fleet import Fleet

# Monday 10:00 site time (IST) = 04:30 UTC, inside working hours.
T0 = datetime(2026, 10, 5, 4, 30, tzinfo=timezone.utc)
TICK = 5
KNOWN = ["10.0.0.1", "10.0.0.2", "10.0.0.3"]
CFG = DetectionConfig(occ_min_samples=500)  # normal occupancy needs far more data than these tests give
OFFICE = ZONES_BY_NAME["Office"].center


def mk(i, rng, **over):
    """One ordinary event at tick i, with small noise."""
    lat, lon = OFFICE
    base = dict(
        device_id="D-001", timestamp=T0 + timedelta(seconds=TICK * i),
        latitude=lat + rng.normal(0, 1e-5) * 0.3, longitude=lon + rng.normal(0, 1e-5) * 0.3,
        battery=80 - 7 * (TICK * i / 3600), cpu_usage=float(np.clip(rng.normal(30, 2), 0, 100)),
        memory_usage=50, temperature=float(35.5 + rng.normal(0, 0.5)), uptime_s=1000 + TICK * i,
        network_connections=int(rng.poisson(8)),
        bytes_sent=int(rng.lognormal(np.log(40_000), 0.3)),
        bytes_received=int(rng.lognormal(np.log(80_000), 0.3)),
        remote_endpoints=list(KNOWN),
    )
    base.update(over)
    return Telemetry(**base)


def trained(n=2700, cfg=CFG, enrolled=("D-001", "D-002")):
    """An engine that has watched n ordinary ticks (3.75 h) of one device."""
    rng = np.random.default_rng(0)
    eng = DetectionEngine(enrolled, cfg)
    last = None
    for i in range(n):
        last = eng.process(mk(i, rng))
    return eng, rng, n, last


# ---- statistics -----------------------------------------------------------------------


def test_welford_matches_numpy():
    xs = np.random.default_rng(1).normal(5, 2, 500)
    s = RunningStat()
    for x in xs:
        s.update(float(x), warm_n=10_000, alpha=0.001)
    assert s.mean == pytest.approx(xs.mean(), rel=1e-9)
    assert s.var == pytest.approx(xs.var(ddof=1), rel=1e-9)


def test_welford_is_stable_with_large_offset():
    # E[x^2]-E[x]^2 loses all precision here; Welford does not.
    xs = 1e9 + np.random.default_rng(2).normal(0, 1, 1000)
    s = RunningStat()
    for x in xs:
        s.update(float(x), 10_000, 0.001)
    assert s.var == pytest.approx(1.0, rel=0.15)


def test_ew_phase_adapts_to_a_level_shift():
    s = RunningStat()
    for _ in range(100):
        s.update(10.0, warm_n=100, alpha=0.05)
    for _ in range(300):
        s.update(20.0, warm_n=100, alpha=0.05)
    assert s.mean > 19.0


def test_ramp_endpoints():
    assert ramp(1.0, 2, 6) == 0 and ramp(6, 2, 6) == 1 and ramp(4, 2, 6) == 0.5 and ramp(99, 2, 6) == 1


def test_min_std_floor_prevents_huge_z_on_constant_feature():
    bs = BaselineSet(DetectionConfig(min_samples=5))
    for _ in range(20):
        bs.learn("work", "f", 10.0)  # zero variance
    assert bs.z("work", "f", 10.5, min_std=1.0, sided="high") == pytest.approx(0.5)


# ---- cold start -----------------------------------------------------------------------


def test_no_alerts_while_learning():
    eng = DetectionEngine(["D-001"], CFG)
    rng = np.random.default_rng(0)
    for i in range(100):  # far fewer than min_samples
        s = eng.process(mk(i, rng, cpu_usage=99.0, bytes_sent=5_000_000))
    assert s.learning
    assert all(r.score == 0 for r in s.results.values() if r.modality is not Modality.SPATIAL)


# ---- spatial --------------------------------------------------------------------------


def server_room_event(i, rng):
    lat, lon = ZONES_BY_NAME["ServerRoom"].center
    return mk(i, rng, latitude=lat, longitude=lon)


def test_restricted_zone_is_debounced_then_full_score_then_resets():
    eng, rng, n, _ = trained()
    # The device walked there over a 2.5-minute gap in reports, so the speed rule (which
    # would rightly flag a 100 m jump in 5 s) is not what fires here.
    n += 30
    first = eng.process(server_room_event(n, rng)).score(Modality.SPATIAL)
    second = eng.process(server_room_event(n + 1, rng)).score(Modality.SPATIAL)
    assert first == 0.5 and second == 1.0
    back = eng.process(mk(n + 40, rng)).score(Modality.SPATIAL)
    assert back < 0.5  # leaving the zone drops the score at once


def test_teleporting_into_a_zone_trips_the_speed_rule_first():
    eng, rng, n, _ = trained()
    s = eng.process(server_room_event(n, rng))  # ~100 m in 5 s
    assert s.score(Modality.SPATIAL) == 1.0 and "speed_mps" in s.results[Modality.SPATIAL].evidence


def test_implausible_speed_is_flagged():
    eng, rng, n, _ = trained()
    lat, lon = OFFICE
    s = eng.process(mk(n, rng, latitude=lat + 0.01, longitude=lon))  # ~1.1 km in 5 s
    assert s.score(Modality.SPATIAL) == 1.0 and "speed_mps" in s.results[Modality.SPATIAL].evidence


def test_normal_walking_speed_is_not_flagged():
    eng, rng, n, _ = trained()
    lat, lon = OFFICE
    # ~7 m in 5 s = 1.4 m/s
    s = eng.process(mk(n, rng, latitude=lat + 0.000063, longitude=lon))
    assert "speed_mps" not in s.results[Modality.SPATIAL].evidence


# ---- network --------------------------------------------------------------------------


def test_sustained_unfamiliar_endpoints_alert():
    eng, rng, n, _ = trained()
    peak = 0.0
    for i in range(24):  # two minutes
        s = eng.process(mk(n + i, rng, remote_endpoints=KNOWN + ["10.0.0.21", "10.0.0.22"]))
        peak = max(peak, s.score(Modality.NETWORK))
    assert peak >= 0.9


def test_one_benign_novel_endpoint_does_not_alert():
    eng, rng, n, _ = trained()
    s = eng.process(mk(n, rng, remote_endpoints=KNOWN + ["10.0.0.30"]))
    assert s.score(Modality.NETWORK) < 0.5


def test_upload_spike_alerts():
    eng, rng, n, _ = trained()
    peak = 0.0
    for i in range(24):
        s = eng.process(mk(n + i, rng, bytes_sent=int(40_000 * 12)))
        peak = max(peak, s.score(Modality.NETWORK))
    assert peak >= 0.9


def test_attack_endpoints_do_not_become_known_during_a_short_attack():
    # Poison resistance: an endpoint counts at most once per 10 minutes, so a 5-minute attack
    # cannot reach the 3 contacts needed to be "known".
    eng, rng, n, _ = trained()
    net = eng.detectors[1]
    for i in range(60):  # 5 minutes of the same unfamiliar endpoint
        eng.process(mk(n + i, rng, remote_endpoints=KNOWN + ["10.0.0.29"]))
    assert net._state["D-001"].counts["10.0.0.29"] < CFG.endpoint_known_count
    assert net._state["D-001"].counts["10.0.0.1"] >= CFG.endpoint_known_count


# ---- device ---------------------------------------------------------------------------


def test_sustained_cpu_rise_alerts_but_single_spike_does_not():
    eng, rng, n, _ = trained()
    spike = eng.process(mk(n, rng, cpu_usage=95.0)).score(Modality.DEVICE)
    assert spike < 0.5  # one tick of 95 averaged over a minute is small
    peak = 0.0
    for i in range(1, 30):
        peak = max(peak, eng.process(mk(n + i, rng, cpu_usage=80.0)).score(Modality.DEVICE))
    assert peak >= 0.9


def test_fast_battery_drain_alerts():
    eng, rng, n, _ = trained()
    base = 80 - 7 * (TICK * n / 3600)
    peak = 0.0
    for i in range(80):  # ~6.7 minutes at 40 %/h
        s = eng.process(mk(n + i, rng, battery=base - 40 * (TICK * i / 3600)))
        peak = max(peak, s.score(Modality.DEVICE))
    assert peak >= 0.9


def test_charging_is_not_scored_as_drain():
    eng, rng, n, _ = trained()
    base = 40.0
    for i in range(80):
        s = eng.process(mk(n + i, rng, battery=base + 12 * (TICK * i / 3600)))  # charging
    assert "drain" not in s.results[Modality.DEVICE].evidence
    assert s.score(Modality.DEVICE) < 0.5


# ---- proximity ------------------------------------------------------------------------


def test_enrolled_neighbours_are_ignored():
    eng, rng, n, _ = trained()
    for i in range(60):
        s = eng.process(mk(n + i, rng, nearby_devices=["D-002"]))
    assert s.score(Modality.PROXIMITY) == 0


def test_brief_visitor_ignored_but_persistent_unenrolled_device_alerts():
    eng, rng, n, _ = trained()
    brief = [eng.process(mk(n + i, rng, nearby_devices=["D-950"])).score(Modality.PROXIMITY) for i in range(6)]
    assert max(brief) < 0.5  # 30 s
    eng.process(mk(n + 6, rng))  # gone: persistence resets
    long = [eng.process(mk(n + 7 + i, rng, nearby_devices=["D-950"])).score(Modality.PROXIMITY) for i in range(48)]
    assert max(long) >= 0.8  # 4 min


# ---- poisoning / replay ---------------------------------------------------------------


def test_baseline_resists_poisoning_by_a_sustained_anomaly():
    eng, rng, n, _ = trained()
    cpu = eng.detectors[2]._baselines["D-001"].stat("work", "cpu")
    before = cpu.mean
    for i in range(360):  # 30 minutes of cpu at 80
        eng.process(mk(n + i, rng, cpu_usage=80.0))
    assert abs(cpu.mean - before) < 0.2 * before


def test_replaying_stored_events_rebuilds_identical_scores():
    rng = np.random.default_rng(3)
    events = [mk(i, rng) for i in range(800)] + [mk(800 + i, rng, cpu_usage=85.0) for i in range(30)]
    live = DetectionEngine(["D-001"], CFG)
    live_scores = [live.process(e).score(Modality.DEVICE) for e in events]

    store = EventStore()
    for e in events:
        store.insert_event(e)
    rebuilt_scores = []
    DetectionEngine(["D-001"], CFG, sink=lambda s: rebuilt_scores.append(s.score(Modality.DEVICE))).replay(store.iter_events())
    assert rebuilt_scores == live_scores


# ---- end to end with the simulator ----------------------------------------------------


def test_simulated_restricted_entry_is_detected_and_normal_time_is_quiet():
    cfg = SimConfig(seed=9, n_devices=2)
    start = cfg.start + timedelta(hours=13)
    ep = Episode(
        episode_id="E-1", device_id="D-001", scenario=ScenarioType.SPATIAL, start=start,
        end=start + timedelta(minutes=20), modalities=frozenset({Modality.SPATIAL}),
        magnitude=1.0, is_malicious=True, seed=9,
    )
    eng = DetectionEngine(cfg.device_ids(), DetectionConfig(occ_min_samples=500))
    during, quiet = [], []
    for t in Fleet(cfg, [ep]).run(timedelta(hours=14)):
        s = eng.process(t)
        if t.device_id != "D-001":
            continue
        if ep.start <= t.timestamp < ep.end:
            during.append(s.score(Modality.SPATIAL))
        elif t.timestamp > cfg.start + timedelta(hours=10) and t.timestamp < ep.start - timedelta(minutes=5):
            quiet.append(s.score(Modality.SPATIAL))
    assert max(during) == 1.0
    assert max(quiet) < 0.5
