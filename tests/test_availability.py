"""Explicit signal availability (manual §5.11, D106/D107): unavailable is reported, never read as normal."""

import json
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from pydantic import ValidationError

from backend.correlation.engine import FROZEN_CORRELATION, CorrelationEngine
from backend.correlation.fusion import MODALITIES, LearnedFusion, NoisyOr
from backend.correlation.thresholds import FULL, Operating, ThresholdTable, describe, pattern_of
from backend.detection.engine import DetectionEngine
from backend.services.alerts import AlertConfig, AlertManager
from contracts.scenarios import Episode, Modality, ScenarioType
from contracts.telemetry import SIGNAL_FIELDS, Telemetry
from simulator.config import SimConfig
from simulator.fleet import Fleet

T0 = datetime(2026, 10, 5, 12, 0, tzinfo=timezone.utc)
S, N, D, P = MODALITIES


def tele(sec=0, lost=(), **over) -> Telemetry:
    f = dict(
        device_id="D-001", timestamp=T0 + timedelta(seconds=sec), latitude=18.5206, longitude=73.8569, battery=80.0, cpu_usage=20.0,
        memory_usage=30.0, temperature=40.0, uptime_s=100, network_connections=5, bytes_sent=1000, bytes_received=2000,
        remote_endpoints=["10.0.0.1"], nearby_devices=[],
    )
    f.update(over)
    for g in lost:
        for name in SIGNAL_FIELDS[g]:
            f[name] = None
    return Telemetry(**f)


# ---- the contract ------------------------------------------------------------------------------------


def test_a_signal_group_is_all_numbers_or_all_null():
    t = tele(lost=("network",))
    assert t.unavailable == {"network"} and t.bytes_sent is None and t.remote_endpoints is None
    assert tele(lost=("spatial", "proximity")).unavailable == {"spatial", "proximity"}
    assert tele().unavailable == frozenset()
    with pytest.raises(ValidationError, match="fully reported or entirely null"):
        tele(latitude=None)  # longitude is present: a half-missing fix is rejected, not guessed
    with pytest.raises(ValidationError, match="fully reported or entirely null"):
        tele(battery=None)


def test_omitting_a_field_is_still_rejected_only_an_explicit_null_means_unavailable():
    d = json.loads(tele().model_dump_json())
    del d["latitude"]
    with pytest.raises(ValidationError):
        Telemetry.model_validate(d)
    d = json.loads(tele(lost=("spatial",)).model_dump_json())
    assert d["latitude"] is None and Telemetry.model_validate(d).unavailable == {"spatial"}


def test_empty_neighbour_list_is_an_observation_null_is_not():
    assert tele(nearby_devices=[]).unavailable == frozenset()
    assert tele(lost=("proximity",)).unavailable == {"proximity"}


# ---- detectors never score what was not observed -----------------------------------------------------


def test_unavailable_signal_gets_no_score_and_does_not_teach_its_baseline():
    eng = DetectionEngine(["D-001"])
    for i in range(50):
        eng.process(tele(i * 5))
    n_before = len(eng.detectors[1]._baselines["D-001"].__dict__) if hasattr(eng.detectors[1]._baselines["D-001"], "__dict__") else 0
    s = eng.process(tele(300, lost=("network",)))
    r = s.results[Modality.NETWORK]
    assert not r.available and r.score == 0.0 and r.soft == 0.0 and not r.evidence
    assert s.unavailable == {Modality.NETWORK}
    assert all(s.results[m].available for m in (Modality.SPATIAL, Modality.DEVICE, Modality.PROXIMITY))
    assert len(eng.detectors[1]._baselines["D-001"].__dict__) == n_before


def test_losing_gps_does_not_invent_a_position_or_a_speed():
    eng = DetectionEngine(["D-001"])
    eng.process(tele(0))
    s = eng.process(tele(5, lost=("spatial",)))
    assert not s.results[Modality.SPATIAL].available
    assert "speed" not in s.features and "in_restricted" not in s.features
    s2 = eng.process(tele(10))  # fix returns; the last known fix was at t=0, 10 s ago
    assert s2.results[Modality.SPATIAL].available


# ---- thresholds and the conservative fallback --------------------------------------------------------


def table() -> ThresholdTable:
    return ThresholdTable({FULL: Operating(0.9, 0.8), 2: Operating(0.6, 0.5)})  # 2 = only the network signal available


def fused(lost=(), tab=None, fusion=None, hot=0.0):
    eng = DetectionEngine(["D-001"])
    ce = CorrelationEngine(fusion or NoisyOr(), FROZEN_CORRELATION, tab)
    out = None
    for i in range(3):
        out = ce.update(eng.process(tele(i * 5, lost=lost)))
    return out


def test_complete_signals_use_the_complete_operating_point():
    f = fused(tab=table())
    assert f.observability == "full" and f.alertable and f.operating == Operating(0.9, 0.8) and f.pattern == FULL


def test_a_validated_pattern_uses_its_own_threshold_and_is_reported_degraded():
    f = fused(lost=("spatial", "device", "proximity"), tab=table())  # only network is available
    assert f.observability == "degraded" and f.alertable and f.operating == Operating(0.6, 0.5)
    assert f.unavailable == {Modality.SPATIAL, Modality.DEVICE, Modality.PROXIMITY}


def test_an_unvalidated_pattern_takes_the_conservative_fallback_not_an_invented_threshold():
    f = fused(lost=("network",), tab=table())  # spatial+device+proximity was never validated in this table
    assert not f.alertable and f.operating is None and f.severity == "normal" and f.observability == "degraded"
    assert "no validated threshold" in f.fallback and describe(f.pattern) in f.fallback


