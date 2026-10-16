from datetime import timedelta, timezone

import pytest

from contracts.scenarios import Episode, Modality, ScenarioType
from contracts.site import SITE_TZ, ZoneKind, zone_of
from simulator.config import SimConfig
from simulator.fleet import Fleet
from simulator.io import read_episodes, read_telemetry, write_dataset
from simulator.plan import build_plan
from simulator.profile import ENDPOINT_POOL

CFG = SimConfig(seed=7, n_devices=6)
HOUR = timedelta(hours=1)
# Monday 10:00 site time is mid working day.
WORK_START = timedelta(hours=10)


def _ep(cfg, device, scenario, mods, mag, start_h=10, minutes=30, malicious=True, eid="E-0001"):
    start = cfg.start + timedelta(hours=start_h)
    return Episode(
        episode_id=eid, device_id=device, scenario=scenario, start=start,
        end=start + timedelta(minutes=minutes), modalities=frozenset(mods),
        magnitude=mag, is_malicious=malicious, seed=cfg.seed,
    )


def _run(cfg, episodes, hours):
    return list(Fleet(cfg, episodes).run(timedelta(hours=hours)))


def _during(rows, ep):
    return [r for r in rows if r.device_id == ep.device_id and ep.start <= r.timestamp < ep.end]


def test_same_seed_gives_identical_data():
    a = [t.model_dump_json() for t in Fleet(CFG).run(timedelta(minutes=10))]
    b = [t.model_dump_json() for t in Fleet(CFG).run(timedelta(minutes=10))]
    assert a == b


def test_different_seed_gives_different_data():
    other = SimConfig(seed=8, n_devices=6)
    a = [t.model_dump_json() for t in Fleet(CFG).run(timedelta(minutes=5))]
    b = [t.model_dump_json() for t in Fleet(other).run(timedelta(minutes=5))]
    assert a != b


def test_adding_devices_does_not_change_existing_device_streams():
    small = SimConfig(seed=7, n_devices=3)
    big = SimConfig(seed=7, n_devices=8)
    def d1(cfg):
        return [
            t.model_copy(update={"nearby_devices": []}).model_dump_json()
            for t in Fleet(cfg).run(timedelta(minutes=10)) if t.device_id == "D-001"
        ]
    assert d1(small) == d1(big)


