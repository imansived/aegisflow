# AegisFlow

Real-time cross-modal behavioral anomaly detection for connected devices: it learns what
normal looks like per device, scores deviations in spatial, network, device and proximity
signals, and tests whether *correlating* those weak deviations beats judging each alone.

Status: **research evaluation and backend API complete** (simulator, pipeline, detectors, correlation + alerts, validation and final test runs, FastAPI + WebSocket + live demo); dashboard and Docker packaging still to build. See
[docs/AegisFlow-Technical-Manual.md](docs/AegisFlow-Technical-Manual.md) for the reasoning
behind every decision.

## Layout

| Path | Purpose |
|---|---|
| `contracts/` | Shared data shapes: telemetry, zones/geometry, scenarios, ground-truth episodes |
| `simulator/` | Device simulator (Phase 1) |
| `backend/` | Stream processing, detectors, correlation, API (Phases 2-4, 6) |
| `ml/` | Training and evaluation harness (Phase 5) |
| `frontend/` | React + TypeScript dashboard (Phase 7) |
| `tests/` | pytest suite |

## Setup (Windows PowerShell)

```powershell
python -m venv .venv
.\.venv\Scripts\python -m pip install -e ".[dev]"
.\.venv\Scripts\python -m pytest
```

Redis (needed from Phase 2) runs through Docker Desktop: `docker compose up -d redis`.

## Limits

Runs on simulated telemetry only. It is an early-warning/anomaly system, not proof of attack,
and scores are rankings, not calibrated probabilities. Results are reported only after being
measured (manual §5).

## Generate a dataset

```powershell
.\.venv\Scripts\python -m simulator.cli --devices 50 --hours 48 --seed 1 --out data/run1
``` 

Writes `telemetry.jsonl` (what detectors see), `episodes.jsonl` (ground truth, kept separate) and `config.json`. About 100 s and 600 MB for 50 devices x 48 h; `data/` is git-ignored.

## Run the pipeline

```powershell
docker compose up -d redis
.\.venv\Scripts\python -m backend.streaming.cli process --db data/events.db   # terminal 1
.\.venv\Scripts\python -m backend.streaming.cli publish --data data/run1 --speed 60  # terminal 2
.\.venv\Scripts\python -m backend.streaming.cli bench   # throughput and latency
```

## Development sanity check (seeds 1-9 only)

```powershell
.\.venv\Scripts\python -m ml.sanity --seed 1 --devices 20   # ~3 min
```

## Build rows and compare systems on dev seeds

```powershell
.\.venv\Scripts\python -m ml.dataset --seed 1 --out data/rows/seed1.npz   # ~5 min each
.\.venv\Scripts\python -m ml.dataset --seed 2 --out data/rows/seed2.npz
.\.venv\Scripts\python -m ml.devcheck --train data/rows/seed1.npz --test data/rows/seed2.npz
```

## Validation experiment E1 (seeds 10-19 only)

```powershell
.\.venv\Scripts\python -m ml.dataset --seeds 10 19 --workers 8   # ~10 min
.\.venv\Scripts\python -m ml.validate --out data/results/e2.json  # ~15 min
```

## Run the API and the live demo

```powershell
docker compose up -d redis
$env:AEGIS_ADMIN_TOKEN  = "choose-a-long-admin-token"     # >= 16 chars, different from the viewer token
$env:AEGIS_VIEWER_TOKEN = "choose-a-long-viewer-token"
.\.venv\Scripts\python -m uvicorn --factory backend.api.app:create_app --port 8010
```

Interactive docs at http://127.0.0.1:8010/docs. Start the simulated fleet and inject an intrusion
(`Authorization: Bearer <admin token>`):

```
POST /demo/start    {"devices": 10, "warmup_hours": 34, "speed": 20}     # ~1 min warm-up, then live
POST /demo/inject   {"device_id": "D-003", "scenario": "spatial", "magnitude": 1.0, "duration_s": 240}
POST /demo/clear    {"device_id": "D-003"}
```

Watch live with a WebSocket to `/ws?token=<viewer token>`. Without both tokens the server refuses to start
(set `AEGIS_AUTH=off` only for a local demo). `models/frozen_design.json` is the learned fusion and thresholds
exported from validation seeds (`python -m ml.export_design`); without it the server uses noisy-OR.
