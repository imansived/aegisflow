"""R3/R4 end-to-end check against the RUNNING Docker stack (declared signal sets and observation-aware alert wording).

  python scripts/smoke_observation.py [report.json]      # reads AEGIS_* from the environment or .env, talks to http://localhost:$AEGIS_PORT/api

A network-only device (declares `signals: ["network"]`) is registered and driven through the real HTTP ingest -> Redis -> processor -> detectors ->
fusion -> alerts path with event-time timestamps, so its baselines fill in about 13 minutes of wall clock at the 2 events per second per-device limit.
Checks: the declaration is stored and returned; the device is not Degraded for signals it never declared; a network spike raises an alert whose
record and text say the alert rests on network telemetry only; a declared signal that disappears is Degraded (missing) and then recovering; an
undeclared signal that is sent anyway is ignored. Nothing here touches the detector, the models or the research artifacts.
"""
import json
import os
import random
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import httpx


def env(name):
    if os.environ.get(name):
        return os.environ[name]
    for line in Path(".env").read_text().splitlines():
        if line.startswith(name + "="):
            return line.split("=", 1)[1].strip()
    sys.exit(f"{name} not set")


BASE = f"http://localhost:{os.environ.get('AEGIS_PORT') or '8080'}/api"
ADMIN, VIEWER = env("AEGIS_ADMIN_TOKEN"), env("AEGIS_VIEWER_TOKEN")
AH, VH = {"Authorization": f"Bearer {ADMIN}"}, {"Authorization": f"Bearer {VIEWER}"}
DEV = "D-932"
report: dict = {"device": DEV, "checks": [], "started": datetime.now(timezone.utc).isoformat()}
c = httpx.Client(base_url=BASE, timeout=20)


def ok(name, detail=""):
    report["checks"].append({"check": name, "ok": True, "detail": detail})
    print(f"  ok  {name}" + (f"  [{detail}]" if detail else ""))


def fail(name, detail):
    report["checks"].append({"check": name, "ok": False, "detail": detail})
    print(f"FAIL  {name}  [{detail}]")
    finish(1)


def finish(code):
    report["finished"] = datetime.now(timezone.utc).isoformat()
    report["passed"] = code == 0
    if len(sys.argv) > 1:
        Path(sys.argv[1]).write_text(json.dumps(report, indent=1))
    print("OBSERVATION CHECK OK" if code == 0 else "OBSERVATION CHECK FAILED")
    sys.exit(code)


def live():
    r = c.get(f"/devices/{DEV}", headers=VH)
    return (r.json() or {}).get("live") if r.status_code == 200 else None


def wait_live(pred, timeout=60):
    end = time.time() + timeout
    snap = None
    while time.time() < end:
        snap = live()
        if snap and pred(snap):
            return snap
        time.sleep(1)
    return snap


print("1. declarations are stored, returned and validated")
for bad in ([], ["wifi"], ["network", "network"]):
    r = c.post("/devices", headers=AH, json={"device_id": "D-939", "name": "x", "signals": bad})
    if r.status_code != 422:
        fail("invalid declaration rejected", f"{bad} -> {r.status_code}")
ok("empty, unknown and repeated declarations are rejected (422)")
r = c.post("/devices", headers=AH, json={"device_id": DEV, "name": "Network-only camera", "type": "camera", "signals": ["network"]})
if r.status_code != 201:
    fail("register network-only device", f"{r.status_code} {r.text[:120]}")
key = r.json()["api_key"]
d = c.get(f"/devices/{DEV}", headers=VH).json()
lst = {x["device_id"]: x for x in c.get("/devices", headers=VH).json()}
if d.get("signals") != ["network"] or lst[DEV].get("signals") != ["network"]:
    fail("declaration returned", f"{d.get('signals')} / {lst[DEV].get('signals')}")
ok("declared signals=['network'] is stored and returned by GET /devices and /devices/{id}")

base = datetime.now(timezone.utc).replace(microsecond=0) - timedelta(hours=3)
tick = [0]
rng = random.Random(7)
H = {"X-Device-Key": key}


def body(lost=(), spike=False, extra=None):
    tick[0] += 1
    t = base + timedelta(seconds=5 * tick[0])
    b = {"device_id": DEV, "timestamp": t.isoformat().replace("+00:00", "Z"),
         "latitude": None, "longitude": None, "battery": None, "cpu_usage": None, "memory_usage": None, "temperature": None, "uptime_s": None,
         "network_connections": 5 + rng.randint(0, 2) + (400 if spike else 0), "bytes_sent": 1000 + rng.randint(0, 200) + (60_000_000 if spike else 0),
         "bytes_received": 2000 + rng.randint(0, 300), "remote_endpoints": ["10.0.0.1"] + ([f"203.0.113.{i}" for i in range(1, 60)] if spike else []),
         "nearby_devices": None}
    if "network" in lost:
        b.update(network_connections=None, bytes_sent=None, bytes_received=None, remote_endpoints=None)
    if extra:
        b.update(extra)
    return b


