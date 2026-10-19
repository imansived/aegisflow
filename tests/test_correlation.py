from datetime import datetime, timedelta, timezone

import numpy as np
import pytest

from backend.correlation.engine import CorrelationConfig, CorrelationEngine
from backend.correlation.explain import explain, factors, reasons
from backend.correlation.fusion import (
    MODALITIES, LearnedFusion, MaxFusion, NoisyOr, SingleSignal, WeightedSum,
)
from backend.database.store import EventStore
from backend.detection.config import DetectionConfig
from backend.detection.types import DetectorResult, Scores
from backend.services.alerts import AlertConfig, AlertManager
from backend.services.pipeline import Pipeline
from contracts.scenarios import Episode, Modality, ScenarioType
from contracts.telemetry import Telemetry
from ml.dataset import RowSet, apply_fusion, build_rows, hold_offline
from ml.iforest import EarlyFusionIF
from simulator.config import SimConfig
from simulator.fleet import Fleet

S, N, D, P = MODALITIES
T0 = datetime(2026, 10, 5, 12, 0, tzinfo=timezone.utc)


def scores(sec, spatial=0.0, network=0.0, device=0.0, proximity=0.0, dev="D-001", learning=False, ev=None):
    """`ev` maps a modality name to its evidence dict, e.g. {"spatial": {"restricted_zone": 1.0}}."""
    ev = ev or {}
    vals = {S: spatial, N: network, D: device, P: proximity}
    results = {m: DetectorResult(m, v, learning, ev.get(m.value, {})) for m, v in vals.items()}
    return Scores(dev, T0 + timedelta(seconds=sec), "work", results, {})


# ---- fusion shapes --------------------------------------------------------------------


def test_weighted_sum_is_an_average_so_one_signal_is_diluted():
    ws = WeightedSum()
    assert ws.score([1, 0, 0, 0]) == pytest.approx(0.25)
    assert ws.score([1, 1, 1, 1]) == pytest.approx(1.0)
    assert ws.score([0.5, 0.5, 0.5, 0.5]) == pytest.approx(0.5)


def test_weighted_sum_normalises_weights_and_honours_them():
    ws = WeightedSum({Modality.SPATIAL: 3, Modality.NETWORK: 1, Modality.DEVICE: 0, Modality.PROXIMITY: 0})
    assert ws.score([1, 0, 0, 0]) == pytest.approx(0.75)


def test_noisy_or_accumulates_weak_signals_and_max_does_not():
    e = [0.3, 0.3, 0.3, 0.0]
    assert NoisyOr().score(e) == pytest.approx(1 - 0.7**3)
    assert MaxFusion().score(e) == 0.3
    assert NoisyOr().score([1, 0, 0, 0]) == 1.0


def test_noisy_or_double_counts_one_benign_cause():
    # A firmware update moves network AND device together. Noisy-OR scores that higher than
    # either alone, which is the false-positive risk described in manual §3.9.
    assert NoisyOr().score([0, 0.7, 0.7, 0]) > NoisyOr().score([0, 0.7, 0, 0])


def test_single_signal_picks_its_modality():
    assert SingleSignal(Modality.DEVICE).score([0.1, 0.2, 0.9, 0.4]) == 0.9
    assert SingleSignal(Modality.DEVICE).name == "device_only"


def test_weighted_sum_contributions_are_exact_and_max_credits_only_the_top():
    e = [0.8, 0.4, 0.0, 0.2]
    c = WeightedSum().contributions(e)
    assert c[Modality.SPATIAL] == pytest.approx(0.25 * 0.8)
    assert sum(c.values()) == pytest.approx(WeightedSum().score(e))
    cm = MaxFusion().contributions(e)
    assert cm[Modality.SPATIAL] == pytest.approx(0.4) and cm[Modality.NETWORK] == 0


def test_learned_fusion_learns_to_ignore_benign_network_plus_device():
    rng = np.random.default_rng(0)
    n = 4000
    E = rng.uniform(0, 0.15, (n, 4))
    y = np.zeros(n, dtype=int)
    atk = slice(0, 1000)  # attack: spatial AND network high
    E[atk, 0] = rng.uniform(0.6, 1, 1000)
    E[atk, 1] = rng.uniform(0.5, 1, 1000)
    y[atk] = 1
    ben = slice(1000, 2000)  # benign look-alike: network AND device high, spatial quiet
    E[ben, 1] = rng.uniform(0.6, 1, 1000)
    E[ben, 2] = rng.uniform(0.6, 1, 1000)
    lf = LearnedFusion().fit(E, y)
    assert lf.score([0.9, 0.8, 0.0, 0.0]) > 0.8
    assert lf.score([0.0, 0.8, 0.8, 0.0]) < 0.3  # a fixed noisy-OR would score this high


