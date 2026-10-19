"""API tests: real Redis, real SQLite files, FastAPI TestClient. Skipped if Redis is not running."""

import asyncio
import time
import uuid
from datetime import datetime, timedelta, timezone

import pytest
import redis
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from backend.api.app import create_app
from backend.api.config import ConfigError, Settings
from backend.api.demo import DemoError
from backend.api.hub import Hub
from backend.api.security import RateLimiter, constant_time_equal, hash_key, role_of
from backend.correlation.fusion import LearnedFusion
from backend.database.store import EventStore
from backend.detection.config import DetectionConfig
from backend.streaming import bus
from backend.streaming.publisher import publish
from contracts.scenarios import Episode, Modality, ScenarioType
from contracts.telemetry import Telemetry
from simulator.config import SimConfig
from simulator.fleet import Fleet

ADMIN, VIEWER = "A" * 24, "V" * 24
AH, VH = {"Authorization": f"Bearer {ADMIN}"}, {"Authorization": f"Bearer {VIEWER}"}
T0 = datetime(2026, 10, 5, 12, 0, tzinfo=timezone.utc)
DET = DetectionConfig(occ_min_samples=500)  # the same shortcut the earlier end-to-end tests use


@pytest.fixture
def r():
    try:
        c = bus.connect()
        c.ping()
    except redis.ConnectionError:
        pytest.skip("Redis not running")
    yield c
    c.close()


def make_settings(tmp_path, **over):
    n = uuid.uuid4().hex[:8]
    # fusion is pinned so the tests do not change behaviour when models/frozen_design.json exists
    base = dict(db_path=str(tmp_path / "t.db"), stream=f"test:{n}:t", dead_letter=f"test:{n}:d", group="g",
                auth=True, admin_token=ADMIN, viewer_token=VIEWER, run_processor=False, fusion="noisy_or")
    base.update(over)
    return Settings(**base)


@pytest.fixture
def api(tmp_path, r):
    s = make_settings(tmp_path)
    app = create_app(s, det_cfg=DET)
    with TestClient(app) as client:
        yield client, app.state.runtime, s
    r.delete(s.stream, s.dead_letter)


def ev(device="D-001", sec=0, **over):
    base = dict(device_id=device, timestamp=(T0 + timedelta(seconds=sec)).isoformat(), latitude=18.5205, longitude=73.8568,
                battery=80, cpu_usage=30, memory_usage=50, temperature=40, uptime_s=100,
                network_connections=5, bytes_sent=1000, bytes_received=2000)
    base.update(over)
    return base


def register(client, device_id="D-001"):
    resp = client.post("/devices", headers=AH, json={"device_id": device_id, "name": "Badge", "type": "badge"})
    assert resp.status_code == 201, resp.text
    return resp.json()["api_key"]


# ---- settings fail closed --------------------------------------------------------------------


def test_settings_refuse_to_start_without_tokens_or_with_weak_ones(tmp_path):
    db = str(tmp_path / "x.db")
    with pytest.raises(ConfigError, match="AEGIS_ADMIN_TOKEN"):
        Settings(db_path=db, auth=True).validate()
    with pytest.raises(ConfigError, match="differ"):
        Settings(db_path=db, auth=True, admin_token="x" * 20, viewer_token="x" * 20).validate()
    with pytest.raises(ConfigError, match="16"):
        Settings(db_path=db, auth=True, admin_token="short", viewer_token="alsoshort").validate()
    with pytest.raises(ConfigError, match="in-memory"):
        Settings(db_path=":memory:", auth=False).validate()
    Settings(db_path=db, auth=False).validate()  # explicit opt-out for a local demo is allowed


# ---- security primitives ---------------------------------------------------------------------


def test_role_resolution_and_constant_time_compare():
    s = Settings(auth=True, admin_token=ADMIN, viewer_token=VIEWER)
    assert role_of(ADMIN, s) == "admin" and role_of(VIEWER, s) == "viewer"
    assert role_of("nope", s) is None and role_of(None, s) is None and role_of("", s) is None
    assert role_of(None, Settings(auth=False)) == "admin"
    assert constant_time_equal("abc", "abc") and not constant_time_equal("abc", "abd")
    assert hash_key("k") != "k" and len(hash_key("k")) == 64