def test_all_rows_valid_and_one_per_device_per_tick():
    rows = list(Fleet(CFG).run(timedelta(minutes=5)))
    assert len(rows) == 6 * (300 // CFG.tick_s)
    assert all(0 <= r.battery <= 100 for r in rows)


def test_devices_are_in_a_zone_during_working_hours():
    cfg = SimConfig(seed=3, n_devices=4, start=CFG.start + timedelta(hours=10))
    rows = list(Fleet(cfg).run(timedelta(hours=1)))
    inside = sum(zone_of((r.latitude, r.longitude)) is not None for r in rows)
    assert inside / len(rows) > 0.8  # some time is spent walking between zones


def test_normal_devices_do_not_enter_restricted_zone():
    cfg = SimConfig(seed=3, n_devices=10, start=CFG.start + timedelta(hours=9))
    rows = list(Fleet(cfg).run(timedelta(hours=4)))
    bad = [r for r in rows if (z := zone_of((r.latitude, r.longitude))) and z.kind is ZoneKind.RESTRICTED]
    assert not bad


def test_strong_spatial_episode_enters_server_room_then_returns():
    ep = _ep(CFG, "D-002", ScenarioType.SPATIAL, {Modality.SPATIAL}, 1.0, minutes=30)
    rows = _run(CFG, [ep], 12)
    during = _during(rows, ep)
    in_restricted = [r for r in during if (z := zone_of((r.latitude, r.longitude))) and z.kind is ZoneKind.RESTRICTED]
    assert len(in_restricted) / len(during) > 0.5
    after = [r for r in rows if r.device_id == "D-002" and r.timestamp > ep.end + timedelta(minutes=20)]
    still = [r for r in after[:60] if (z := zone_of((r.latitude, r.longitude))) and z.kind is ZoneKind.RESTRICTED]
    assert len(still) < 60  # it leaves again


def test_mild_spatial_episode_does_not_enter_restricted_zone():
    ep = _ep(CFG, "D-002", ScenarioType.SPATIAL, {Modality.SPATIAL}, 0.3)
    rows = _during(_run(CFG, [ep], 12), ep)
    assert not any((z := zone_of((r.latitude, r.longitude))) and z.kind is ZoneKind.RESTRICTED for r in rows)


def test_network_episode_raises_traffic_and_adds_unfamiliar_endpoints():
    ep = _ep(CFG, "D-003", ScenarioType.NETWORK, {Modality.NETWORK}, 0.9)
    rows = _run(CFG, [ep], 12)
    during = _during(rows, ep)
    before = [r for r in rows if r.device_id == "D-003" and r.timestamp < ep.start][-200:]
    mean = lambda xs: sum(xs) / len(xs)
    assert mean([r.bytes_sent for r in during]) > 3 * mean([r.bytes_sent for r in before])
    # Unfamiliar endpoints come from the SAME pool, so they cannot be spotted by format.
    assert all(e in ENDPOINT_POOL for r in during for e in r.remote_endpoints)
    known = set().union(*(set(r.remote_endpoints) for r in before))
    assert any(e not in known for r in during for e in r.remote_endpoints)


def test_hardware_episode_drains_battery_and_heats_up():
    ep = _ep(CFG, "D-004", ScenarioType.HARDWARE, {Modality.DEVICE}, 1.0, minutes=60)
    rows = _run(CFG, [ep], 12)
    during = _during(rows, ep)
    before = [r for r in rows if r.device_id == "D-004" and r.timestamp < ep.start][-200:]
    assert during[0].battery - during[-1].battery > before[0].battery - before[-1].battery
    assert sum(r.cpu_usage for r in during) / len(during) > sum(r.cpu_usage for r in before) / len(before) + 20


def test_proximity_episode_adds_unknown_non_fleet_devices():
    ep = _ep(CFG, "D-005", ScenarioType.PROXIMITY, {Modality.PROXIMITY}, 1.0)
    during = _during(_run(CFG, [ep], 12), ep)
    fleet_ids = set(CFG.device_ids())
    assert all(any(n not in fleet_ids for n in r.nearby_devices) for r in during)
    # The same rogue ids persist through the episode.
    assert len({n for r in during for n in r.nearby_devices if n not in fleet_ids}) <= 4


def test_nearby_is_symmetric_among_fleet_devices():
    fleet_ids = set(CFG.device_ids())
    for batch in Fleet(CFG).ticks(timedelta(minutes=30)):
        by_id = {t.device_id: set(t.nearby_devices) & fleet_ids for t in batch}
        for a, near in by_id.items():
            for b in near:
                assert a in by_id[b]


def test_network_effect_scales_with_magnitude():
    """Weak anomalies must be weak: the share of ticks with an unfamiliar contact rises with m."""

    def frac(mag):
        ep = _ep(CFG, "D-003", ScenarioType.NETWORK, {Modality.NETWORK}, mag, minutes=60)
        fleet = Fleet(CFG, [ep])
        known = set(fleet.devices[2].p.known_endpoints)
        rows = _during(list(fleet.run(timedelta(hours=12))), ep)
        return sum(any(e not in known for e in r.remote_endpoints) for r in rows) / len(rows)

    low, high = frac(0.1), frac(0.9)
    assert low < 0.3 and high > 0.7 and high > 3 * low


def test_visitors_are_rare_and_brief():
    rows = list(Fleet(CFG).run(timedelta(hours=8)))
    fleet_ids = set(CFG.device_ids())
    with_visitor = [r for r in rows if any(n not in fleet_ids for n in r.nearby_devices)]
    assert 0 < len(with_visitor) / len(rows) < 0.03


def test_devices_head_home_after_work_ends():
    # 18:00-18:30 is the grace window; by 19:00 everyone is back in their home zone area.
    cfg = SimConfig(seed=11, n_devices=8, start=CFG.start + timedelta(hours=17, minutes=30))
    rows = list(Fleet(cfg).run(timedelta(hours=2)))
    late = [r for r in rows if r.timestamp >= cfg.start.astimezone(timezone.utc) + timedelta(minutes=95)]
    in_allowed_or_transit = [r for r in late if (z := zone_of((r.latitude, r.longitude))) is None or z.name in {"Office", "Lab"}]
    assert len(in_allowed_or_transit) / len(late) > 0.95  # nobody is still sitting in the meeting room


# ---- plan --------------------------------------------------------------------------------


def test_plan_respects_warmup_and_does_not_overlap():
    cfg = SimConfig(seed=5, n_devices=20)
    plan = build_plan(cfg, timedelta(hours=48), timedelta(hours=12), episodes_per_device_per_day=3)
    assert plan
    assert all(e.start >= cfg.start + timedelta(hours=12) for e in plan)
    assert all(e.end <= cfg.start + timedelta(hours=48) for e in plan)
    for dev in cfg.device_ids():
        eps = sorted((e for e in plan if e.device_id == dev), key=lambda e: e.start)
        for a, b in zip(eps, eps[1:]):
            assert b.start - a.end >= timedelta(seconds=300)


def test_plan_is_reproducible_and_covers_hard_cases():
    cfg = SimConfig(seed=5, n_devices=40)
    a = build_plan(cfg, timedelta(hours=72), timedelta(hours=12), 3)
    b = build_plan(cfg, timedelta(hours=72), timedelta(hours=12), 3)
    assert a == b
    kinds = {e.scenario for e in a}
    assert {ScenarioType.WEAK_CORRELATED, ScenarioType.HARD_NEGATIVE, ScenarioType.COMBINED} <= kinds
    assert all(not e.is_malicious for e in a if e.scenario is ScenarioType.HARD_NEGATIVE)
    assert all(e.magnitude <= 0.4 for e in a if e.scenario is ScenarioType.WEAK_CORRELATED)


def test_magnitude_override_for_difficulty_sweep():
    cfg = SimConfig(seed=5, n_devices=20)
    plan = build_plan(cfg, timedelta(hours=48), timedelta(hours=12), 3, magnitude=(0.05, 0.1))
    assert all(e.magnitude <= 0.1 for e in plan if e.variant != "authorized_visit")


def test_dataset_roundtrip_and_labels_are_separate(tmp_path):
    cfg = SimConfig(seed=2, n_devices=3)
    plan = build_plan(cfg, timedelta(hours=14), timedelta(hours=12), 6)
    n = write_dataset(tmp_path, cfg, plan, Fleet(cfg, plan).run(timedelta(minutes=2)))
    assert n == 3 * (120 // cfg.tick_s)
    assert len(list(read_telemetry(tmp_path / "telemetry.jsonl"))) == n
    assert read_episodes(tmp_path / "episodes.jsonl") == plan
    text = (tmp_path / "telemetry.jsonl").read_text()
    assert "is_malicious" not in text and "scenario" not in text
