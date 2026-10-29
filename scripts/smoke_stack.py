"""Checks the container-level guarantees of the Docker stack (manual §3.24). Run with the stack up:

  python scripts/smoke_stack.py            # reads AEGIS_* from the environment or .env

These are properties of the PACKAGING, not of the app (the app has its own tests): security headers, a bounded
request body, a client address clients cannot spoof, tokens kept out of logs, non-root containers, an API that is
not published to the host, and data that survives a restart.
"""
import asyncio
import json
import os
import subprocess
import sys
import time
from pathlib import Path

import httpx
import websockets


def env(name):
    if os.environ.get(name):
        return os.environ[name]
    for line in Path(".env").read_text().splitlines():
        if line.startswith(name + "="):
            return line.split("=", 1)[1].strip()
    sys.exit(f"{name} not set")


PORT = os.environ.get("AEGIS_PORT") or "8080"
BASE = f"http://localhost:{PORT}"
ADMIN, VIEWER = env("AEGIS_ADMIN_TOKEN"), env("AEGIS_VIEWER_TOKEN")
AH, VH = {"Authorization": f"Bearer {ADMIN}"}, {"Authorization": f"Bearer {VIEWER}"}


def ok(msg):
    print(f"  ok  {msg}")


def dc(*a):
    return subprocess.run(["docker", "compose", *a], capture_output=True, text=True, timeout=120)


TELEMETRY = {"timestamp": "2026-10-05T12:00:00Z", "latitude": 18.5205, "longitude": 73.8568, "battery": 50, "cpu_usage": 10,
             "memory_usage": 20, "temperature": 35, "uptime_s": 5, "network_connections": 2, "bytes_sent": 10, "bytes_received": 20}

c = httpx.Client(base_url=BASE, timeout=20)

print("1. the dashboard is served with a strict security policy")
r = c.get("/")
assert r.status_code == 200 and "AegisFlow" in r.text
h = {k.lower(): v for k, v in r.headers.items()}
csp = h["content-security-policy"]
assert "default-src 'none'" in csp and "script-src 'self'" in csp
assert "unsafe-inline" not in csp.split("script-src")[1].split(";")[0]  # no inline SCRIPT
assert h["x-content-type-options"] == "nosniff" and h["x-frame-options"] == "DENY" and h["referrer-policy"] == "no-referrer"
assert not any(ch.isdigit() for ch in h.get("server", "")), f"server header leaks a version: {h.get('server')}"
ok("CSP, nosniff, frame-deny, no-referrer present; script-src has no unsafe-inline; nginx version hidden")
assert c.get("/some/client/route").status_code == 200
ok("unknown paths fall back to the single-page app")

print("2. the API is reachable only through the proxy, and still enforces auth")
assert c.get("/api/health").json() == {"status": "ok"}
assert c.get("/api/devices").status_code == 401
assert c.get("/api/me", headers=VH).json()["role"] == "viewer"
assert c.get("/api/me", headers=AH).json()["role"] == "admin"
ok("health open, devices 401 without a token, roles resolve through nginx")
rows = [json.loads(line) for line in dc("ps", "--format", "json").stdout.splitlines() if line.strip()]
pub = {p["Service"]: [x for x in (p.get("Publishers") or []) if x.get("PublishedPort")] for p in rows}
assert not pub["api"], f"the API must not be published to the host: {pub['api']}"
assert all(x["URL"] in ("127.0.0.1", "::1") for x in pub["web"]), pub["web"]
ok("API publishes no host port; the dashboard is bound to loopback only")

print("3. request bodies are bounded at the edge")
big = c.post("/api/telemetry", content=b"x" * 100_000, headers={"Content-Type": "application/json"})
assert big.status_code == 413, big.status_code
ok("a 100 KB body is refused with 413 before it reaches the API")

print("4. the client address cannot be spoofed to dodge the failed-key limiter")
codes = [
    c.post("/api/telemetry", headers={"X-Device-Key": f"ak_guess{i}", "X-Forwarded-For": f"203.0.113.{i}"}, json={"device_id": "D-777", **TELEMETRY}).status_code
    for i in range(30)
]
assert codes[0] == 401 and codes[-1] == 429, codes
ok(f"30 guesses, each claiming a different X-Forwarded-For: first {codes[0]}, last {codes[-1]} (nginx overwrote the header)")

