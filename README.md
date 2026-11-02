# AegisFlow

![The Observatory during a restricted-zone alert](docs/img/observatory-alert.png)

**Real-time cross-modal behavioural anomaly detection for connected devices.**

AegisFlow learns what "normal" looks like for each device, scores deviations in four signals (location, network,
device health, proximity), and asks one question no single signal can answer: *are several small deviations happening
together on the same device?* When they are, it raises an alert and explains exactly which signals contributed and why.

It runs on **simulated** devices. It is an early-warning system, not proof of an attack, and its scores are rankings,
not probabilities. **Engineering and detection are validated differently:** the pipeline, availability states and alert wording are tested end to end; whether the detectors find real attacks has **not** been measured (see below).

## What was found

**Start with [docs/RESEARCH.md](docs/RESEARCH.md)**: every headline claim with its experiment, evidence file, seeds and commit, plus the failure cases and
what the evidence does not cover. The artifacts behind it are frozen with hashes in [docs/research/v1/manifest.json](docs/research/v1/manifest.json) (git tag `research-v1`).

The central claim was tested with a pre-registered final evaluation on unseen simulated data (1,331 episodes, every
system held to the same 0.1% false-alarm budget; "0.1%" turned out to mean 2 to 7 false alerts per device per day, see below):

| Question | Result |
|---|---|
| Does fusing signals beat independent detection ("any detector fires")? | **Yes, clearly:** +0.43 recall (95% interval +0.39 to +0.48) |
| Does fusion help with no training labels at all? | Yes, modestly: +0.13 (+0.10 to +0.17) |
| Does it beat the single best detector (network)? | **Not shown:** +0.02 (−0.01 to +0.04) |
| Does it beat a supervised joint model? | No: −0.09; the supervised model is better |
| Does a correlation time window help? | Not shown, even for staggered attacks |

An honest, partial result, reported with its corrections and limits. It says nothing about real fleets.

**Audit after the fact (exploratory; see docs/RESEARCH.md).** The results were then attacked. Counting alerts instead of rows, every
system raises 2 to 7 false alarms per device per day at that budget; fusion still beats "any detector fires" under every
stress test (unseen combinations, benign noise, noisy sensors, benign drift, lost telemetry, a missing signal), but its edge over
the single best detector appears only when the anomaly lies outside that detector's view, and it reverses under sensor noise. The
frozen thresholds do not survive noise or drift. Two public real datasets were used: TON_IoT host telemetry (device health plus
network, mixed and session-confounded) and IoT-23 real IoT traffic (network only; the frozen network threshold gives 12 to 76
false alerts per device-day on real traffic). Location and proximity have no real-data validation.

**Missing signals (D106, pre-registered, docs/preregistration/D106-missing-signals.md).** When one signal disappears the current system reads it as "normal": with
the network signal gone its recall on weak and staged attacks falls from 0.87 to 0.18 while its alert rate drops, which looks
quiet and is blind. Treating availability explicitly (a separate threshold per set of available signals, and optionally a separate
learned fusion per set) cut the average loss from 0.34 to 0.22 across 12 missing-signal conditions at the same alert budget
(+0.12, 10 of 10 fresh seeds). A post-hoc ablation shows nearly all of that comes from the per-set thresholds, the cheaper
change. Now implemented and independently validated (D107, docs/preregistration/D107-availability-implementation.md): telemetry marks a signal group
unavailable with an explicit null (never a zero); detectors score and learn nothing from it; the unchanged learned fusion is judged by a
threshold validated for exactly the signals that remain, with a conservative fallback (no alert, reported as Degraded) for any combination
without one; and the dashboard shows **Normal / Anomalous / Degraded**, never "Normal" for a device it cannot fully see. On 10 fresh seeds run
through the real pipeline, the shipped version loses 0.10 less recall than the old behaviour across 12 missing-signal conditions
(interval +0.086 to +0.118, 10 of 10 seeds), within 0.013 of the heavier per-pattern learned model, at the same alert budget. It still loses
0.21 on average, and when both location and network are gone it is nearly blind.

**Real traces (R1, docs/preregistration/R1-iot23-replay.md).** Replaying real IoT-23 device traffic (network signal only) through the unchanged system:
the availability and recovery states behave exactly as designed, but the network detector raises about **32 false alert onsets per device-day** on real
benign traffic (the simulator: about 5), because real devices are silent on 90% of ticks and the detector expected always-chatty ones. The original
benchmark (`research-v1`) is left untouched; fixes would be a new version with a new protocol. See docs/RESEARCH.md.

**Later work (R2 to R7, each with a pre-registered protocol).** Two offline candidate network detectors (zero-inflated, windowed-rate) held the false-alarm budget on
159.6 held-out benign device-days of a public IoT testbed (about 2.1 to 2.7 per device-day, interval upper bounds under 3.75), where the shipped detector raised 45 per
device-day; they are not part of the shipped system. Devices can now declare which signals they support, so a network-only device is no longer Degraded for life, and
alert text states which signals were not observed and that this neither confirms nor rules out an attack (R3/R4; scores and thresholds unchanged, and tested to be).
**Detection of real attacks remains unmeasured:** four attempts to assemble an eligible evaluation (dated attack onsets, a clean verified-continuous baseline, enough
independent sessions) stopped at pre-registered gates, the last one because the public daily recordings have real gaps. The status table and the closure record are in docs/RESEARCH.md.

