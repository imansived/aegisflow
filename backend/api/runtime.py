"""The live system: Redis stream -> processor thread -> analysis pipeline -> store + WebSocket.

Everything that touches the SQLite WRITE connection lives in one place and one thread (the
processor's). API request threads open their own short-lived READ connections (WAL mode lets
readers and the writer proceed together).
"""

import json
import threading
import time
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path

import redis

from backend.correlation.engine import FROZEN_CORRELATION, CorrelationConfig
from backend.correlation.thresholds import ThresholdTable
from backend.correlation.fusion import Fusion, LearnedFusion, NoisyOr
from backend.database.store import EventStore
from backend.detection.config import DetectionConfig
from backend.services.alerts import AlertConfig
from backend.services.pipeline import Pipeline
from backend.streaming import bus
from backend.streaming.processor import Processor
from backend.streaming.publisher import publish
from contracts.site import SITE_ORIGIN, zone_of
from contracts.telemetry import Telemetry

from .config import Settings
from .hub import Hub

# Alert thresholds chosen on VALIDATION seeds at the 0.1% ordinary-tick budget (the F1 stage-1
# thresholds, manual §5.7). They depend on the fusion, because the two score on different scales.
DEFAULT_ALERT_THRESHOLDS = {
    "noisy_or": AlertConfig(raise_at=0.69, clear_at=0.45, clear_after_s=60.0),
    "learned": AlertConfig(raise_at=0.98, clear_at=0.90, clear_after_s=60.0),
}
SCORE_SAMPLE_EVERY_S = 10.0  # history keeps one point per device per 10 s, plus every elevated one
SCORE_PUSH_EVERY_S = 1.0  # WebSocket score updates per device are throttled to ~1/s of wall time


def load_design(path: Path) -> dict | None:
    try:
        return json.loads(path.read_text())
    except (OSError, ValueError):
        return None


def choose_fusion(settings: Settings, design: dict | None) -> tuple[Fusion, AlertConfig]:
    want = settings.fusion
    have_learned = bool(design and design.get("fusion", {}).get("learned"))
    if want == "learned" and not have_learned:
        raise RuntimeError(f"AEGIS_FUSION=learned but {settings.design_path} has no learned fusion; run `python -m ml.export_design`")
    if want == "learned" or (want == "auto" and have_learned):
        thr = (design or {}).get("thresholds", {}).get("learned")
        cfg = AlertConfig(raise_at=thr, clear_at=max(0.0, thr - 0.08), clear_after_s=60.0) if thr else DEFAULT_ALERT_THRESHOLDS["learned"]
        return LearnedFusion.from_dict(design["fusion"]["learned"]), cfg
    thr = (design or {}).get("thresholds", {}).get("noisy_or")
    cfg = AlertConfig(raise_at=thr, clear_at=max(0.0, thr - 0.24), clear_after_s=60.0) if thr else DEFAULT_ALERT_THRESHOLDS["noisy_or"]
    return NoisyOr(), cfg


def _iso(ts: datetime) -> str:
    return ts.astimezone(timezone.utc).isoformat()


def build_snapshot(t: Telemetry, f, prev: dict | None) -> dict:
    """The dashboard's view of one device after one event. Pure, so it can be tested without Redis or a server."""
    lost = t.unavailable
    stale = "spatial" in lost
    if stale:  # no fix: keep the last known position, flagged as stale, rather than inventing one
        lat, lon = (prev["latitude"], prev["longitude"]) if prev else SITE_ORIGIN
    else:
        lat, lon = t.latitude, t.longitude
    zone = zone_of((lat, lon))
    n_near = None if t.nearby_devices is None else len(t.nearby_devices)
    anomalous = f.severity != "normal"
    snap = {
        "device_id": t.device_id, "ts": _iso(t.timestamp),
        "latitude": lat, "longitude": lon, "position_stale": stale,
        "zone": zone.name if zone else None, "restricted": bool(zone and zone.kind.value == "restricted") and not stale,
        # null = unavailable this tick (never a silent zero)
        "battery": t.battery, "cpu_usage": t.cpu_usage, "temperature": t.temperature,
        "network_connections": t.network_connections, "nearby_devices": n_near,
        "memory_usage": t.memory_usage, "bytes_sent": t.bytes_sent, "bytes_received": t.bytes_received,
        "score": round(f.score, 4), "severity": f.severity,
        "effective": {m.value: round(v, 3) for m, v in f.effective.items()},
        "contributions": {m.value: round(v, 3) for m, v in f.contributions.items()},
        "learning": f.learning,
        # Observability: how much of this device was actually seen. A quiet score on a partly seen device is
        # NOT "normal": the state says Degraded, with the missing signals named.
        # `unavailable` = what the device reported as null PLUS what a detector is withholding judgment on while its
        # window refills after an outage; `recovering` is the second part, so the dashboard can say which is which.
        # R3 splits it: `unsupported` = never declared (not a fault), `missing` = declared but null, `recovering` = back, still withheld.
        "unavailable": sorted(m.value for m in f.unavailable), "recovering": sorted(m.value for m in f.recovering),
        "unsupported": sorted(m.value for m in f.unsupported), "missing": sorted(m.value for m in f.missing),
        "observability": f.observability, "alertable": f.alertable, "fallback": f.fallback,
        "state": "anomalous" if anomalous else ("normal" if f.observability == "full" else "degraded"),
    }
    return snap