print("5. a registered device can send telemetry through the proxy")
reg = c.post("/api/devices", headers=AH, json={"device_id": "D-901", "name": "Smoke test", "type": "badge"})
assert reg.status_code in (201, 409), reg.text
if reg.status_code == 201:
    key = reg.json()["api_key"]
    time.sleep(31)  # the failed-attempt bucket above refills at 2/s; wait it out before a legitimate request
    t = c.post("/api/telemetry", headers={"X-Device-Key": key}, json={"device_id": "D-901", **TELEMETRY})
    assert t.status_code == 202, t.text
    ok("registered, authenticated and accepted (202)")
else:
    ok("device D-901 already registered (earlier run); skipping the send")


print("5b. explicit signal availability crosses the proxy: a null group is accepted, a half-missing group and an omitted field are not")
reg2 = c.post("/api/devices", headers=AH, json={"device_id": "D-902", "name": "Smoke test (availability)", "type": "badge"})
assert reg2.status_code in (201, 409), reg2.text
if reg2.status_code == 201:
    key2 = reg2.json()["api_key"]
    time.sleep(3)
    hk = {"X-Device-Key": key2}
    lost = {**TELEMETRY, "device_id": "D-902", "network_connections": None, "bytes_sent": None, "bytes_received": None, "remote_endpoints": None}
    assert c.post("/api/telemetry", headers=hk, json=lost).status_code == 202
    half = {**TELEMETRY, "device_id": "D-902", "latitude": None}  # longitude still present
    assert c.post("/api/telemetry", headers=hk, json=half).status_code == 422
    omitted = {k: v for k, v in {**TELEMETRY, "device_id": "D-902"}.items() if k != "battery"}
    assert c.post("/api/telemetry", headers=hk, json=omitted).status_code == 422
    ok("network group null -> 202; latitude null with longitude present -> 422; battery omitted -> 422")
else:
    ok("device D-902 already registered (earlier run); skipping the availability send")


async def ws_check():
    async with websockets.connect(f"ws://localhost:{PORT}/api/ws?token={VIEWER}") as ws:
        return json.loads(await asyncio.wait_for(ws.recv(), 10))["type"]


async def ws_bad():
    try:
        async with websockets.connect(f"ws://localhost:{PORT}/api/ws?token=not-valid-token-1234") as ws:
            await asyncio.wait_for(ws.recv(), 5)
            return "accepted"
    except Exception as e:  # noqa: BLE001 - any refusal is the expected outcome
        return type(e).__name__


print("6. the WebSocket works through nginx, and its token never reaches a log")
assert asyncio.run(ws_check()) == "snapshot"
assert asyncio.run(ws_bad()) != "accepted"
ok("viewer receives a snapshot; a bad token is refused")
logs = dc("logs", "web").stdout + dc("logs", "api").stdout
assert VIEWER not in logs and ADMIN not in logs and "/api/ws" not in logs, "a token or the WebSocket URL appeared in a log"
ok("neither token appears in the web or api logs, and /api/ws is not access-logged")

print("7. containers run as non-root")
for svc, want in (("api", "10001"), ("web", None)):
    uid = dc("exec", "-T", svc, "id", "-u").stdout.strip()
    assert uid != "0" and (want is None or uid == want), (svc, uid)
    ok(f"{svc} runs as uid {uid}")

print("8. data survives an API restart")
before = c.get("/api/devices", headers=VH).json()
assert any(d["device_id"] == "D-901" for d in before)
dc("restart", "api")
for _ in range(60):
    try:
        if c.get("/api/health").status_code == 200:
            break
    except httpx.HTTPError:
        pass
    time.sleep(1)
after = c.get("/api/devices", headers=VH).json()
assert [d["device_id"] for d in after] == [d["device_id"] for d in before]
ok("the device registry is intact after restarting the API container")
print("STACK OK")
