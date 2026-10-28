"""The HTTP + WebSocket API (spec §21-22; manual §3.22).

Run:  AEGIS_ADMIN_TOKEN=... AEGIS_VIEWER_TOKEN=... uvicorn --factory backend.api.app:create_app
"""

import asyncio
from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI, Header, HTTPException, Query, Request, WebSocket
from fastapi.middleware.cors import CORSMiddleware
from fastapi.concurrency import run_in_threadpool
from pydantic import BaseModel, Field

from backend.database.store import EventStore
from contracts.site import SITE_ORIGIN, ZONES
from contracts.telemetry import DEVICE_ID_PATTERN, Telemetry

from .config import Settings
from .demo import DemoError, LiveSimulator
from .hub import Hub
from .runtime import Runtime
from .security import RateLimiter, hash_key, new_device_key, constant_time_equal, role_of

from datetime import datetime, timezone


class DeviceIn(BaseModel):
    device_id: str = Field(pattern=DEVICE_ID_PATTERN)
    name: str = Field(min_length=1, max_length=64)
    type: str = Field(default="generic", min_length=1, max_length=32)


class DemoStartIn(BaseModel):
    devices: int = Field(default=20, ge=2, le=50)
    warmup_hours: float = Field(default=34.0, ge=12, le=72)
    speed: float = Field(default=10.0, ge=0.5, le=60)


class InjectIn(BaseModel):
    device_id: str = Field(pattern=DEVICE_ID_PATTERN)
    scenario: str = Field(max_length=32)
    magnitude: float = Field(default=0.8, ge=0.0, le=1.0)
    duration_s: float = Field(default=120.0, ge=10, le=900)
    signals: list[str] | None = Field(default=None, max_length=4)  # for scenario signal_loss: which signals go unavailable


class ClearIn(BaseModel):
    device_id: str = Field(pattern=DEVICE_ID_PATTERN)


