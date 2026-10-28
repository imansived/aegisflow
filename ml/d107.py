"""D107: independent validation of the shipped availability handling (protocol: docs/preregistration/D107-availability-implementation.md).

  python -m ml.d107 build [--workers 6]        # end-to-end runs through the real product pipeline, seeds 400-409
  python -m ml.d107 evaluate                   # THE evaluation; refuses to run twice (guard file)

`build` makes DATA only (telemetry with explicit nulls, run through DetectionEngine -> CorrelationEngine(table) ->
AlertManager exactly as the server does) and records per-tick detector outputs and product outputs. Nothing is scored there.
"""

import json
import sys
from concurrent.futures import ProcessPoolExecutor
from dataclasses import replace
from datetime import timedelta
from pathlib import Path

import numpy as np

from backend.correlation.engine import FROZEN_CORRELATION
from backend.correlation.fusion import MODALITIES, LearnedFusion
from backend.correlation.thresholds import FULL, ThresholdTable, pattern_of
from backend.services.alerts import AlertConfig
from backend.services.pipeline import Pipeline
from contracts.scenarios import SCENARIOS, Episode, ScenarioType
from contracts.telemetry import SIGNAL_FIELDS
from simulator.config import SimConfig
from simulator.fleet import Fleet
from simulator.plan import build_plan

from .dataset import POST_EPISODE_BUFFER_S, SCENARIO_CODES
from .missing import MODS, TOUCH, TOUCH_VARIANT, bursts
from .validate import Combined

SEEDS = list(range(400, 410))
E2E_SUSTAINED_SEEDS = [400, 401]
HOURS, WARM, DEVICES = 72.0, 33.0, 20
OUT = Path("data/d107")
GUARD = Path("data/results/D107_TEST_SEEDS_USED")
TABLE = Path("models/availability_thresholds.json")
DESIGN = Path("models/frozen_design.json")
PREREG_COMMIT = "8eacdbb"
SUSTAINED = [f"single:{m}" for m in MODS] + [f"pair:{MODS[i]}+{MODS[j]}" for i in range(4) for j in range(i + 1, 4)]
CONDS = ["complete"] + SUSTAINED + ["intermittent", "correlated"]
GROUP_OF = {"spatial": "spatial", "network": "network", "device": "device", "proximity": "proximity"}


# ------------------------------------------------------------------------------- telemetry-level masks ----