def test_rate_limiter_refills_over_time_and_bounds_memory():
    now = [0.0]
    rl = RateLimiter(rate=2.0, burst=3, clock=lambda: now[0], max_keys=5)
    assert [rl.allow("a") for _ in range(4)] == [True, True, True, False]  # burst of 3, then refused
    assert rl.retry_after("a") == pytest.approx(0.5)
    now[0] += 0.5
    assert rl.allow("a") and not rl.allow("a")  # half a second refills exactly one token
    assert rl.allow("b")  # keys are independent
    for i in range(20):
        rl.allow(f"k{i}")
    assert len(rl._buckets) <= 5  # inventing keys cannot grow the table without limit


def test_hub_queue_is_bounded_and_drops_the_oldest():
    hub = Hub(maxsize=3)
    c = hub.connect()
    for i in range(5):
        hub._fanout({"n": i})
    assert [c.queue.get_nowait()["n"] for _ in range(3)] == [2, 3, 4] and c.dropped == 2


# ---- authentication and roles ----------------------------------------------------------------


def test_health_is_open_everything_else_needs_a_token(api):
    client, *_ = api
    assert client.get("/health").json() == {"status": "ok"}
    for path in ("/devices", "/alerts", "/anomalies/live", "/anomalies/history", "/system/status", "/demo/status"):
        resp = client.get(path)
        assert resp.status_code == 401 and resp.headers["www-authenticate"] == "Bearer", path
        assert client.get(path, headers={"Authorization": "Bearer wrong-token-value"}).status_code == 401


def test_me_reports_the_callers_role(api):
    client, *_ = api
    assert client.get("/me").status_code == 401
    assert client.get("/me", headers=VH).json() == {"role": "viewer", "auth": True}
    assert client.get("/me", headers=AH).json() == {"role": "admin", "auth": True}


def test_site_endpoint_serves_the_zone_geometry_the_detectors_use(api):
    client, *_ = api
    assert client.get("/site").status_code == 401
    site = client.get("/site", headers=VH).json()
    zones = {z["name"]: z for z in site["zones"]}
    assert set(zones) == {"Office", "MeetingRoom", "Lab", "ServerRoom"}
    assert zones["ServerRoom"]["kind"] == "restricted" and zones["Office"]["kind"] == "allowed"
    assert all(len(z["polygon"]) == 4 for z in zones.values())
    assert site["origin"] == {"latitude": 18.5204, "longitude": 73.8567}


def test_viewer_can_read_but_only_admin_can_change_things(api):
    client, *_ = api
    assert client.get("/devices", headers=VH).status_code == 200
    body = {"device_id": "D-001", "name": "Badge"}
    assert client.post("/devices", headers=VH, json=body).status_code == 403
    assert client.post("/demo/start", headers=VH, json={}).status_code == 403
    assert client.post("/demo/inject", headers=VH, json={"device_id": "D-001", "scenario": "spatial"}).status_code == 403
    assert client.post("/devices", headers=AH, json=body).status_code == 201


def test_device_registration_returns_the_key_once_and_stores_only_a_hash(api):
    client, rt, s = api
    key = register(client)
    assert key.startswith("ak_") and len(key) > 20
    assert client.post("/devices", headers=AH, json={"device_id": "D-001", "name": "again"}).status_code == 409
    listing = client.get("/devices", headers=VH).json()
    assert listing[0]["device_id"] == "D-001" and listing[0]["has_key"] is True and key not in str(listing)
    assert key not in str(client.get("/devices/D-001", headers=VH).json())
    db = EventStore(s.db_path)
    assert db.get_device("D-001")["key_hash"] == hash_key(key) != key
    db.close()
    assert "D-001" in rt.enrolled  # shared by reference with the detection engine