def test_nothing_available_is_blind_not_normal():
    f = fused(lost=("spatial", "network", "device", "proximity"), tab=table())
    assert f.observability == "blind" and not f.alertable and "no signal" in f.fallback


def test_without_a_table_a_partly_observed_device_is_never_judged_by_the_complete_signal_threshold():
    f = fused(lost=("network",), tab=None)
    assert not f.alertable and "no availability thresholds" in f.fallback
    assert fused(tab=None).alertable  # complete telemetry behaves exactly as before


def test_a_missing_signal_is_never_scored_as_a_fresh_zero():
    eng = DetectionEngine(["D-001"])
    ce = CorrelationEngine(NoisyOr(), replace_half_life(60.0), table())
    hot = eng.process(tele(0, bytes_sent=10**9, network_connections=500, remote_endpoints=[f"9.9.9.{i}" for i in range(50)]))
    f1 = ce.update(hot)
    f2 = ce.update(eng.process(tele(5, lost=("network",))))
    assert f2.raw[Modality.NETWORK] == 0.0  # a placeholder: availability, not the score, says it was not observed
    assert f2.effective[Modality.NETWORK] == pytest.approx(f1.effective[Modality.NETWORK] * 0.5 ** (5 / 60.0), rel=1e-6)


def replace_half_life(h):
    from dataclasses import replace
    return replace(FROZEN_CORRELATION, half_life_s=h)


def test_shipped_thresholds_cover_every_combination_and_are_ordered_sanely():
    path = Path("models/availability_thresholds.json")
    tab = ThresholdTable.load(path)
    assert tab is not None and sorted(tab.entries) == list(range(1, 16))
    for p, o in tab.entries.items():
        assert 0 < o.clear_at < o.raise_at < 1.0, p
        assert o.false_per_day is not None and o.false_per_day <= 3.0 + 1e-6
    assert ThresholdTable.load(Path("nope.json")) is None
    d = json.loads(path.read_text())
    d["patterns"]["13"]["validated"] = False
    assert 13 not in ThresholdTable.from_dict(d).entries  # an unvalidated entry is as good as absent


def test_the_learned_model_with_the_shipped_table_degrades_instead_of_going_quiet():
    design = json.loads(Path("models/frozen_design.json").read_text())
    lf = LearnedFusion.from_dict(design["fusion"]["learned"])
    tab = ThresholdTable.load(Path("models/availability_thresholds.json"))
    # a strong network anomaly with the network signal reported: alerts under the complete threshold
    assert lf.score([0.0, 1.0, 0.0, 0.0]) > tab.lookup(FULL).raise_at
    # the same device with the network signal UNAVAILABLE has its own, lower-evidence operating point
    assert tab.lookup(pattern_of({Modality.NETWORK})).raise_at != tab.lookup(FULL).raise_at


# ---- alerts honour the thresholds of the moment ------------------------------------------------------


def test_an_unjudgeable_tick_neither_opens_nor_keeps_an_alert_open():
    am = AlertManager(AlertConfig(raise_at=0.9, clear_at=0.5, clear_after_s=30.0), FROZEN_CORRELATION)
    eng = DetectionEngine(["D-001"])

    class Hot(NoisyOr):  # a fusion that always says "extreme", to isolate the alert logic
        def score(self, e):
            return 0.99

        def contributions(self, e):
            return {}

    ce_hot = CorrelationEngine(Hot(), FROZEN_CORRELATION, table())

    def step(e, i, **kw):  # baselines are in cold start in a unit test; mark the tick as past learning
        return am_of(e).update(replace(ce_hot.update(e.process(tele(i * 5, **kw))), learning=False))

    am_of = lambda _e: am  # noqa: E731
    ev = []
    for i in range(3):
        ev += step(eng, i)
    assert [e["event"] for e in ev] == ["opened"] and ev[0]["unavailable"] == []
    closed = []
    for i in range(3, 20):  # network lost: pattern not in the table -> cannot judge -> the alert is not kept alive by it
        closed += step(eng, i, lost=("network",))
    assert [e["event"] for e in closed] == ["closed"]
    # and a hot score on an unjudgeable device never opens an alert
    am2 = AlertManager(AlertConfig(raise_at=0.9, clear_at=0.5), FROZEN_CORRELATION)
    eng2 = DetectionEngine(["D-001"])
    am_of = lambda _e: am2  # noqa: E731
    assert all(not step(eng2, i, lost=("network",)) for i in range(5))


# ---- the simulator can reproduce a loss --------------------------------------------------------------


def test_a_signal_loss_episode_nulls_exactly_that_signal_for_exactly_that_time():
    cfg = SimConfig(seed=1, n_devices=2)
    ep = Episode(
        episode_id="L1", device_id="D-001", scenario=ScenarioType.SIGNAL_LOSS, start=cfg.start + timedelta(seconds=60),
        end=cfg.start + timedelta(seconds=120), modalities=frozenset({Modality.NETWORK}), magnitude=1.0, is_malicious=False, seed=1,
    )
    seen = {}
    for t in Fleet(cfg, [ep]).run(timedelta(seconds=180)):
        if t.device_id == "D-001":
            seen[int((t.timestamp - cfg.start.astimezone(timezone.utc)).total_seconds())] = t.unavailable
        else:
            assert t.unavailable == frozenset()  # other devices are untouched
    assert seen[30] == frozenset() and seen[60] == {"network"} and seen[115] == {"network"} and seen[120] == frozenset()