def send(b):
    while True:
        r = c.post("/telemetry", headers=H, json=b)
        if r.status_code == 202:
            time.sleep(0.52)  # stay under the 2 events per second per-device limit
            return
        if r.status_code == 429:
            time.sleep(float(r.headers.get("Retry-After", "1")))
            continue
        fail("telemetry accepted", f"{r.status_code} {r.text[:160]}")


print("2. a network-only device is learned and is not Degraded for signals it never declared (about 13 minutes)")
# The network detector's unfamiliar-endpoint baseline starts after 3600 s of event time and then needs 720 samples, so a device reports
# `learning` for about 1440 events; alerts are (rightly) withheld until it stops. Warm until the device says it is done, with a cap.
sent_n = 0
snap = None
while sent_n < 1900:
    for _ in range(100):
        send(body())
        sent_n += 1
    print(f"      {sent_n} events sent")
    snap = wait_live(lambda s: True, 30)
    if snap and snap.get("learning") is False:
        break
if not snap:
    fail("live snapshot", "no snapshot for the device")
if snap.get("learning") is not False:
    fail("device finishes learning", f"still learning after {sent_n} events")
report["events_to_finish_learning"] = sent_n
report["snapshot_quiet"] = snap
if snap.get("observability") != "full" or snap.get("state") != "normal":
    fail("network-only device is not Degraded", f"observability={snap.get('observability')} state={snap.get('state')} learning={snap.get('learning')}")
if sorted(snap.get("unsupported", [])) != ["device", "proximity", "spatial"] or snap.get("missing") or snap.get("recovering"):
    fail("unsupported/missing/recovering split", f"unsupported={snap.get('unsupported')} missing={snap.get('missing')} recovering={snap.get('recovering')}")
ok("observability full, state normal, unsupported = device+proximity+spatial, nothing missing or recovering", f"learning={snap.get('learning')}")

print("3. an undeclared signal that is sent anyway is ignored")
before = wait_live(lambda s: True)
send(body(extra={"latitude": 18.52, "longitude": 73.85}))
after = wait_live(lambda s: s["ts"] != before["ts"], 20)
if after is None or "spatial" not in after.get("unsupported", []) or after.get("observability") != "full":
    fail("undeclared location ignored", f"{after and (after.get('unsupported'), after.get('observability'))}")
ok("location sent by a device that never declared it is treated as unsupported (state unchanged)")

print("4. a network spike raises an alert that says it rests on network telemetry only")
for _ in range(14):
    send(body(spike=True))
alerts = []
end = time.time() + 60
while time.time() < end and not alerts:
    alerts = c.get("/alerts", headers=VH, params={"device_id": DEV}).json()
    time.sleep(2)
if not alerts:
    snap = live()
    fail("alert raised for the spike", f"no alert; last score={snap and snap.get('score')} severity={snap and snap.get('severity')}")
a = alerts[0]
pl = a.get("payload") or a
obs, expl = pl.get("observation", ""), pl.get("explanation", "")
report["alert"] = {k: pl.get(k) for k in ("alert_id", "severity", "peak_score", "unavailable", "unsupported", "missing", "recovering", "observation")}
if sorted(pl.get("unsupported", [])) != ["device", "proximity", "spatial"] or pl.get("missing") or pl.get("recovering"):
    fail("alert record names the unsupported signals", str(report["alert"]))
if "not supported by this device" not in obs or "based on network telemetry only" not in obs or "neither confirm nor rule out" not in obs:
    fail("observation wording", obs)
if obs not in expl or "Observation:" not in expl:
    fail("explanation carries the observation line", expl[-300:])
ok("alert record: unsupported = device+proximity+spatial; text: " + obs[:90] + "...")

print("5. a declared signal that disappears is Degraded (missing) and then recovering")
for _ in range(20):
    send(body())  # calm again
send(body(lost=("network",)))
s_lost = wait_live(lambda s: s.get("missing") == ["network"], 20)
if not s_lost or s_lost.get("state") != "degraded" or s_lost.get("observability") not in ("blind", "degraded"):
    fail("lost declared signal is Degraded", f"{s_lost and (s_lost.get('state'), s_lost.get('observability'), s_lost.get('missing'))}")
ok("network lost: state degraded, missing = ['network'], observability " + s_lost["observability"])
send(body())
s_back = wait_live(lambda s: s.get("recovering") == ["network"], 20)
if not s_back or s_back.get("missing"):
    fail("signal back is recovering", f"{s_back and (s_back.get('missing'), s_back.get('recovering'))}")
ok("network back: recovering = ['network'], judgment withheld (alertable=" + str(s_back.get("alertable")) + ")")
finish(0)
