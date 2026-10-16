"""Pipeline commands.

  python -m backend.streaming.cli publish --data data/run1 [--speed 60]
  python -m backend.streaming.cli process --db data/events.db
  python -m backend.streaming.cli bench   [--devices 50 --minutes 30 --rate 2000]
  python -m backend.streaming.cli stats
"""

import argparse
import json
import platform
import threading
import time
import uuid
from datetime import timedelta
from pathlib import Path

import redis

from backend.database.store import EventStore
from simulator.config import SimConfig
from simulator.fleet import Fleet
from simulator.io import read_telemetry

from . import bus
from .processor import Processor
from .publisher import publish


def cmd_publish(a) -> None:
    r = bus.connect(a.redis)
    n, tick_s = 0, SimConfig().tick_s
    batch: list = []
    last = None
    for t in read_telemetry(Path(a.data) / "telemetry.jsonl"):
        if last is not None and t.timestamp != last:
            n += publish(r, batch)
            batch = []
            if a.speed > 0:
                time.sleep(tick_s / a.speed)  # pace: `speed`x faster than the simulated clock
        last = t.timestamp
        batch.append(t)
    n += publish(r, batch)
    print(f"published {n} events")


def cmd_process(a) -> None:
    r = bus.connect(a.redis)
    p = Processor(r, EventStore(a.db), consumer=a.consumer, max_future_skew_s=None)
    print(f"processing into {a.db} (Ctrl+C to stop)")
    last_print = time.time()
    try:
        while True:
            p.recover(min_idle_ms=30_000)
            p.run_once(block_ms=1000)
            if time.time() - last_print > 5:
                print(json.dumps(p.metrics.summary()), bus.stats(r))
                last_print = time.time()
    except KeyboardInterrupt:
        print("stopped", p.metrics.summary())


def cmd_stats(a) -> None:
    print(bus.stats(bus.connect(a.redis)))


def _bench_data(devices: int, minutes: int):
    return list(Fleet(SimConfig(seed=1, n_devices=devices)).run(timedelta(minutes=minutes)))


def cmd_bench(a) -> None:
    """Two measurements, deliberately separate:

    1. throughput - publish everything first, then see how fast the processor drains the
       backlog. Latency from this run is just queueing time, so it is NOT reported.
    2. latency under steady load - publish at a fixed rate while the processor runs; latency
       is publish-to-stored. Valid only if the processor keeps up (lag stays ~0).
    """
    rows = _bench_data(a.devices, a.minutes)
    r = bus.connect(a.redis)
    names = lambda: {"stream": f"bench:{uuid.uuid4().hex[:6]}", "dead_letter": f"bench:{uuid.uuid4().hex[:6]}:d"}  # noqa: E731

    n1 = names()
    publish(r, rows, n1["stream"], maxlen=None)
    p = Processor(r, EventStore(), **n1, batch=500)
    t0 = time.perf_counter()
    while p.run_once(block_ms=50):
        pass
    dt = time.perf_counter() - t0
    thr = len(rows) / dt
    r.delete(n1["stream"], n1["dead_letter"])

    n2 = names()
    p2 = Processor(r, EventStore(), **n2, batch=500)
    stop = threading.Event()

    def producer():
        chunk = max(1, int(a.rate / 20))  # 20 bursts per second
        for i in range(0, len(rows), chunk):
            if stop.is_set():
                return
            publish(r, rows[i : i + chunk], n2["stream"], maxlen=None)
            time.sleep(chunk / a.rate)

    th = threading.Thread(target=producer)
    th.start()
    while th.is_alive() or bus.stats(r, n2["stream"])["lag"]:
        p2.run_once(block_ms=20)
    th.join()
    lag_end = bus.stats(r, n2["stream"])
    r.delete(n2["stream"], n2["dead_letter"])

    print(json.dumps({
        "environment": {"python": platform.python_version(), "os": platform.platform(), "processor": platform.processor(), "redis": r.info("server")["redis_version"]},
        "events": len(rows),
        "backlog_throughput_events_per_s": round(thr),
        "steady_load": {"target_rate_per_s": a.rate, **p2.metrics.summary(), "end_lag": lag_end["lag"]},
    }, indent=2))


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--redis", default=bus.DEFAULT_URL)
    sub = ap.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("publish"); s.add_argument("--data", required=True); s.add_argument("--speed", type=float, default=0); s.set_defaults(f=cmd_publish)
    s = sub.add_parser("process"); s.add_argument("--db", default="data/events.db"); s.add_argument("--consumer", default="worker-1"); s.set_defaults(f=cmd_process)
    s = sub.add_parser("stats"); s.set_defaults(f=cmd_stats)
    s = sub.add_parser("bench"); s.add_argument("--devices", type=int, default=50); s.add_argument("--minutes", type=int, default=30); s.add_argument("--rate", type=int, default=2000); s.set_defaults(f=cmd_bench)
    a = ap.parse_args()
    a.f(a)


if __name__ == "__main__":
    main()