def test_learned_fusion_requires_fit():
    with pytest.raises(RuntimeError):
        LearnedFusion().score([0, 0, 0, 0])


# ---- the correlation window ------------------------------------------------------------


def test_decayed_peak_halves_every_half_life_and_retains_evidence():
    ce = CorrelationEngine(MaxFusion(), CorrelationConfig(half_life_s=60))
    ce.update(scores(0, spatial=1.0, ev={"spatial": {"restricted_zone": 1.0}}))
    f = ce.update(scores(60))
    assert f.effective[Modality.SPATIAL] == pytest.approx(0.5)
    assert ce.update(scores(120)).effective[Modality.SPATIAL] == pytest.approx(0.25)
    assert f.evidence[Modality.SPATIAL] == {"restricted_zone": 1.0}  # remembers WHY


def test_new_peak_replaces_a_decayed_one():
    ce = CorrelationEngine(MaxFusion(), CorrelationConfig(half_life_s=60))
    ce.update(scores(0, spatial=0.9))
    f = ce.update(scores(60, spatial=0.7))  # decayed 0.45 < 0.7
    assert f.effective[Modality.SPATIAL] == pytest.approx(0.7)


def test_signals_close_in_time_combine_more_than_signals_far_apart():
    def run(gap):
        ce = CorrelationEngine(NoisyOr(), CorrelationConfig(half_life_s=60))
        ce.update(scores(0, spatial=0.5))
        return ce.update(scores(gap, network=0.5)).score

    assert run(20) > run(600) and run(600) == pytest.approx(0.5, abs=0.01)


def test_devices_are_tracked_independently():
    ce = CorrelationEngine(MaxFusion())
    ce.update(scores(0, spatial=1.0, dev="D-001"))
    assert ce.update(scores(1, dev="D-002")).score == 0


def test_offline_hold_matches_the_streaming_engine():
    rng = np.random.default_rng(5)
    n = 300
    ts = np.cumsum(rng.uniform(1, 9, n)) + T0.timestamp()
    dev = rng.integers(0, 3, n).astype(np.int16)
    raw = (rng.random((n, 4)) * (rng.random((n, 4)) > 0.7)).astype(np.float32)
    off = hold_offline(ts, dev, raw, 60.0)
    ce = CorrelationEngine(MaxFusion(), CorrelationConfig(half_life_s=60))
    for i in range(n):
        sc = Scores(f"D-{dev[i]:03d}", datetime.fromtimestamp(ts[i], timezone.utc), "work",
                    {m: DetectorResult(m, float(raw[i, k]), False) for k, m in enumerate(MODALITIES)}, {})
        f = ce.update(sc)
        np.testing.assert_allclose([f.effective[m] for m in MODALITIES], off[i], atol=1e-5)


# ---- explanations ----------------------------------------------------------------------


def test_reasons_quote_only_unusual_z_and_always_state_rules():
    r = reasons({"restricted_zone": 1.0, "cpu": 5.2, "temp": 0.4, "unfamiliar_1m": 7.0})
    text = " | ".join(r)
    assert "restricted zone" in text and "CPU load is 5.2" in text
    assert "temperature" not in text  # z = 0.4 is not worth saying
    assert r[0].startswith("entered")  # rule findings first, even beside a larger z-score
    assert "7.0" in r[1] and "5.2" in r[2]  # then statistical findings, largest z first


def test_explain_ranks_factors_and_disclaims_probability():
    ce = CorrelationEngine(WeightedSum(), CorrelationConfig(explain_above=0.0))
    evid = {"spatial": {"restricted_zone": 1.0}, "network": {"unfamiliar_1m": 8.0}}
    ce.update(scores(0, spatial=1.0, network=0.9, ev=evid))
    f = ce.update(scores(1, spatial=1.0, network=0.9, ev=evid))
    txt = explain(f)
    assert "restricted zone" in txt and "endpoints it does not normally use" in txt
    assert "not a probability" in txt
    ranked = factors(f)
    assert [r["modality"] for r in ranked][:2] == ["spatial", "network"]


