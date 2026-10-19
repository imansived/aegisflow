"""Pipeline tests against a REAL Redis (docker compose -f docker-compose.dev.yml up -d). Skipped if unreachable."""

import uuid
from datetime import datetime, timedelta, timezone

import pytest
import redis

from backend.database.store import EventStore
from backend.streaming import bus
from backend.streaming.processor import Processor
from backend.streaming.publisher import publish
from contracts.telemetry import Telemetry
from simulator.config import SimConfig
from simulator.fleet import Fleet

T0 = datetime(2026, 10, 5, 12, 0, tzinfo=timezone.utc)


@pytest.fixture
def r():
    try:
        client = bus.connect()
        client.ping()
    except redis.ConnectionError:
        pytest.skip("Redis not running")
    yield client
    client.close()


@pytest.fixture
def names(r):
    n = uuid.uuid4().hex[:8]
    s = {"stream": f"test:{n}:telemetry", "dead_letter": f"test:{n}:dead"}
    yield s
    r.delete(s["stream"], s["dead_letter"])


def ev(device="D-001", sec=0, **over) -> Telemetry:
    base = dict(
        device_id=device, timestamp=T0 + timedelta(seconds=sec), latitude=18.52, longitude=73.85,
        battery=80, cpu_usage=30, memory_usage=50, temperature=40, uptime_s=100,
        network_connections=5, bytes_sent=1000, bytes_received=2000,
    )
    base.update(over)
    return Telemetry(**base)


def make(r, names, store=None, handler=None, **kw):
    return Processor(r, store or EventStore(), handler, **names, **kw)


def drain(p, rounds=10):
    for _ in range(rounds):
        if not p.run_once(block_ms=50):
            break


def test_roundtrip_stores_every_event(r, names):
    p = make(r, names)
    publish(r, [ev(sec=i) for i in range(50)], names["stream"])
    drain(p)
    assert p.store.count() == 50 and p.metrics.processed == 50
    assert bus.stats(r, names["stream"])["pending"] == 0
    assert "p99_ms" in p.metrics.summary()


def test_duplicate_delivery_is_stored_once_and_handler_runs_once(r, names):
    seen = []
    p = make(r, names, handler=seen.append)
    publish(r, [ev(sec=1), ev(sec=1)], names["stream"])  # same device + event time twice
    drain(p)
    assert p.store.count() == 1 and len(seen) == 1
    assert p.metrics.duplicates == 1
    assert bus.stats(r, names["stream"])["pending"] == 0  # both acknowledged


def test_invalid_messages_go_to_dead_letter_and_do_not_block(r, names):
    p = make(r, names)
    r.xadd(names["stream"], {"data": "not json at all"})
    r.xadd(names["stream"], {"data": ev().model_dump_json().replace('"battery":80.0', '"battery":900')})
    r.xadd(names["stream"], {"nodata": "x"})
    publish(r, [ev(sec=5)], names["stream"])
    drain(p)
    assert p.store.count() == 1  # the good one still got through
    assert p.metrics.invalid == 3
    assert r.xlen(names["dead_letter"]) == 3
    assert bus.stats(r, names["stream"])["pending"] == 0


def test_crashed_consumer_work_is_recovered(r, names):
    publish(r, [ev(sec=i) for i in range(10)], names["stream"])
    crashed = make(r, names, consumer="dies")
    # It reads (so Redis marks them delivered to it) but never processes or acks: a crash.
    r.xreadgroup(crashed.group, "dies", {names["stream"]: ">"}, count=10)
    assert bus.stats(r, names["stream"])["pending"] == 10

    rescuer = make(r, names, consumer="rescuer", store=crashed.store)
    assert rescuer.recover(min_idle_ms=0) == 10
    assert rescuer.store.count() == 10
    assert bus.stats(r, names["stream"])["pending"] == 0


