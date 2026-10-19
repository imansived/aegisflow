"""The spec's seven-step demonstration (§37), narrated, driven through the API.

  python scripts/demo_flow.py [--device D-004] [--base http://localhost:8080/api]

Needs the stack running and the admin token in AEGIS_ADMIN_TOKEN (or .env). It starts the simulated fleet if it is
not already live. Everything it prints comes from the running system.
"""
import argparse
import os
import sys
import time
from pathlib import Path

import httpx

ap = argparse.ArgumentParser()
ap.add_argument("--device", default="D-004")
ap.add_argument("--base", default="http://localhost:8080/api")
ap.add_argument("--speed", type=float, default=20.0, help="simulated seconds per real second")
a = ap.parse_args()


def token():
    if os.environ.get("AEGIS_ADMIN_TOKEN"):
        return os.environ["AEGIS_ADMIN_TOKEN"]
    for line in Path(".env").read_text().splitlines():
        if line.startswith("AEGIS_ADMIN_TOKEN="):
            return line.split("=", 1)[1].strip()
    sys.exit("AEGIS_ADMIN_TOKEN not set")


c = httpx.Client(base_url=a.base, headers={"Authorization": f"Bearer {token()}"}, timeout=30)
D = a.device
SIM = lambda real_s: f"{real_s * a.speed / 60:.0f} simulated minutes"


def live(device=D):
    return next((x for x in c.get("/anomalies/live").json() if x["device_id"] == device), None)


def step(n, title):
    print(f"\n=== Step {n}: {title}")


def wait(cond, timeout, what):
    end = time.time() + timeout
    while time.time() < end:
        v = cond()
        if v:
            return v
        time.sleep(0.5)
    sys.exit(f"timed out waiting for {what}")


def show(snap):
    eff = ", ".join(f"{k} {v:.2f}" for k, v in snap["effective"].items() if v >= 0.1) or "all signals quiet"
    print(f"  {snap['device_id']}  zone={snap['zone']}  score={snap['score']:.2f} ({snap['severity']})  signals: {eff}")


step(1, "start the system: a simulated fleet with normal behaviour")
st = c.get("/demo/status").json()
if st["state"] != "live":
    c.post("/demo/start", json={"devices": 10, "warmup_hours": 34, "speed": a.speed})
    t0 = time.time()
    wait(lambda: c.get("/demo/status").json()["state"] == "live", 240, "the fleet to go live")
    print(f"  fleet live after {time.time() - t0:.0f} s (34 simulated hours of warm-up to learn each device's normal behaviour)")
fleet = c.get("/anomalies/live").json()
print(f"  {len(fleet)} devices reporting; open alerts: {len(c.get('/alerts', params={'status': 'open'}).json())}")

step(2, "baseline activity: devices move between zones with ordinary network, battery and proximity")
time.sleep(3)
base = live()
show(base)
assert base["severity"] in ("normal", "low"), "the demo device should start quiet"

step(3, f"trigger a spatial event: {D} walks into the restricted server room")
c.post("/demo/inject", json={"device_id": D, "scenario": "spatial", "magnitude": 1.0, "duration_s": 600})
first = wait(lambda: next(iter(c.get("/alerts", params={"device_id": D, "status": "open"}).json()), None), 90, "an alert")
print(f"  ALERT opened: {first['severity'].upper()}  peak {first['peak_score']:.2f}  fusion={first['fusion']}")
show(live())

step(4, "add other deviations: unknown connections, battery drain and CPU, an unknown device nearby")
for scenario in ("network", "hardware", "proximity"):
    time.sleep(2)  # staggered, like the spec's example: a few seconds apart
    c.post("/demo/inject", json={"device_id": D, "scenario": scenario, "magnitude": 0.8, "duration_s": 600})
    print(f"  injected {scenario}")

step(5, "show correlation: the fused score and which signals contribute")
snap = wait(lambda: (lambda s: s if s and sum(v >= 0.3 for v in s["effective"].values()) >= 3 else None)(live()), 120, "three signals to register")
show(snap)
print("  each signal's share of the score: " + ", ".join(f"{k} {v:.2f}" for k, v in snap["contributions"].items() if v > 0.005))

step(6, "explain the alert: which signals contributed, and the evidence")
time.sleep(4)
alert = c.get("/alerts", params={"device_id": D}).json()[0]
print("  " + alert["explanation"].replace("\n", "\n  "))
# The whole point of the system: the alert must name every signal that is active, not just the first one.
assert len(alert["factors"]) >= 3, f"the explanation lists only {[f['modality'] for f in alert['factors']]}"
assert sum(f["contribution"] for f in alert["factors"]) > 0.5, "contributions should add up to most of the score"

step(7, "restore normal behaviour and watch the score respond")
c.post("/demo/clear", json={"device_id": D})
print("  cleared; the score as behaviour returns to baseline (one line per ~2 s of real time):")
trace, t0 = [], time.time()
while time.time() - t0 < 150:
    s = live()
    active = ", ".join(f"{k} {v:.2f}" for k, v in s["effective"].items() if v >= 0.3) or "none"
    print(f"    +{time.time() - t0:4.0f}s  score {s['score']:.2f} ({s['severity']:<6})  zone {str(s['zone']):<8}  signals still elevated: {active}")
    trace.append(s)
    if s["severity"] == "normal" and not c.get("/alerts", params={"device_id": D, "status": "open"}).json():
        break
    time.sleep(2)
end = trace[-1]
assert end["severity"] == "normal", f"{D} did not return to normal: {end['severity']} (signals {end['effective']})"
closed = c.get("/alerts", params={"device_id": D, "status": "closed"}).json()[0]
print(f"  alert closed (peak {closed['peak_score']:.2f}); {D} is back to {end['severity']} in {end['zone']} after {time.time() - t0:.0f} s")
print(f"  (the slowest signal is battery drain, judged over a 5-minute window; at this speed that took {SIM(time.time() - t0)})")
c.post("/demo/stop")
print("\nDEMO FLOW OK")