# ---- alert lifecycle -------------------------------------------------------------------


def fused_at(sec, score, learning=False, dev="D-001"):
    ce = CorrelationEngine(MaxFusion(), CorrelationConfig(half_life_s=1e-9, explain_above=0.0))
    return ce.update(scores(sec, spatial=score, dev=dev, learning=learning))


def test_one_alert_per_incident_and_hysteresis_prevents_flapping():
    am = AlertManager(AlertConfig(raise_at=0.6, clear_at=0.4, clear_after_s=60))
    events = []
    for sec, sc in enumerate([0.1, 0.7, 0.65, 0.5, 0.7, 0.5, 0.65]):  # wobbles between 0.5 and 0.7
        events += am.update(fused_at(sec, sc))
    assert [e["event"] for e in events] == ["opened"]  # never closed, never re-opened


def test_alert_closes_only_after_staying_low_for_the_clear_period():
    am = AlertManager(AlertConfig(raise_at=0.6, clear_at=0.4, clear_after_s=60))
    ev = am.update(fused_at(0, 0.9))
    ev += am.update(fused_at(10, 0.2))
    ev += am.update(fused_at(40, 0.2))
    assert [e["event"] for e in ev] == ["opened"]
    ev += am.update(fused_at(80, 0.2))
    assert [e["event"] for e in ev] == ["opened", "closed"] and ev[-1]["ended"]
    # A brief dip that recovers resets the clock.
    am2 = AlertManager(AlertConfig(clear_after_s=60))
    out = am2.update(fused_at(0, 0.9)) + am2.update(fused_at(30, 0.2)) + am2.update(fused_at(40, 0.7)) + am2.update(fused_at(120, 0.7))
    assert [e["event"] for e in out] == ["opened"]


def test_escalation_is_reported_and_peak_is_kept():
    am = AlertManager()
    ev = am.update(fused_at(0, 0.65)) + am.update(fused_at(1, 0.9)) + am.update(fused_at(2, 0.7))
    assert [e["event"] for e in ev] == ["opened", "escalated"]
    assert ev[-1]["severity"] == "high" and ev[-1]["peak_score"] == 0.9


def test_no_alert_while_a_baseline_is_still_learning():
    assert AlertManager().update(fused_at(0, 0.95, learning=True)) == []


def test_alert_id_is_deterministic():
    a = AlertManager().update(fused_at(5, 0.9))[0]["alert_id"]
    b = AlertManager().update(fused_at(5, 0.9))[0]["alert_id"]
    assert a == b


def test_store_upsert_updates_rather_than_duplicates():
    st = EventStore()
    base = dict(alert_id="a1", device_id="D-001", started="t", ended=None, peak_score=0.7, severity="medium")
    st.upsert_alert(base)
    st.upsert_alert({**base, "peak_score": 0.9, "severity": "high", "ended": "t2"})
    rows = st.alerts("D-001")
    assert len(rows) == 1 and rows[0]["peak_score"] == 0.9 and rows[0]["ended"] == "t2"


# ---- the whole chain on simulated data -------------------------------------------------


def _spatial_episode(cfg):
    start = cfg.start + timedelta(hours=13)
    return Episode(
        episode_id="E-1", device_id="D-001", scenario=ScenarioType.SPATIAL, start=start,
        end=start + timedelta(minutes=20), modalities=frozenset({Modality.SPATIAL}),
        magnitude=1.0, is_malicious=True, seed=9,
    )


def test_pipeline_raises_a_stored_explained_alert_and_replay_is_idempotent():
    cfg = SimConfig(seed=9, n_devices=2)
    ep = _spatial_episode(cfg)
    events = list(Fleet(cfg, [ep]).run(timedelta(hours=14)))
    det = DetectionConfig(occ_min_samples=500)
    published = []

    store = EventStore()
    pipe = Pipeline(cfg.device_ids(), MaxFusion(), store, det_cfg=det, on_alert=published.append)
    for t in events:
        pipe(t)

    alerts = store.alerts()
    assert [a["device_id"] for a in alerts] == ["D-001"]  # D-002 stayed quiet
    a = alerts[0]
    assert a["severity"] == "high" and "restricted zone" in a["explanation"]
    assert a["factors"][0]["modality"] == "spatial"
    assert ep.start <= datetime.fromisoformat(a["started"]) <= ep.end
    assert [e["event"] for e in published][0] == "opened"

    # Re-processing the same stream (a replay after a crash) must not create a second alert.
    pipe2 = Pipeline(cfg.device_ids(), MaxFusion(), store, det_cfg=det)
    for t in events:
        pipe2(t)
    assert len(store.alerts()) == 1


