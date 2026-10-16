# AegisFlow

Real-time cross-modal behavioral anomaly detection for connected devices: it learns what
normal looks like per device, scores deviations in spatial, network, device and proximity
signals, and tests whether *correlating* those weak deviations beats judging each alone.

Status: **Phase 0** (contracts and scenario definitions). See
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