class Masker:
    """Which signal groups a device reports as unavailable at each post-warm-up tick, for one condition and seed."""

    def __init__(self, cond: str, seed: int, plan: list[Episode], n_post: int, t_warm: float, tick: int):
        self.cond, self.t_warm, self.tick, self.n_post = cond, t_warm, tick, n_post
        self.sustained = frozenset(cond.split(":")[1].split("+")) if cond.startswith(("single:", "pair:")) else frozenset()
        self.arr: dict[tuple[str, str], np.ndarray] = {}
        if cond not in ("intermittent", "correlated"):
            return
        cid = {"intermittent": 1, "correlated": 2}[cond]
        stat = 0.20 if cond == "intermittent" else 0.02
        for d in range(1, DEVICES + 1):
            for m in range(4):
                rng = np.random.default_rng(np.random.SeedSequence([cid, seed, d, m]))
                self.arr[(f"D-{d:03d}", MODS[m])] = bursts(n_post, stat, 12.0, rng)
        if cond == "correlated":
            rng = np.random.default_rng(np.random.SeedSequence([2, 999, seed]))
            for e in plan:
                touched = TOUCH_VARIANT.get(e.variant) if not e.is_malicious else TOUCH.get(e.scenario.value)
                if not touched:
                    continue
                a = int((e.start.timestamp() - t_warm) // tick)
                b = int((e.end.timestamp() - t_warm) // tick) + 1
                for m in touched:
                    if rng.random() < 0.6:  # the whole signal is unavailable for the whole episode
                        self.arr[(e.device_id, MODS[m])][max(0, a):max(0, b)] = True

    def lost(self, device: str, ts: float) -> frozenset[str]:
        if ts < self.t_warm:
            return frozenset()  # baselines are learned on complete data, as in every earlier experiment
        if self.sustained:
            return self.sustained
        if not self.arr:
            return frozenset()
        k = int((ts - self.t_warm) // self.tick)
        return frozenset(m for m in MODS if k < self.n_post and self.arr[(device, m)][k])


def _null(t, lost: frozenset[str]):
    if not lost:
        return t
    return t.model_copy(update={f: None for g in lost for f in SIGNAL_FIELDS[g]})


# ------------------------------------------------------------------------------------------- build -----


def build_run(seed: int, cond: str, root: Path | None = None) -> dict:
    cfg = SimConfig(seed=seed, n_devices=DEVICES)
    dur, warm = timedelta(hours=HOURS), timedelta(hours=WARM)
    plan = build_plan(cfg, dur, warm, 4.0)
    design = json.loads(DESIGN.read_text())
    lf = LearnedFusion.from_dict(design["fusion"]["learned"])
    thr = design["thresholds"]["learned"]
    alert_cfg = AlertConfig(raise_at=thr, clear_at=max(0.0, thr - 0.08), clear_after_s=60.0)
    corr = replace(FROZEN_CORRELATION, low=0.5 * alert_cfg.clear_at, medium=alert_cfg.clear_at, high=alert_cfg.raise_at)
    table = ThresholdTable.load(TABLE)
    t_warm = (cfg.start + warm).timestamp()
    n_post = int((dur - warm).total_seconds() // cfg.tick_s)
    masker = Masker(cond, seed, plan, n_post, t_warm, cfg.tick_s)

    events: list[dict] = []
    state = {"scores": None, "fused": None, "opened_on_fallback": 0}
    pipe = Pipeline(cfg.device_ids(), lf, store=None, corr_cfg=corr, alert_cfg=alert_cfg, table=table,
                    on_alert=lambda ev: (events.append(ev), state.update(opened_on_fallback=state["opened_on_fallback"] + (
                        ev["event"] in ("opened", "escalated") and not state["fused"].alertable))))
    pipe.detection.sink = lambda s: state.update(scores=s)
    pipe.on_fused = lambda f: state.update(fused=f)

    idx = {d: i for i, d in enumerate(cfg.device_ids())}
    by_dev: dict[str, list[tuple[int, Episode]]] = {d: [] for d in cfg.device_ids()}
    for k, e in enumerate(plan):
        by_dev[e.device_id].append((k, e))
    buf = timedelta(seconds=POST_EPISODE_BUFFER_S)
    cap = DEVICES * n_post
    A = {k: np.empty(cap, dt) for k, dt in (("ts", np.float64), ("dev", np.int16), ("avail", np.int8), ("label", np.int8), ("ignore", bool),
                                            ("episode", np.int32), ("scenario", np.int8), ("learning", bool), ("p_score", np.float64),
                                            ("p_alertable", bool), ("p_hot", bool), ("p_obs", np.int8), ("p_thr", np.float64), ("p_pattern", np.int8))}
    soft = np.empty((cap, 4), np.float64)  # float64: the conformance check compares the product's scores to a recomputation
    hard = np.empty((cap, 4), np.float64)
    n = 0
    for t in Fleet(cfg, plan).run(dur):
        ts = t.timestamp.timestamp()
        pipe.process(_null(t, masker.lost(t.device_id, ts)))
        if ts < t_warm:
            continue
        s, f = state["scores"], state["fused"]
        label, ignore, epi, sc = 0, False, -1, -1
        for k, e in by_dev[t.device_id]:
            if e.start <= t.timestamp < e.end:
                label, epi, sc = int(e.is_malicious), k, SCENARIO_CODES.index(e.scenario)
                break
            if e.end <= t.timestamp < e.end + buf:
                ignore, epi = True, k
        A["ts"][n], A["dev"][n], A["avail"][n] = ts, idx[t.device_id], f.pattern
        A["label"][n], A["ignore"][n], A["episode"][n], A["scenario"][n], A["learning"][n] = label, ignore, epi, sc, s.learning
        for j, m in enumerate(MODALITIES):
            soft[n, j], hard[n, j] = s.results[m].soft, s.results[m].score
        A["p_score"][n], A["p_alertable"][n], A["p_pattern"][n] = f.score, f.alertable, f.pattern
        A["p_obs"][n] = {"full": 0, "degraded": 1, "blind": 2}[f.observability]
        A["p_thr"][n] = f.operating.raise_at if f.operating else np.nan
        A["p_hot"][n] = bool(f.alertable and f.operating is not None and f.score >= f.operating.raise_at)
        n += 1
    out = (root or OUT) / cond.replace(":", "_").replace("+", "-") / f"seed{seed}.npz"
    out.parent.mkdir(parents=True, exist_ok=True)
    np.savez_compressed(out, soft=soft[:n], hard=hard[:n], **{k: v[:n] for k, v in A.items()},
                        episodes=np.array([e.model_dump_json() for e in plan]), alerts=np.array([json.dumps(e) for e in events] or [""]),
                        opened_on_fallback=state["opened_on_fallback"], t_warm=t_warm)
    return {"seed": seed, "condition": cond, "rows": n, "alerts": len(events), "opened_on_fallback": state["opened_on_fallback"]}


def _job(a):
    return build_run(*a)


def cmd_build_dry(workers: int) -> None:
    """Pipeline check on VALIDATION seed 18 only (all 13 conditions end to end): lets the evaluator be debugged without
    ever scoring the D107 test seeds."""
    root = Path("data/d107_dry")
    jobs = [(18, c, root) for c in CONDS if not (root / c.replace(":", "_").replace("+", "-") / "seed18.npz").exists()]
    with ProcessPoolExecutor(max_workers=workers) as pool:
        for r in pool.map(_job, jobs):
            print(json.dumps(r), flush=True)


def cmd_build(workers: int) -> None:
    jobs = [(s, c) for s in SEEDS for c in ("complete", "intermittent", "correlated")]
    jobs += [(s, c) for s in E2E_SUSTAINED_SEEDS for c in SUSTAINED]
    jobs = [j for j in jobs if not (OUT / j[1].replace(":", "_").replace("+", "-") / f"seed{j[0]}.npz").exists()]
    print(f"{len(jobs)} end-to-end runs through the product pipeline", flush=True)
    with ProcessPoolExecutor(max_workers=workers) as pool:
        for r in pool.map(_job, jobs):
            print(json.dumps(r), flush=True)


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "build":
        cmd_build(int(sys.argv[sys.argv.index("--workers") + 1]) if "--workers" in sys.argv else 6)
    elif len(sys.argv) > 1 and sys.argv[1] == "build-dry":
        cmd_build_dry(5)
    elif len(sys.argv) > 1 and sys.argv[1] == "evaluate":
        from .d107_eval import main
        main()
    else:
        raise SystemExit(__doc__)