# ---- out-of-order and rewound clocks ------------------------------------------------------


def test_learned_fusion_attributes_a_saturated_score_to_every_active_signal():
    """Leave-one-out read ~0 for each of several redundant signals once the score saturated (the Phase 8 demo
    showed 'contributes 0.00'). The model's own additive structure shares the score out instead."""
    rng = np.random.default_rng(3)
    E = rng.random((3000, 4)) * (rng.random((3000, 4)) > 0.6)
    y = ((E[:, 0] > 0.5) | (E[:, 1] > 0.5)).astype(int)  # either spatial OR network is enough
    lf = LearnedFusion().fit(E, y)
    both = [1.0, 1.0, 0.0, 0.0]
    s = lf.score(both)
    assert s > 0.97  # saturated: each signal alone would already suffice
    loo = super(LearnedFusion, lf).contributions(both)
    assert max(loo.values()) < 0.1  # the generic method sees ~nothing to attribute
    c = lf.contributions(both)
    assert c[Modality.SPATIAL] > 0.2 and c[Modality.NETWORK] > 0.2  # the model's method credits both
    assert sum(c.values()) == pytest.approx(s)  # and the shares add up to the score
    assert c[Modality.DEVICE] == 0.0 and c[Modality.PROXIMITY] == 0.0
    assert all(v == 0.0 for v in lf.contributions([0, 0, 0, 0]).values())


def test_an_open_alert_is_updated_when_another_signal_joins_even_if_the_score_is_saturated():
    am = AlertManager(AlertConfig(raise_at=0.6, clear_at=0.4, clear_after_s=60))

    def at(sec, spatial=0.0, network=0.0, device=0.0, score_fusion=MaxFusion()):
        ce = CorrelationEngine(score_fusion, CorrelationConfig(half_life_s=1e-9, explain_above=0.0))
        return ce.update(scores(sec, spatial=spatial, network=network, device=device, ev={"network": {"unfamiliar_1m": 7.0}}))

    ev = am.update(at(0, spatial=1.0))
    assert [e["event"] for e in ev] == ["opened"] and [f["modality"] for f in ev[0]["factors"]] == ["spatial"]
    ev = am.update(at(5, spatial=1.0))  # nothing new: no event
    assert ev == []
    ev = am.update(at(10, spatial=1.0, network=0.9))  # the score cannot rise (already 1.0), but a signal joined
    assert [e["event"] for e in ev] == ["updated"]
    assert {f["modality"] for f in ev[0]["factors"]} == {"spatial", "network"}  # the explanation now shows both
    assert any("endpoints" in r for f in ev[0]["factors"] for r in f["reasons"])
    ev = am.update(at(15, spatial=1.0, network=0.9, device=0.8))
    assert [e["event"] for e in ev] == ["updated"] and len(ev[0]["factors"]) == 3
    assert am.update(at(20, spatial=1.0, network=0.9, device=0.8)) == []  # no change, no event


def test_close_all_ends_every_open_alert_at_its_last_seen_moment():
    am = AlertManager(AlertConfig(raise_at=0.6, clear_at=0.4, clear_after_s=60))
    am.update(fused_at(100, 0.9, dev="D-001"))
    am.update(fused_at(100, 0.9, dev="D-002"))
    am.update(fused_at(130, 0.8, dev="D-001"))  # D-001 was last seen 30 s later
    events = {e["device_id"]: e for e in am.close_all()}
    assert set(events) == {"D-001", "D-002"} and all(e["event"] == "closed" for e in events.values())
    assert events["D-001"]["ended"] > events["D-002"]["ended"]
    assert am.close_all() == []  # idempotent: nothing left