def test_bad_device_ids_are_rejected_before_anything_happens(api):
    client, *_ = api
    for bad in ("X-1", "D-12", "d-001", "D-0011", "'; DROP TABLE devices;--"):
        assert client.post("/devices", headers=AH, json={"device_id": bad, "name": "x"}).status_code == 422
    assert client.get("/alerts", headers=VH, params={"device_id": "nope"}).status_code == 422
    assert client.get("/devices/D-404", headers=VH).status_code == 404


# ---- telemetry ingestion -------------------------------------------------------------------------


def test_ingestion_needs_the_right_device_key(api):
    client, rt, s = api
    key = register(client, "D-001")
    other = register(client, "D-002")
    ok = client.post("/telemetry", headers={"X-Device-Key": key}, json=ev("D-001"))
    assert ok.status_code == 202 and ok.json() == {"accepted": True}
    bad = client.post("/telemetry", headers={"X-Device-Key": "ak_wrong"}, json=ev("D-001"))
    ghost = client.post("/telemetry", headers={"X-Device-Key": key}, json=ev("D-099"))
    cross = client.post("/telemetry", headers={"X-Device-Key": other}, json=ev("D-001"))  # D-002's key, D-001's id
    none = client.post("/telemetry", json=ev("D-001"))
    assert bad.status_code == ghost.status_code == cross.status_code == none.status_code == 401
    assert bad.json() == ghost.json()  # an unknown device and a wrong key look identical: no id enumeration
    # An admin token is not a device key.
    assert client.post("/telemetry", headers={"X-Device-Key": ADMIN}, json=ev("D-001")).status_code == 401


def test_malformed_telemetry_is_rejected_not_queued(api, r):
    client, rt, s = api
    key = register(client)
    for bad in (ev(battery=900), ev(latitude=200), ev(nearby_devices=["D-001"]), {**ev(), "evil": "x"}, ev(timestamp="2026-10-05T12:00:00")):
        assert client.post("/telemetry", headers={"X-Device-Key": key}, json=bad).status_code == 422
    assert r.xlen(s.stream) == 0


def test_accepted_telemetry_flows_through_redis_into_the_store_and_the_api(api):
    client, rt, s = api
    key = register(client)
    for i in range(5):
        assert client.post("/telemetry", headers={"X-Device-Key": key}, json=ev(sec=i * 5)).status_code == 202
    assert rt.drain() == 5
    events = client.get("/devices/D-001/events", headers=VH, params={"limit": 3}).json()
    assert len(events) == 3 and events[0]["timestamp"] > events[1]["timestamp"]  # newest first
    assert client.get("/devices/D-001", headers=VH).json()["live"]["device_id"] == "D-001"
    st = client.get("/system/status", headers=VH).json()
    assert st["processor"]["processed"] == 5 and st["stream"]["pending"] == 0 and st["fusion"] == "noisy_or"
    assert st["correlation"] == {"half_life_s": 1.0, "score_type": "soft"}  # the frozen design (manual §5.7)


def test_per_device_rate_limit_and_failed_attempt_limit(tmp_path, r):
    s = make_settings(tmp_path, telemetry_rate_per_s=0.001, telemetry_burst=3)
    app = create_app(s, det_cfg=DET)
    with TestClient(app) as client:
        key = register(client)
        codes = [client.post("/telemetry", headers={"X-Device-Key": key}, json=ev(sec=i)).status_code for i in range(6)]
        assert codes == [202, 202, 202, 429, 429, 429]
        # A client hammering wrong keys is cut off with 429 instead of being allowed unlimited guesses.
        guesses = [client.post("/telemetry", headers={"X-Device-Key": f"ak_{i}"}, json=ev()).status_code for i in range(30)]
        assert guesses[0] == 401 and guesses[-1] == 429
    r.delete(s.stream, s.dead_letter)


def test_read_api_rate_limit_returns_retry_after(tmp_path, r):
    s = make_settings(tmp_path, api_rate_per_s=0.001, api_burst=3)
    with TestClient(create_app(s, det_cfg=DET)) as client:
        codes = [client.get("/devices", headers=VH).status_code for _ in range(5)]
        assert codes[:3] == [200, 200, 200] and codes[3] == 429
        assert int(client.get("/devices", headers=VH).headers["retry-after"]) >= 1
    r.delete(s.stream, s.dead_letter)