**Reading the score.** The dashboard shows a severity band (Normal, Low, Medium, High) and an alert as two different things. The band follows the fused score; an alert opens at the alert level once a device has finished learning and closes only after 60 s below the clear level, so a device can read Normal while its alert is still open. The ring says `LOW · COMBINED` (not NOMINAL) when weak signals add up to a non-Normal band, and a collapsible "How to read this score" panel explains the rules.

**Reading the top bar and panels.** The four cards in the top bar (connected devices, open alerts, peak risk, and the clock) show the number now and, for the first three, a small trend over the last 30 simulated minutes, drawn only from data the dashboard already holds. The clock is labelled **Simulated time** with its speed (for example x20) because the demo fleet runs faster than real time. The ring in Device health counts each device once as **Normal, Degraded or Flagged**; it never says "healthy", because a quiet score on a device with missing signals says nothing about the signals it cannot see.

## Run it (Docker)

```powershell
cp .env.example .env          # then put two different long random tokens in it (there are no defaults)
docker compose up -d --build
```

Open **http://localhost:8080** and sign in with the **admin** token to run the demo, or the **viewer** token to watch.
In **Scenarios** start the fleet (about a minute to learn each device's normal behaviour), pick a device, and inject a threat or a signal
outage. The four-minute story (normal, anomalous, outage, blind spot, recovery, late alert) is in [docs/DEMO.md](docs/DEMO.md); `python scripts/demo_story.py`
runs it in a browser and regenerates its screenshots. Without a route to Docker's registry, `docker compose -f docker-compose.yml -f docker-compose.overlay.yml up -d --build`
layers the current code onto earlier images.

The stack is three containers (Redis, API, nginx + dashboard). Only the dashboard is exposed, on `127.0.0.1`. Put TLS in
front before serving anyone else.

## Documentation

| | |
|---|---|
| [docs/RESEARCH.md](docs/RESEARCH.md) | The results: main comparison, failure cases, limitations, why thresholds only, and a trace from each claim to its experiment |
| [docs/DEMO.md](docs/DEMO.md) | The honest four-minute demonstration, with screenshots |
| `docs/preregistration/` | The D106 and D107 protocols, committed before their test seeds existed |
| `docs/research/v1/manifest.json` | Hashes and commits of every frozen artifact; never edited, a change is a new version |
| `docs/results/` | The raw results of every experiment |

## Layout

| Path | Purpose |
|---|---|
| `contracts/` | Shared data shapes: telemetry, zones and geometry, scenarios, ground-truth episodes |
| `simulator/` | Device simulator with labelled anomalies |
| `backend/` | Streaming pipeline, detectors, correlation, alerts, API |
| `ml/` | Dataset builder, comparison controls, the pre-registered experiments |
| `frontend/` | React + TypeScript dashboard |
| `docker/`, `docker-compose.yml` | Packaging |
| `scripts/` | End-to-end browser check, stack smoke test, narrated demo |
| `models/frozen_design.json` | The learned fusion and alert thresholds the server loads (fitted on validation seeds only) |
| `tests/` | 233 Python tests (some need Redis) |

## Develop

```powershell
python -m venv .venv
.\.venv\Scripts\python -m pip install -e ".[dev]"
docker compose -f docker-compose.dev.yml up -d        # Redis only, no tokens needed
.\.venv\Scripts\python -m pytest                      # ~6 min; add -m "not slow" to skip the live-demo tests
```

API without Docker (port 8010, because 8000 is often taken):

```powershell
$env:AEGIS_ADMIN_TOKEN  = "choose-a-long-admin-token"     # at least 16 characters, different from the viewer token
$env:AEGIS_VIEWER_TOKEN = "choose-a-long-viewer-token"
.\.venv\Scripts\python -m uvicorn --factory backend.api.app:create_app --port 8010
cd frontend; npm install; npm run dev                     # http://localhost:5173, proxies /api to the API
npm test                                                  # 71 unit and component tests
```

Checks that drive the real system (with the stack running; they need `pip install playwright`, which uses the installed Edge):

```powershell
python scripts/e2e_dashboard.py     # whole UI flow in a real browser; fails on any failed request or console error
python scripts/smoke_stack.py       # Docker packaging guarantees: headers, body limit, non-root, no tokens in logs...
python scripts/demo_flow.py         # the narrated seven-step demo through the API
```

## Reproduce the research

```powershell
.\.venv\Scripts\python -m ml.dataset --seeds 10 19 --workers 4     # validation data, ~10 min
.\.venv\Scripts\python -m ml.validate --out data/results/e2.json   # experiments E1/E2, ~15 min
.\.venv\Scripts\python -m ml.export_design                         # the artefact the server loads
```

The final test run (`python -m ml.final_eval`) refuses to run twice on the same seeds: they were spent by design.

## Limits

Simulated fleet; the scenarios and effect sizes are my own model. Real-data checks (R1 to R7) cover the network signal only, on benign traffic and public captures; location, device-health and proximity detection have no real-data validation, and detection of real attacks is unmeasured. No TLS, key rotation, per-user accounts or audit log. Single node. All verified on Windows with Docker Desktop.
The full list is in docs/RESEARCH.md.
