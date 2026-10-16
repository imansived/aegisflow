# AegisFlow

Real-time cross-modal behavioral anomaly detection for connected devices: it learns what
normal looks like per device, scores deviations in spatial, network, device and proximity
signals, and tests whether *correlating* those weak deviations beats judging each alone.

Status: **Phase 3** (contracts, labelled simulator, Redis Streams pipeline, per-modality detectors). See
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