# ---- alerts end to end ------------------------------------------------------------------------------


def _restricted_entry_events():
    cfg = SimConfig(seed=9, n_devices=2)
    start = cfg.start + timedelta(hours=13)
    ep = Episode(episode_id="E-1", device_id="D-001", scenario=ScenarioType.SPATIAL, start=start,
                 end=start + timedelta(minutes=20), modalities=frozenset({Modality.SPATIAL}),
                 magnitude=1.0, is_malicious=True, seed=9)
    return cfg, ep, list(Fleet(cfg, [ep]).run(timedelta(hours=14)))


def test_simulated_intrusion_becomes_a_stored_explained_alert_visible_over_the_api(api, r):
    client, rt, s = api
    cfg, ep, events = _restricted_entry_events()
    register(client, "D-001")
    register(client, "D-002")
    publish(r, events, s.stream, maxlen=None)
    rt.drain(max_rounds=500)

    alerts = client.get("/alerts", headers=VH).json()
    assert [a["device_id"] for a in alerts] == ["D-001"]  # D-002 stayed quiet
    a = alerts[0]
    assert a["severity"] in ("medium", "high") and "restricted zone" in a["explanation"]
    assert a["factors"][0]["modality"] == "spatial" and a["status"] in ("open", "closed")
    assert ep.start <= datetime.fromisoformat(a["started"]) <= ep.end

    one = client.get(f"/alerts/{a['alert_id']}", headers=VH)  # ids contain ':' and '+': the path route must cope
    assert one.status_code == 200 and one.json()["alert_id"] == a["alert_id"]
    assert client.get("/alerts/does-not-exist", headers=VH).status_code == 404
    assert client.get("/alerts", headers=VH, params={"device_id": "D-002"}).json() == []
    assert client.get("/alerts", headers=VH, params={"status": "closed"}).json() == [x for x in alerts if x["status"] == "closed"]

    hist = client.get("/anomalies/history", headers=VH, params={"device_id": "D-001"}).json()
    assert hist and max(h["score"] for h in hist) >= 0.69  # the intrusion is in the history
    assert client.get("/anomalies/live", headers=VH).json()[0]["score"] >= client.get("/anomalies/live", headers=VH).json()[-1]["score"]


# ---- WebSocket ---------------------------------------------------------------------------------------


def test_websocket_needs_a_token_then_sends_a_snapshot_and_live_messages(api):
    client, rt, s = api
    with pytest.raises(WebSocketDisconnect) as bad:
        with client.websocket_connect("/ws?token=wrong"):
            pass
    assert bad.value.code == 4401
    with pytest.raises(WebSocketDisconnect):
        with client.websocket_connect("/ws"):
            pass

    with client.websocket_connect(f"/ws?token={VIEWER}") as ws:  # viewers may watch
        snap = ws.receive_json()
        assert snap["type"] == "snapshot" and snap["live"] == [] and snap["open_alerts"] == []
        rt.hub.publish({"type": "alert", "event": "opened", "device_id": "D-001"})
        msg = ws.receive_json()
        assert msg == {"type": "alert", "event": "opened", "device_id": "D-001"}
    assert client.get("/system/status", headers=VH).json()["websocket_clients"] == 0  # cleaned up on disconnect


def test_websocket_receives_the_real_alert_from_the_pipeline(api, r):
    client, rt, s = api
    cfg, ep, events = _restricted_entry_events()
    register(client, "D-001")
    register(client, "D-002")
    publish(r, events, s.stream, maxlen=None)
    with client.websocket_connect(f"/ws?token={VIEWER}") as ws:
        assert ws.receive_json()["type"] == "snapshot"
        rt.drain(max_rounds=500)
        seen = []
        deadline = time.time() + 10
        while time.time() < deadline:
            m = ws.receive_json()
            seen.append(m)
            if m["type"] == "alert" and m["event"] == "opened":
                break
        alert = seen[-1]
        assert alert["device_id"] == "D-001" and "restricted zone" in alert["explanation"]
        assert any(m["type"] == "score" for m in seen)  # score updates flowed too