def test_recovery_after_crash_between_commit_and_ack_does_not_double_process(r, names):
    seen = []
    p = make(r, names, handler=seen.append)
    publish(r, [ev(sec=i) for i in range(5)], names["stream"])
    # Simulate: stored + handled, but the XACK never happened.
    msgs = r.xreadgroup(p.group, p.consumer, {names["stream"]: ">"}, count=5)[0][1]
    p.store.begin()
    for _, f in msgs:
        t = Telemetry.model_validate_json(f["data"])
        p.store.insert_event(t)
        seen.append(t)
    p.store.commit()
    assert p.recover(min_idle_ms=0) == 5
    assert p.store.count() == 5
    assert len(seen) == 5  # handler NOT run a second time
    assert p.metrics.duplicates == 5


def test_failing_handler_leaves_message_pending_then_retry_succeeds(r, names):
    calls = {"n": 0}

    def flaky(t):
        calls["n"] += 1
        if calls["n"] == 1:
            raise RuntimeError("boom")

    p = make(r, names, handler=flaky)
    publish(r, [ev(sec=1)], names["stream"])
    drain(p)
    assert p.metrics.failed == 1
    assert p.store.count() == 0  # rolled back: the insert did not survive the failure
    assert bus.stats(r, names["stream"])["pending"] == 1

    p.recover(min_idle_ms=0)
    assert p.store.count() == 1 and bus.stats(r, names["stream"])["pending"] == 0


def test_one_bad_message_does_not_roll_back_its_batch_mates(r, names):
    def picky(t):
        if t.device_id == "D-002":
            raise RuntimeError("no")

    p = make(r, names, handler=picky)
    publish(r, [ev("D-001", 1), ev("D-002", 1), ev("D-003", 1)], names["stream"])
    drain(p)
    assert {t for t in ("D-001", "D-003") if p.store.events_for(t)} == {"D-001", "D-003"}
    assert p.store.events_for("D-002") == []


def test_poison_message_is_dead_lettered_after_max_deliveries(r, names):
    def always_fails(t):
        raise RuntimeError("poison")

    p = make(r, names, handler=always_fails, max_deliveries=2)
    publish(r, [ev(sec=1)], names["stream"])
    drain(p)
    for _ in range(5):
        p.recover(min_idle_ms=0)
    assert r.xlen(names["dead_letter"]) == 1
    assert p.metrics.dead_lettered == 1
    assert bus.stats(r, names["stream"])["pending"] == 0
    assert p.store.count() == 0


def test_future_timestamp_rejected_only_when_enabled(r, names):
    future = ev(sec=0, timestamp=T0 + timedelta(days=30))
    fixed_now = T0.timestamp()
    strict = make(r, names, max_future_skew_s=300, clock=lambda: fixed_now)
    publish(r, [future], names["stream"])
    drain(strict)
    assert strict.store.count() == 0 and r.xlen(names["dead_letter"]) == 1


def test_replay_mode_accepts_far_future_event_times(r, names):
    p = make(r, names)  # max_future_skew_s=None
    publish(r, [ev(sec=0, timestamp=T0 + timedelta(days=30))], names["stream"])
    drain(p)
    assert p.store.count() == 1


def test_maxlen_caps_stream_growth(r, names):
    publish(r, [ev(sec=i) for i in range(2000)], names["stream"], maxlen=100)
    assert r.xlen(names["stream"]) < 600  # approximate trimming: bounded, not exact


def test_stats_report_lag(r, names):
    p = make(r, names)
    publish(r, [ev(sec=i) for i in range(30)], names["stream"])
    before = bus.stats(r, names["stream"])
    assert before["length"] == 30 and before["lag"] == 30
    drain(p)
    assert bus.stats(r, names["stream"])["lag"] == 0


def test_simulator_to_store_end_to_end(r, names):
    cfg = SimConfig(seed=4, n_devices=5)
    rows = list(Fleet(cfg).run(timedelta(minutes=3)))
    p = make(r, names)
    publish(r, rows, names["stream"])
    while p.run_once(block_ms=50):
        pass
    assert p.store.count() == len(rows) == 5 * 36
    assert len(p.store.events_for("D-003")) == 36