def create_app(settings: Settings | None = None, det_cfg=None) -> FastAPI:
    settings = settings or Settings.from_env()
    settings.validate()
    hub = Hub()
    rt = Runtime(settings, hub, det_cfg)
    demo = LiveSimulator(rt)
    api_limiter = RateLimiter(settings.api_rate_per_s, settings.api_burst)
    device_limiter = RateLimiter(settings.telemetry_rate_per_s, settings.telemetry_burst)
    fail_limiter = RateLimiter(2.0, 20)  # failed device-key attempts per client address

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        hub.bind(asyncio.get_running_loop())
        rt.start()
        yield
        demo.stop()
        rt.stop()

    app = FastAPI(title="AegisFlow", version="0.1.0", lifespan=lifespan,
                  description="Real-time cross-modal behavioural anomaly detection for connected devices.")
    app.state.runtime, app.state.demo, app.state.hub = rt, demo, hub
    app.add_middleware(
        CORSMiddleware, allow_origins=list(settings.cors_origins), allow_methods=["GET", "POST"],
        allow_headers=["Authorization", "Content-Type"],
    )

    # ---- auth and per-request resources --------------------------------------------------------

    def principal(request: Request) -> str:
        auth = request.headers.get("authorization", "")
        token = auth[7:] if auth.lower().startswith("bearer ") else None
        role = role_of(token, settings)
        if role is None:
            raise HTTPException(401, "missing or invalid token", headers={"WWW-Authenticate": "Bearer"})
        if not api_limiter.allow(hash_key(token or "anonymous")[:16]):
            raise HTTPException(429, "rate limit exceeded", headers={"Retry-After": str(int(api_limiter.retry_after(hash_key(token or '')[:16])) + 1)})
        return role

    def admin(role: str = Depends(principal)) -> str:
        if role != "admin":
            raise HTTPException(403, "this action needs the admin role")
        return role

    def store():
        # A short-lived connection for this request. FastAPI runs this generator's setup and teardown on
        # (possibly) different worker threads, hence check_same_thread=False (see EventStore).
        s = EventStore(settings.db_path, check_same_thread=False)
        try:
            yield s
        finally:
            s.close()

    # ---- public ----------------------------------------------------------------------------------

    @app.get("/health")
    def health() -> dict:
        return {"status": "ok"}

    # ---- devices ---------------------------------------------------------------------------------

    @app.post("/devices", status_code=201)
    def create_device(body: DeviceIn, _: str = Depends(admin), db: EventStore = Depends(store)) -> dict:
        key = new_device_key()
        stamp = datetime.now(timezone.utc).isoformat()
        if not db.register_device(body.device_id, body.name, body.type, stamp, hash_key(key)):
            raise HTTPException(409, f"device {body.device_id} already exists")
        rt.register_enrolled(body.device_id)
        # The key is returned ONCE. Only its hash is stored, so it cannot be shown again.
        return {"device_id": body.device_id, "api_key": key, "note": "store this key now; it cannot be retrieved later"}

    @app.get("/devices")
    def list_devices(_: str = Depends(principal), db: EventStore = Depends(store)) -> list[dict]:
        return [{**d, "live": rt.live.get(d["device_id"])} for d in db.list_devices()]

    @app.get("/devices/{device_id}")
    def get_device(device_id: str, _: str = Depends(principal), db: EventStore = Depends(store)) -> dict:
        d = db.get_device(device_id)
        if d is None:
            raise HTTPException(404, "unknown device")
        d.pop("key_hash", None)
        return {**d, "live": rt.live.get(device_id), "open_alerts": db.query_alerts(device_id, "open")}

    @app.get("/devices/{device_id}/events")
    def device_events(device_id: str, limit: int = Query(100, ge=1, le=1000), _: str = Depends(principal),
                      db: EventStore = Depends(store)) -> list[dict]:
        if db.get_device(device_id) is None:
            raise HTTPException(404, "unknown device")
        return [e.model_dump(mode="json") for e in db.latest_events(device_id, limit)]

    # ---- telemetry ingestion (device-authenticated) ---------------------------------------------

    @app.post("/telemetry", status_code=202)
    def ingest(t: Telemetry, request: Request, x_device_key: str = Header(default=""), db: EventStore = Depends(store)) -> dict:
        ip = request.client.host if request.client else "unknown"
        dev = db.get_device(t.device_id)
        # One message for "unknown device" and "wrong key": it must not reveal which ids exist.
        ok = bool(dev and dev["key_hash"] and x_device_key and constant_time_equal(hash_key(x_device_key), dev["key_hash"]))
        if not ok:
            if not fail_limiter.allow(ip):
                raise HTTPException(429, "too many failed attempts", headers={"Retry-After": "30"})
            raise HTTPException(401, "invalid device key")
        if not device_limiter.allow(t.device_id):
            raise HTTPException(429, "device is sending too fast", headers={"Retry-After": "1"})
        rt.ingest(t)
        return {"accepted": True}

    # ---- alerts and anomalies --------------------------------------------------------------------

    @app.get("/alerts")
    def alerts(device_id: str | None = Query(None, pattern=DEVICE_ID_PATTERN), status: str | None = Query(None, pattern="^(open|closed)$"),
               limit: int = Query(100, ge=1, le=500), _: str = Depends(principal), db: EventStore = Depends(store)) -> list[dict]:
        return db.query_alerts(device_id, status, limit)

    @app.get("/alerts/{alert_id:path}")
    def alert(alert_id: str, _: str = Depends(principal), db: EventStore = Depends(store)) -> dict:
        a = db.get_alert(alert_id)
        if a is None:
            raise HTTPException(404, "unknown alert")
        return a

    @app.get("/anomalies/live")
    def anomalies_live(_: str = Depends(principal)) -> list[dict]:
        return sorted(rt.live.values(), key=lambda s: s["score"], reverse=True)

    @app.get("/anomalies/history")
    def anomalies_history(device_id: str | None = Query(None, pattern=DEVICE_ID_PATTERN), limit: int = Query(500, ge=1, le=5000),
                          _: str = Depends(principal), db: EventStore = Depends(store)) -> list[dict]:
        return db.score_history(device_id, limit)

    @app.get("/me")
    def me(role: str = Depends(principal)) -> dict:
        """Who am I? The dashboard uses this to decide whether to show admin controls (the server still enforces them)."""
        return {"role": role, "auth": settings.auth}

    @app.get("/site")
    def site(_: str = Depends(principal)) -> dict:
        """The simulated site's zones, so the dashboard map draws the same geometry the detectors use."""
        return {
            "origin": {"latitude": SITE_ORIGIN[0], "longitude": SITE_ORIGIN[1]},
            "zones": [
                {"name": z.name, "kind": z.kind.value, "polygon": [{"latitude": p[0], "longitude": p[1]} for p in z.polygon]}
                for z in ZONES
            ],
        }

    @app.get("/system/status")
    def system_status(_: str = Depends(principal)) -> dict:
        return {**rt.status(), "demo": demo.status()}

    # ---- demo (admin) ------------------------------------------------------------------------------

    def demo_call(fn, *args, **kw):
        try:
            return fn(*args, **kw)
        except DemoError as e:
            raise HTTPException(409, str(e)) from None

    @app.post("/demo/start", status_code=202)
    def demo_start(body: DemoStartIn, _: str = Depends(admin)) -> dict:
        demo_call(demo.start, body.devices, body.warmup_hours, body.speed)
        return demo.status()

    @app.post("/demo/stop")
    def demo_stop(_: str = Depends(admin)) -> dict:
        demo.stop()
        return demo.status()

    @app.get("/demo/status")
    def demo_status(_: str = Depends(principal)) -> dict:
        return demo.status()

    @app.post("/demo/inject", status_code=201)
    def demo_inject(body: InjectIn, _: str = Depends(admin)) -> dict:
        return demo_call(demo.inject, body.device_id, body.scenario, body.magnitude, body.duration_s, body.signals)

    @app.post("/demo/clear")
    def demo_clear(body: ClearIn, _: str = Depends(admin)) -> dict:
        return {"cleared": demo_call(demo.clear, body.device_id)}

    # ---- live updates ----------------------------------------------------------------------------

    @app.websocket("/ws")
    async def websocket_endpoint(websocket: WebSocket, token: str | None = Query(None)):
        if role_of(token, settings) is None:
            await websocket.close(code=4401)  # 4401 ~ unauthorised; the browser sees a failed connect
            return
        await websocket.accept()
        client = hub.connect()

        def open_alerts() -> list[dict]:
            s = EventStore(settings.db_path)
            try:
                return s.query_alerts(None, "open", 100)
            finally:
                s.close()

        async def sender() -> None:
            await websocket.send_json({"type": "snapshot", "live": list(rt.live.values()),
                                       "open_alerts": await run_in_threadpool(open_alerts)})
            while True:
                try:
                    msg = await asyncio.wait_for(client.queue.get(), timeout=25)
                except asyncio.TimeoutError:
                    msg = {"type": "ping"}  # heartbeat so dead connections are noticed
                await websocket.send_json(msg)

        async def receiver() -> None:
            while True:
                await websocket.receive_text()  # we ignore client chatter; this raises on disconnect

        tasks = [asyncio.create_task(sender()), asyncio.create_task(receiver())]
        try:
            await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
        finally:
            for t in tasks:
                t.cancel()
            hub.disconnect(client)

    return app