def test_severity_bands_follow_the_alert_thresholds_so_red_means_alert(api):
    client, rt, s = api
    st = client.get("/system/status", headers=VH).json()
    bands, th = st["severity_bands"], st["alert_thresholds"]
    assert bands["high"] == th["raise_at"] and bands["medium"] == th["clear_at"] and bands["low"] == pytest.approx(th["clear_at"] / 2)
    # A score just under the alert level must NOT read as "High" (the bug the dashboard screenshot exposed).
    from backend.correlation.engine import severity
    assert severity(th["raise_at"] - 0.01, rt.corr_cfg) == "medium"
    assert severity(th["raise_at"], rt.corr_cfg) == "high"
    assert severity(th["clear_at"] / 4, rt.corr_cfg) == "normal"


def test_a_request_connection_can_be_closed_on_a_different_thread_than_it_was_opened_on(tmp_path):
    """Regression: FastAPI may open a dependency on one worker thread and close it on another. With SQLite's
    default thread check that raised ProgrammingError and returned 500 on a real server, while the single-threaded
    test client hid it (found by driving the dashboard in a real browser)."""
    from concurrent.futures import ThreadPoolExecutor

    path = str(tmp_path / "x.db")
    relaxed = EventStore(path, check_same_thread=False)
    with ThreadPoolExecutor(1) as pool:
        pool.submit(relaxed.list_devices).result()   # used on another thread
        pool.submit(relaxed.close).result()          # closed on another thread: fine
    strict = EventStore(path)  # the processor's write connection stays strict
    with ThreadPoolExecutor(1) as pool, pytest.raises(Exception, match="same thread"):
        pool.submit(strict.list_devices).result()
    strict.close()


def test_many_concurrent_requests_do_not_fail(api):
    from concurrent.futures import ThreadPoolExecutor

    client, *_ = api
    register(client)
    with ThreadPoolExecutor(8) as pool:
        codes = list(pool.map(lambda _: client.get("/devices", headers=VH).status_code, range(60)))
    assert set(codes) <= {200, 429}  # 429 only if the per-token rate limit kicked in; never a 500
    assert codes.count(200) > 0


# ---- learned fusion artefact -------------------------------------------------------------------------


def test_learned_fusion_survives_a_save_and_load():
    import numpy as np

    rng = np.random.default_rng(0)
    E = rng.random((500, 4))
    y = (E[:, 0] * E[:, 1] > 0.3).astype(int)
    lf = LearnedFusion().fit(E, y)
    back = LearnedFusion.from_dict(lf.to_dict())
    assert all(back.score(row) == pytest.approx(lf.score(row)) for row in E[:20])
    with pytest.raises(RuntimeError):
        LearnedFusion().to_dict()


def test_fusion_choice_follows_the_exported_design_and_fails_loudly_when_it_is_missing(tmp_path):
    import numpy as np
    from backend.api.runtime import DEFAULT_ALERT_THRESHOLDS, choose_fusion

    rng = np.random.default_rng(1)
    E = rng.random((300, 4))
    lf = LearnedFusion().fit(E, (E[:, 0] > 0.7).astype(int))
    design = {"fusion": {"learned": lf.to_dict()}, "thresholds": {"learned": 0.95, "noisy_or": 0.7}}

    auto = Settings(db_path=str(tmp_path / "d.db"), auth=False, fusion="auto")
    f, cfg = choose_fusion(auto, design)
    assert f.name == "learned" and cfg.raise_at == 0.95 and cfg.clear_at < cfg.raise_at  # hysteresis preserved
    f, cfg = choose_fusion(auto, None)  # no design file: fall back to the label-free fusion with default thresholds
    assert f.name == "noisy_or" and cfg == DEFAULT_ALERT_THRESHOLDS["noisy_or"]
    f, cfg = choose_fusion(Settings(db_path=str(tmp_path / "d.db"), auth=False, fusion="noisy_or"), design)
    assert f.name == "noisy_or" and cfg.raise_at == 0.7
    with pytest.raises(RuntimeError, match="export_design"):
        choose_fusion(Settings(db_path=str(tmp_path / "d.db"), auth=False, fusion="learned"), None)