def test_stale_open_alerts_from_a_dead_process_are_closed_at_startup(tmp_path):
    st = EventStore(str(tmp_path / "s.db"))
    st.insert_event(Telemetry(device_id="D-001", timestamp=datetime(2026, 10, 5, 12, 5, tzinfo=timezone.utc), latitude=18.52, longitude=73.85,
                              battery=80, cpu_usage=1, memory_usage=1, temperature=30, uptime_s=1, network_connections=1, bytes_sent=1, bytes_received=1))
    st.upsert_alert({"alert_id": "D-001@x", "device_id": "D-001", "started": "2026-10-05T12:00:00+00:00", "ended": None,
                     "peak_score": 0.99, "severity": "high", "event": "opened"})
    st.upsert_alert({"alert_id": "D-009@y", "device_id": "D-009", "started": "2026-10-05T12:00:00+00:00", "ended": None,
                     "peak_score": 0.8, "severity": "medium", "event": "opened"})  # no events stored for this device
    assert st.close_stale_alerts() == 2
    a = st.get_alert("D-001@x")
    assert a["status"] == "closed" and a["ended"] == "2026-10-05T12:05:00+00:00" and a["event"] == "closed"  # ends at the device's newest event
    assert st.get_alert("D-009@y")["ended"] == "2026-10-05T12:00:00+00:00"  # no events: ends at its own start
    assert st.query_alerts(status="open") == [] and st.close_stale_alerts() == 0


def test_alert_manager_closes_an_open_alert_when_a_device_is_reset():
    am = AlertManager(AlertConfig(raise_at=0.6, clear_at=0.4, clear_after_s=60))
    assert [e["event"] for e in am.update(fused_at(100, 0.9))] == ["opened"]
    closed = am.reset_device("D-001")
    assert [e["event"] for e in closed] == ["closed"] and closed[0]["ended"]
    assert am.reset_device("D-001") == []  # nothing left to close
    assert [e["event"] for e in am.update(fused_at(10, 0.9))] == ["opened"]  # a new incident can open


def test_pipeline_drops_slightly_late_events_and_resets_on_a_big_clock_jump():
    from backend.services.pipeline import MAX_LATE_S

    cfg = SimConfig(seed=9, n_devices=2)
    ep = _spatial_episode(cfg)
    events = [t for t in Fleet(cfg, [ep]).run(timedelta(hours=14))]
    published, store = [], EventStore()
    pipe = Pipeline(cfg.device_ids(), MaxFusion(), store, det_cfg=DetectionConfig(occ_min_samples=500), on_alert=published.append)
    cut = next(i for i, t in enumerate(events) if t.device_id == "D-001" and t.timestamp >= ep.start + timedelta(minutes=4))
    for t in events[:cut]:
        pipe.process(t)
    assert [e["event"] for e in published] == ["opened"]  # the intrusion is under way, alert open
    last = events[cut - 1] if events[cut - 1].device_id == "D-001" else events[cut - 2]

    late = last.model_copy(update={"timestamp": last.timestamp - timedelta(seconds=MAX_LATE_S - 10)})
    assert pipe.process(late) is None and pipe.late_events == 1  # slightly late: dropped, state untouched
    assert [e["event"] for e in published] == ["opened"]

    rewound = last.model_copy(update={"timestamp": last.timestamp - timedelta(hours=3)})
    assert pipe.process(rewound) is not None and pipe.clock_resets == 1  # a big jump: a new epoch, analysis continues
    assert [e["event"] for e in published] == ["opened", "closed"]  # the stranded alert was closed, not left open forever
    assert store.alerts("D-001")[0]["ended"] is not None

    for _ in range(3):  # the device can keep going afterwards: later events are accepted again
        rewound = rewound.model_copy(update={"timestamp": rewound.timestamp + timedelta(seconds=5)})
        assert pipe.process(rewound) is not None
    assert pipe.clock_resets == 1


def test_pipeline_cost_per_event_does_not_grow_after_a_clock_jump():
    """The measured failure was 46 -> 583 -> 2607 us/event. After the fix it stays flat."""
    import time

    cfg = SimConfig(seed=9, n_devices=2)
    events = [t for t in Fleet(cfg, []).run(timedelta(hours=3)) if t.device_id == "D-001"]
    pipe = Pipeline(cfg.device_ids(), MaxFusion(), None, det_cfg=DetectionConfig(occ_min_samples=500))
    for t in events:
        pipe.process(t)

    def per_event(shift_h):
        rows = [t.model_copy(update={"timestamp": t.timestamp - timedelta(hours=shift_h)}) for t in events]
        t0 = time.perf_counter()
        for r in rows:
            pipe.process(r)
        return (time.perf_counter() - t0) / len(rows)

    first, second = per_event(48), per_event(96)  # two successive jumps back
    assert second < 3 * first + 5e-5, (first, second)  # a growing window would make `second` many times larger


# ---- dataset and the Isolation Forest control -----------------------------------------