class Runtime:
    def __init__(self, settings: Settings, hub: Hub | None = None, det_cfg: DetectionConfig | None = None,
                 corr_cfg: CorrelationConfig | None = None):
        self.settings = settings
        self.hub = hub or Hub()
        self.det_cfg = det_cfg
        self.design = load_design(settings.design_path)
        self.fusion, self.alert_cfg = choose_fusion(settings, self.design)
        # Availability thresholds were calibrated for the LEARNED fusion only; for any other fusion a partly
        # observed device takes the conservative fallback (no alert, reported as Degraded).
        self.table = ThresholdTable.load(settings.thresholds_path) if self.fusion.name == "learned" else None
        # Severity bands follow the ALERT thresholds, so a red device and an alert mean the same thing:
        # High = at or above the alert level, Medium = above the clear level (elevated, close to alerting),
        # Low = above half of it. The spec's example bands (0.3/0.6/0.8) are not tuned to any fusion; with a
        # learned fusion that saturates near 0 and 1 they showed "High" for scores that never alerted.
        a = self.alert_cfg
        self.corr_cfg = corr_cfg or replace(FROZEN_CORRELATION, low=0.5 * a.clear_at, medium=a.clear_at, high=a.raise_at)
        self.redis = bus.connect(settings.redis_url)
        self.started_at = time.time()

        # The registry is shared BY REFERENCE with the detection engine: registering a device makes it
        # "enrolled" at once, so its neighbours stop counting as unknown devices.
        self.enrolled: set[str] = set()
        Path(settings.db_path).parent.mkdir(parents=True, exist_ok=True)
        reader = EventStore(settings.db_path)
        devices = reader.list_devices()
        self.enrolled.update(d["device_id"] for d in devices)
        # Signal groups each device supports (R3). Only devices that declared a subset have an entry; shared by reference with the detectors.
        self.declared: dict[str, frozenset[str]] = {d["device_id"]: frozenset(d["signals"]) for d in devices if d["signals"] is not None}
        reader.close()

        self.live: dict[str, dict] = {}  # latest snapshot per device, for the dashboard
        self.warming = False  # while True, callbacks stay silent (the demo's fast-forward warm-up)
        self.lock = threading.Lock()  # serialises pipeline access between the processor and the demo warm-up

        self._thread: threading.Thread | None = None
        self._stop = threading.Event()
        self._processor: Processor | None = None
        self._pipeline: Pipeline | None = None
        self._store: EventStore | None = None
        self._last_sample: dict[str, float] = {}
        self._last_push: dict[str, tuple[float, str]] = {}

    # ---- construction (called in the thread that will own the write connection) ------------------

    def _new_pipeline(self) -> Pipeline:
        return Pipeline(
            self.enrolled, self.fusion, self._store,
            det_cfg=self.det_cfg, corr_cfg=self.corr_cfg, alert_cfg=self.alert_cfg, table=self.table,
            on_alert=self._on_alert, on_processed=self._on_processed, declared=self.declared,
        )

    def reset_analysis(self) -> None:
        """Start the analysis from scratch: fresh detectors, correlation and alert state.

        A new demo run replays simulated time from the beginning, so state left over from the previous run
        (baselines for a different fleet, windows at a later time) would be wrong for it. The stored events
        and alerts are kept; only the in-memory analysis restarts."""
        self.close_open_alerts()  # nothing will track the old ones any more
        with self.lock:
            self._pipeline = self._new_pipeline()
            self.live.clear()
            self._last_sample.clear()
            self._last_push.clear()
        # Tell every connected dashboard: the devices and score histories they hold belong to the OLD run and
        # would otherwise be drawn into the new one (a line across weeks of simulated time, seen in the browser).
        self.hub.publish({"type": "reset"})

    def close_open_alerts(self) -> int:
        """Close every open alert, store and announce it. Safe to call from any thread: it uses a short-lived
        connection, because the write connection belongs to the processor thread."""
        if self._pipeline is None:
            return 0
        with self.lock:
            events = self._pipeline.alerts.close_all()
        if events:
            db = EventStore(self.settings.db_path, check_same_thread=False)
            try:
                for ev in events:
                    db.upsert_alert(ev)
            finally:
                db.close()
            for ev in events:
                self.hub.publish({"type": "alert", **ev})
        return len(events)

    def _build(self) -> None:
        self._store = EventStore(self.settings.db_path)
        self._store.close_stale_alerts()  # a previous process may have died with alerts open
        self._pipeline = self._new_pipeline()

        def handler(t: Telemetry) -> None:
            with self.lock:
                self._pipeline.process(t)

        self._processor = Processor(
            self.redis, self._store, handler, stream=self.settings.stream, group=self.settings.group,
            dead_letter=self.settings.dead_letter, consumer="api-worker",
        )

    @property
    def pipeline(self) -> Pipeline:
        assert self._pipeline is not None, "runtime not started"
        return self._pipeline

    # ---- callbacks (run in the processor thread) -------------------------------------------------

    def _on_alert(self, ev: dict) -> None:
        if not self.warming:
            self.hub.publish({"type": "alert", **ev})

    def _on_processed(self, t: Telemetry, f) -> None:
        snap = build_snapshot(t, f, self.live.get(t.device_id))
        self.live[t.device_id] = snap
        if self.warming:
            return
        now = time.time()
        last = self._last_sample.get(t.device_id, 0.0)
        if f.score >= self.corr_cfg.low or now - last >= SCORE_SAMPLE_EVERY_S:
            self._last_sample[t.device_id] = now
            if self._store is not None:
                self._store.record_score(t.device_id, _iso(t.timestamp), f.score, f.severity, snap["effective"])
        pushed_at, pushed_sev = self._last_push.get(t.device_id, (0.0, ""))
        sev_key = f"{f.severity}/{f.observability}"
        if now - pushed_at >= SCORE_PUSH_EVERY_S or sev_key != pushed_sev:
            self._last_push[t.device_id] = (now, sev_key)
            self.hub.publish({"type": "score", **snap})

    # ---- lifecycle -------------------------------------------------------------------------------

    def start(self) -> None:
        if not self.settings.run_processor:
            return
        ready = threading.Event()

        def loop() -> None:
            self._build()
            ready.set()
            last_recover = 0.0
            while not self._stop.is_set():
                try:
                    if time.time() - last_recover > 30:
                        self._processor.recover(min_idle_ms=30_000)
                        last_recover = time.time()
                    self._processor.run_once(block_ms=500)
                except redis.RedisError:
                    time.sleep(1.0)  # Redis blipped: keep the thread alive and retry

        self._thread = threading.Thread(target=loop, name="aegis-processor", daemon=True)
        self._thread.start()
        ready.wait(timeout=10)

    def stop(self) -> None:
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=5)

    def drain(self, max_rounds: int = 50) -> int:
        """Process everything currently on the stream, in the CALLING thread. For tests and tools."""
        if self._processor is None:
            self._build()
        n = 0
        for _ in range(max_rounds):
            got = self._processor.run_once(block_ms=50)
            if not got:
                break
            n += got
        return n

    # ---- operations used by the API --------------------------------------------------------------

    def ingest(self, t: Telemetry) -> None:
        publish(self.redis, [t], self.settings.stream)

    def register_enrolled(self, device_id: str, signals: list[str] | None = None) -> None:
        self.enrolled.add(device_id)
        if signals is not None:
            self.declared[device_id] = frozenset(signals)

    def status(self) -> dict:
        p = self._processor
        full = (self.table.lookup(15) if self.table else None) or self.alert_cfg  # the complete-signal operating point
        return {
            "uptime_s": round(time.time() - self.started_at, 1),
            "fusion": self.fusion.name,
            "correlation": {"half_life_s": self.corr_cfg.half_life_s, "score_type": self.corr_cfg.score_type},
            "severity_bands": {"low": self.corr_cfg.low, "medium": self.corr_cfg.medium, "high": self.corr_cfg.high},
            "alert_thresholds": {"raise_at": full.raise_at, "clear_at": full.clear_at},
            "availability": {
                "thresholds_loaded": self.table is not None,
                "validated_patterns": sorted(self.table.entries) if self.table else [],
                "devices_degraded": sum(1 for d in self.live.values() if d.get("observability") != "full"),
            },
            "stream": bus.stats(self.redis, self.settings.stream, self.settings.group),
            "dead_letters": self.redis.xlen(self.settings.dead_letter),
            "processor": p.metrics.summary() if p else None,
            "late_events": self._pipeline.late_events if self._pipeline else 0,
            "clock_resets": self._pipeline.clock_resets if self._pipeline else 0,
            "devices_live": len(self.live),
            "websocket_clients": len(self.hub.clients),
        }