# ---- demo -----------------------------------------------------------------------------------------------


def test_demo_refuses_to_inject_before_it_is_live_and_validates_input(api):
    client, rt, s = api
    assert client.post("/demo/inject", headers=AH, json={"device_id": "D-001", "scenario": "spatial"}).status_code == 409
    assert client.post("/demo/start", headers=AH, json={"devices": 1}).status_code == 422
    assert client.post("/demo/start", headers=AH, json={"warmup_hours": 1}).status_code == 422
    assert client.post("/demo/start", headers=AH, json={}).status_code == 409  # processor is off in this fixture
    assert client.get("/demo/status", headers=VH).json()["state"] == "idle"


def test_demo_injection_and_clear_change_what_a_device_does(api):
    client, rt, s = api
    demo = client.app.state.demo
    demo.cfg = SimConfig(seed=3, n_devices=3)
    demo.fleet = Fleet(demo.cfg, [])
    demo.sim_now, demo.state = demo.cfg.start + timedelta(hours=10), "live"
    info = demo.inject("D-001", "spatial", 1.0, 120)
    t = demo.sim_now + timedelta(seconds=10)
    on = lambda dev: {m for e in demo.fleet._effects(dev, t) for m in e.modalities}
    assert on("D-001") == {Modality.SPATIAL} and on("D-002") == set()
    assert info["scenario"] == "spatial" and info["episode_id"] == "DEMO-0001"
    assert demo.clear("D-001") == 1
    assert on("D-001") == set()  # cleared: the device returns to its routine
    for bad in (lambda: demo.inject("D-099", "spatial"), lambda: demo.inject("D-001", "warp"),
                lambda: demo.inject("D-001", "hard_negative"), lambda: demo.inject("D-001", "spatial", 5.0),
                lambda: demo.clear("D-099")):
        with pytest.raises(DemoError):
            bad()
    staged = demo.inject("D-002", "staged", 0.2, 100)
    assert staged["scenario"] == "staged" and any(e.windows_s for e in demo.fleet.episodes_by_device["D-002"])


@pytest.mark.slow
def test_live_demo_runs_the_whole_system_and_an_injected_intrusion_raises_an_alert(tmp_path, r):
    s = make_settings(tmp_path, run_processor=True)
    app = create_app(s, det_cfg=DET)
    with TestClient(app) as client:
        assert client.post("/demo/start", headers=AH, json={"devices": 2, "warmup_hours": 12, "speed": 60}).status_code == 202
        deadline = time.time() + 120
        while client.get("/demo/status", headers=VH).json()["state"] != "live":
            assert time.time() < deadline, client.get("/demo/status", headers=VH).json()
            assert client.get("/demo/status", headers=VH).json()["state"] != "error"
            time.sleep(0.5)
        assert len(client.get("/devices", headers=VH).json()) == 2  # the fleet was registered and enrolled
        assert client.post("/demo/inject", headers=AH, json={"device_id": "D-001", "scenario": "spatial", "magnitude": 1.0, "duration_s": 300}).status_code == 201

        deadline, found = time.time() + 90, []
        while not found and time.time() < deadline:
            found = client.get("/alerts", headers=VH, params={"device_id": "D-001"}).json()
            time.sleep(0.5)
        assert found and "restricted zone" in found[0]["explanation"]
        assert client.get("/alerts", headers=VH, params={"device_id": "D-002"}).json() == []

        assert client.post("/demo/clear", headers=AH, json={"device_id": "D-001"}).json()["cleared"] >= 1
        client.post("/demo/stop", headers=AH)
        assert client.get("/demo/status", headers=VH).json()["state"] == "idle"
    r.delete(s.stream, s.dead_letter)