@pytest.fixture(scope="module")
def small_rows(tmp_path_factory):
    rs = build_rows(seed=3, n_devices=3, hours=15, warmup_hours=10, episodes_per_device_per_day=30)
    path = tmp_path_factory.mktemp("rows") / "r.npz"
    rs.save(path)
    return rs, path


def test_rowset_roundtrip(small_rows):
    rs, path = small_rows
    back = RowSet.load(path)
    assert len(back) == len(rs) and back.episodes == rs.episodes
    np.testing.assert_array_equal(back.raw, rs.raw)
    assert rs.raw.shape == (len(rs), 4) and rs.feat.shape[0] == len(rs)


def test_labels_follow_the_episode_plan(small_rows):
    rs, _ = small_rows
    assert len(rs.episodes) > 3
    start = SimConfig(seed=3).start.timestamp() + 10 * 3600
    assert rs.ts.min() >= start  # warm-up rows are not included
    mal = rs.label == 1
    assert mal.any()
    for i in np.flatnonzero(mal)[::25]:
        assert rs.episodes[rs.episode[i]].is_malicious
    benign = (rs.scenario == list(ScenarioType).index(ScenarioType.HARD_NEGATIVE))
    assert (rs.label[benign] == 0).all()  # benign look-alikes are labelled 0 but still tagged
    assert rs.ignore.any() and not (rs.ignore & (rs.label == 1)).any()


def test_apply_fusion_matches_row_by_row():
    eff = np.random.default_rng(0).random((50, 4))
    out = apply_fusion(NoisyOr(), eff)
    assert out[7] == pytest.approx(NoisyOr().score(eff[7]))


def test_every_vectorised_fusion_equals_its_per_row_version():
    rng = np.random.default_rng(4)
    E = rng.random((300, 4)) * (rng.random((300, 4)) > 0.5)
    y = (E[:, 0] * E[:, 1] > 0.2).astype(int)
    fusions = [MaxFusion(), NoisyOr(), WeightedSum(), WeightedSum({Modality.SPATIAL: 5, Modality.NETWORK: 1, Modality.DEVICE: 2, Modality.PROXIMITY: 0}),
               SingleSignal(Modality.DEVICE), LearnedFusion().fit(E, y)]
    for f in fusions:
        batch = f.score_batch(E)
        np.testing.assert_allclose(batch, [f.score(r) for r in E], atol=1e-9, err_msg=f.name)
        assert (batch >= 0).all() and (batch <= 1).all()


def test_rowset_carries_hard_and_soft_scores(small_rows):
    rs, _ = small_rows
    assert rs.raw_soft.shape == rs.raw.shape
    # soft keeps weak evidence, so it is nonzero in strictly more places than hard
    assert (rs.raw_soft > 0).sum() > (rs.raw > 0).sum()
    # spatial is rule-based: soft == hard exactly
    np.testing.assert_array_equal(rs.raw_soft[:, 0], rs.raw[:, 0])


def test_supervised_early_fusion_control_learns_a_label_it_is_given():
    from ml.supervised import EarlyFusionSupervised

    rng = np.random.default_rng(2)
    n = 4000
    ts = np.arange(n, dtype=float) * 5
    dev = np.zeros(n, dtype=np.int16)
    feat = np.abs(rng.normal(0, 0.5, (n, 12))).astype(np.float32)
    y = np.zeros(n, dtype=int)
    y[3000:3200] = 1
    feat[3000:3200, 3] += 4.0
    m = EarlyFusionSupervised(half_life_s=10).fit(ts, dev, feat, y, mask=np.arange(n) < 3600)
    sc = m.score(ts, dev, feat)
    assert sc[3000:3200].mean() > 0.9 and sc[:3000].mean() < 0.2


def test_isolation_forest_control_ranks_outliers_above_ordinary_rows():
    rng = np.random.default_rng(1)
    n = 3000
    ts = np.arange(n, dtype=float) * 5
    dev = np.zeros(n, dtype=np.int16)
    feat = np.abs(rng.normal(0, 0.5, (n, 12))).astype(np.float32)
    feat[2500:2520] += 6.0  # twenty extreme rows
    iff = EarlyFusionIF(n_estimators=50).fit(ts, dev, feat, train_mask=np.arange(n) < 2000)
    sc = iff.score(ts, dev, feat)
    assert ((sc >= 0) & (sc <= 1)).all()
    assert sc[2500:2520].mean() > 0.95 and sc[:2000].mean() < 0.6
