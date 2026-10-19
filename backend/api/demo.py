"""Live demo driver: a simulated fleet that streams through the REAL pipeline (manual §3.22).

1. Register the fleet's devices (enrolled, no API keys).
2. Fast-forward `warmup_hours` of simulated time through the analysis pipeline directly, with alerts and
   WebSocket traffic silenced, so every device has baselines (a demo with no baselines alerts on nothing).
3. Go live: publish ticks to Redis at `speed` x real time. They travel the same path as real telemetry:
   stream -> processor -> pipeline -> store + WebSocket.

Scenarios can then be injected on a chosen device and cleared again (the spec's demo flow, §37).
"""

import threading
import time
from datetime import datetime, timedelta

import numpy as np

from backend.streaming.publisher import publish
from contracts.scenarios import SCENARIOS, Episode, ScenarioType
from simulator.config import SimConfig
from simulator.fleet import Fleet
from simulator.plan import staged_shape

from .runtime import Runtime

INJECTABLE = [t for t in ScenarioType if t not in (ScenarioType.NORMAL, ScenarioType.HARD_NEGATIVE)]


class DemoError(RuntimeError):
    pass


class LiveSimulator:
    def __init__(self, rt: Runtime):
        self.rt = rt
        self.state = "idle"  # idle | registering | warming | live | stopping | error
        self.error: str | None = None
        self.fleet: Fleet | None = None
        self.cfg: SimConfig | None = None
        self.sim_now: datetime | None = None
        self.speed = 10.0
        self.warm_progress = 0.0
        self._thread: threading.Thread | None = None
        self._stop = threading.Event()
        self._n = 0
        self._rng = np.random.default_rng(7)

    # ---- control ------------------------------------------------------------------------------

    def start(self, devices: int = 20, warmup_hours: float = 34.0, speed: float = 10.0, seed: int = 42) -> None:
        if self._thread and self._thread.is_alive():
            raise DemoError("the demo is already running")
        if not 2 <= devices <= 50:
            raise DemoError("devices must be between 2 and 50")
        if not 12 <= warmup_hours <= 72:
            raise DemoError("warmup_hours must be between 12 and 72 (baselines need about a working day)")
        if not 0.5 <= speed <= 60:
            raise DemoError("speed must be between 0.5 and 60 (x real time)")
        if self.rt._pipeline is None:
            raise DemoError("the processor is not running (AEGIS_RUN_PROCESSOR=off?)")
        self.speed, self.error, self.warm_progress = speed, None, 0.0
        self.cfg = SimConfig(seed=seed, n_devices=devices)
        self.fleet = Fleet(self.cfg, [])
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, args=(warmup_hours,), name="aegis-demo", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        if self._thread and self._thread.is_alive():
            self.state = "stopping"
            self._stop.set()
            self._thread.join(timeout=10)
        self.state = "idle"

    def status(self) -> dict:
        eps = []
        if self.fleet and self.sim_now:
            for dev, lst in self.fleet.episodes_by_device.items():
                eps += [{"episode_id": e.episode_id, "device_id": dev, "scenario": e.scenario.value, "magnitude": e.magnitude,
                         "ends_in_sim_s": round((e.end - self.sim_now).total_seconds())} for e in lst if e.end > self.sim_now]
        return {
            "state": self.state, "error": self.error, "devices": self.cfg.n_devices if self.cfg else 0,
            "speed": self.speed, "warmup_progress": round(self.warm_progress, 3),
            "sim_time": self.sim_now.isoformat() if self.sim_now else None, "active_episodes": eps,
        }

    # ---- scenarios ----------------------------------------------------------------------------

    def inject(self, device_id: str, scenario: str, magnitude: float = 0.8, duration_s: float = 120.0) -> dict:
        if self.state != "live" or not self.fleet or not self.sim_now:
            raise DemoError("the demo is not live yet")
        if device_id not in self.fleet.episodes_by_device:
            raise DemoError(f"unknown device {device_id}")
        try:
            stype = ScenarioType(scenario)
        except ValueError:
            raise DemoError(f"unknown scenario {scenario!r}") from None
        if stype not in INJECTABLE:
            raise DemoError(f"scenario {scenario!r} cannot be injected; choose from {[t.value for t in INJECTABLE]}")
        if not 0.0 <= magnitude <= 1.0 or not 10 <= duration_s <= 900:
            raise DemoError("magnitude must be in [0, 1] and duration_s in [10, 900]")

        spec = SCENARIOS[stype]
        variant, windows, length = "", {}, float(duration_s)
        if stype is ScenarioType.STAGED:
            variant, length, windows = staged_shape(self._rng)
        start = self.sim_now + timedelta(seconds=self.cfg.tick_s)
        self._n += 1
        ep = Episode(
            episode_id=f"DEMO-{self._n:04d}", device_id=device_id, scenario=stype, start=start,
            end=start + timedelta(seconds=length), modalities=spec.modalities, magnitude=magnitude,
            is_malicious=spec.is_malicious, seed=self.cfg.seed, variant=variant, windows_s=windows,
        )
        self.fleet.episodes_by_device[device_id] = [*self.fleet.episodes_by_device[device_id], ep]  # replace the list atomically
        return {"episode_id": ep.episode_id, "device_id": device_id, "scenario": stype.value, "magnitude": magnitude,
                "duration_s": round(length), "starts_at": ep.start.isoformat()}

    def clear(self, device_id: str) -> int:
        """End every active or future episode on the device now: it resumes its routine (spec §37 step 7)."""
        if self.state != "live" or not self.fleet or not self.sim_now:
            raise DemoError("the demo is not live yet")
        if device_id not in self.fleet.episodes_by_device:
            raise DemoError(f"unknown device {device_id}")
        now, kept, changed = self.sim_now, [], 0
        for e in self.fleet.episodes_by_device[device_id]:
            if e.end <= now:
                kept.append(e)
            else:
                changed += 1
                if e.start < now:
                    kept.append(e.model_copy(update={"end": now}))
        self.fleet.episodes_by_device[device_id] = kept
        return changed

    # ---- the thread ---------------------------------------------------------------------------

    def _run(self, warmup_hours: float) -> None:
        rt = self.rt
        try:
            self.state = "registering"
            from backend.database.store import EventStore
            from datetime import timezone

            store = EventStore(rt.settings.db_path)  # this thread's own connection
            stamp = datetime.now(timezone.utc).isoformat()
            for d in self.cfg.device_ids():
                store.register_device(d, f"Demo device {d}", "badge", stamp, None)
                rt.register_enrolled(d)
            store.close()

            self.state = "warming"
            rt.live.clear()
            gen = self.fleet.ticks(timedelta(days=3650))
            n_warm = int(warmup_hours * 3600 / self.cfg.tick_s)
            pipe = rt.pipeline
            saved_store, rt.warming, pipe.store = pipe.store, True, None  # silence alerts/WebSocket/DB for the fast-forward
            try:
                for i in range(n_warm):
                    if self._stop.is_set():
                        return
                    batch = next(gen)
                    with rt.lock:
                        for t in batch:
                            pipe.process(t)
                    if i % 200 == 0:
                        self.warm_progress = i / n_warm
                        self.sim_now = batch[0].timestamp
            finally:
                pipe.store, rt.warming = saved_store, False
            self.warm_progress = 1.0

            self.state = "live"
            interval = self.cfg.tick_s / self.speed
            next_at = time.monotonic()
            for batch in gen:
                if self._stop.is_set():
                    break
                self.sim_now = batch[0].timestamp
                publish(rt.redis, batch, rt.settings.stream)
                next_at += interval
                delay = next_at - time.monotonic()
                if delay > 0:
                    time.sleep(delay)
                elif delay < -5:  # fell far behind real time: do not try to catch up in a burst
                    next_at = time.monotonic()
        except Exception as e:  # noqa: BLE001 - surfaced through /demo/status rather than lost with the thread
            self.error, self.state = f"{type(e).__name__}: {e}", "error"
            return
        self.state = "idle"
