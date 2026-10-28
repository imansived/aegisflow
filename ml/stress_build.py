"""Stress conditions for the simulator audit (manual §5.8). Builds labelled detector-output rows like `ml.dataset`,
but with a perturbed world: unseen anomaly combinations, heavy benign noise, noisy sensors, benign drift, and lost
telemetry. The frozen generator and detectors (commit 9b3c7ef) are NOT modified; this module reuses them and
changes only what is fed in.

  python -m ml.stress_build --condition unseen --seeds 210 214
  python -m ml.stress_build --condition all
"""

import argparse
import json
from collections.abc import Callable
from dataclasses import replace
from datetime import timedelta
from pathlib import Path

import numpy as np

from backend.correlation.fusion import MODALITIES
from backend.detection.engine import DetectionEngine
from contracts.scenarios import SCENARIOS, Episode, Modality, ScenarioType
from simulator.config import TAG_PLAN, SimConfig, child_rng
from simulator.fleet import Fleet
from simulator.plan import DEFAULT_WEIGHTS, MIN_GAP_S, build_plan

from .dataset import FEATURE_NAMES, POST_EPISODE_BUFFER_S, SCENARIO_CODES, Z_NAMES, RULE_NAMES, RowSet

M = Modality
HOURS, WARM, DEVICES = 72.0, 33.0, 20

# ------------------------------------------------------------------------------------ conditions ----


def _custom_plan(cfg: SimConfig, kinds: list[dict], per_device_per_day: float, benign_share: float = 0.2) -> list[Episode]:
    """Episodes drawn from `kinds` (each: modalities, magnitude range, optional staged windows), plus the usual benign
    look-alikes, placed without overlap and never during warm-up."""
    rng = child_rng(cfg.seed, TAG_PLAN, 77)
    dur, warm = HOURS * 3600.0, WARM * 3600.0
    days = (dur - warm) / 86_400
    drafts: list[tuple] = []
    for dev in cfg.device_ids():
        taken: list[tuple[float, float]] = []
        for _ in range(int(rng.poisson(per_device_per_day * days))):
            benign = rng.random() < benign_share
            if benign:
                if rng.random() < 0.5:
                    kind = {"name": "firmware_update", "mods": frozenset({M.NETWORK, M.DEVICE}), "mag": (0.2, 1.0), "benign": True}
                else:
                    kind = {"name": "authorized_visit", "mods": frozenset({M.SPATIAL}), "mag": (1.0, 1.0), "benign": True}
            else:
                kind = kinds[int(rng.integers(0, len(kinds)))]
            length = float(rng.integers(120, 420))
            windows: dict[str, tuple[float, float]] = {}
            if kind.get("order"):  # staggered onsets in the given order, all persisting to the end
                step = float(rng.uniform(15, 45))
                windows = {m.value: (i * step, length) for i, m in enumerate(kind["order"])}
                length = max(length, (len(kind["order"]) - 1) * step + 90)
                windows = {k: (a, length) for k, (a, _b) in windows.items()}
            for _ in range(20):
                start = float(rng.uniform(warm, dur - length))
                if all(start >= e + MIN_GAP_S or start + length + MIN_GAP_S <= s for s, e in taken):
                    break
            else:
                continue
            taken.append((start, start + length))
            lo, hi = kind["mag"]
            mag = float(rng.uniform(lo, hi))
            drafts.append((start, dev, length, kind, mag, windows))
    drafts.sort(key=lambda d: (d[0], d[1]))
    out = []
    for i, (start, dev, length, kind, mag, windows) in enumerate(drafts, 1):
        benign = bool(kind.get("benign"))
        out.append(Episode(
            episode_id=f"S-{i:04d}", device_id=dev,
            scenario=ScenarioType.HARD_NEGATIVE if benign else ScenarioType.WEAK_CORRELATED,
            start=cfg.start + timedelta(seconds=start), end=cfg.start + timedelta(seconds=start + length),
            modalities=frozenset(kind["mods"]), magnitude=round(mag, 3), is_malicious=not benign, seed=cfg.seed,
            variant=kind["name"], windows_s=windows,
        ))
    return out


def _pair_kinds() -> list[dict]:
    """Anomaly combinations the fusion model never saw in training (it saw single signals, all four, spatial+network+
    device weak, and the staged spatial->network->device cascade)."""
    weak, moderate = (0.05, 0.25), (0.25, 0.5)
    pairs = [(M.SPATIAL, M.PROXIMITY), (M.NETWORK, M.PROXIMITY), (M.DEVICE, M.PROXIMITY), (M.SPATIAL, M.DEVICE), (M.SPATIAL, M.NETWORK), (M.NETWORK, M.DEVICE)]
    ks = [{"name": f"pair_{a.value}+{b.value}_weak", "mods": frozenset({a, b}), "mag": weak} for a, b in pairs]
    ks += [{"name": f"pair_{a.value}+{b.value}_moderate", "mods": frozenset({a, b}), "mag": moderate} for a, b in pairs[:4]]
    ks += [
        {"name": "triple_network+device+proximity_weak", "mods": frozenset({M.NETWORK, M.DEVICE, M.PROXIMITY}), "mag": weak},
        {"name": "triple_spatial+device+proximity_weak", "mods": frozenset({M.SPATIAL, M.DEVICE, M.PROXIMITY}), "mag": weak},
        {"name": "all_four_weak", "mods": frozenset(MODALITIES_ENUM), "mag": weak},
        {"name": "cascade_reversed_device>network>spatial", "mods": frozenset({M.DEVICE, M.NETWORK, M.SPATIAL}), "mag": weak, "order": [M.DEVICE, M.NETWORK, M.SPATIAL]},
        {"name": "cascade_proximity>device>network", "mods": frozenset({M.PROXIMITY, M.DEVICE, M.NETWORK}), "mag": weak, "order": [M.PROXIMITY, M.DEVICE, M.NETWORK]},
    ]
    return ks


MODALITIES_ENUM = [M.SPATIAL, M.NETWORK, M.DEVICE, M.PROXIMITY]


def _clip(x, lo, hi):
    return float(min(hi, max(lo, x)))


def make_noise(seed: int) -> Callable:
    """Sensor noise on top of every reading: lognormal bytes, jittery CPU/temperature/battery, connection counts."""
    rng = np.random.default_rng(np.random.SeedSequence([seed, 9001]))

    def f(t):
        return t.model_copy(update={
            "cpu_usage": _clip(t.cpu_usage + rng.normal(0, 6.0), 0, 100),
            "temperature": _clip(t.temperature + rng.normal(0, 2.5), -40, 150),
            "battery": _clip(t.battery + rng.normal(0, 0.6), 0, 100),
            "bytes_sent": int(t.bytes_sent * rng.lognormal(0, 0.6)),
            "bytes_received": int(t.bytes_received * rng.lognormal(0, 0.6)),
            "network_connections": max(0, t.network_connections + int(rng.integers(-2, 3))),
        })
    return f


def make_drift(seed: int, t_drift: float) -> Callable:
    """Benign, permanent behaviour change on every other device after `t_drift`: more traffic, more connections,
    busier CPU. Nothing malicious happens; any alert it causes is a false alarm."""
    def f(t):
        idx = int(t.device_id[2:])
        if idx % 2 or t.timestamp.timestamp() < t_drift:
            return t
        return t.model_copy(update={
            "bytes_sent": int(t.bytes_sent * 2.5), "bytes_received": int(t.bytes_received * 2.5),
            "network_connections": t.network_connections + 6, "cpu_usage": _clip(t.cpu_usage + 12.0, 0, 100),
        })
    return f


def make_gaps(seed: int) -> Callable:
    """Lost telemetry: 20% of ticks vanish at random, plus a 20-minute blackout per device per day."""
    rng = np.random.default_rng(np.random.SeedSequence([seed, 9002]))
    blackout: dict[str, tuple[float, float]] = {}

    def f(t):
        d, ts = t.device_id, t.timestamp.timestamp()
        day = int(ts // 86_400)
        key = f"{d}:{day}"
        if key not in blackout:
            s = float(rng.uniform(day * 86_400, day * 86_400 + 85_000))
            blackout[key] = (s, s + 1200.0)
        a, b = blackout[key]
        if a <= ts < b or rng.random() < 0.20:
            return None
        return t
    return f


CONDITIONS = {
    # name: (first seed, description)
    "control": 200,   # fresh seeds, the original generator: does the frozen design still behave on unseen runs?
    "unseen": 210,    # anomaly combinations the models never saw
    "benign": 220,    # six times the benign noise and four times the benign look-alikes
    "noisy": 230,     # noisy sensors
    "drift": 240,     # benign permanent behaviour change on half the devices
    "gaps": 250,      # lost telemetry
}
N_SEEDS = 5


def build_condition(cond: str, seed: int) -> RowSet:
    cfg_kw: dict = {}
    plan_fn: Callable[[SimConfig], list[Episode]] | None = None
    transform: Callable | None = None
    dur, warm = timedelta(hours=HOURS), timedelta(hours=WARM)
    cfg0 = SimConfig(seed=seed, n_devices=DEVICES)
    t_warm = (cfg0.start + warm).timestamp()

    if cond == "control":
        pass
    elif cond == "unseen":
        plan_fn = lambda cfg: _custom_plan(cfg, _pair_kinds(), per_device_per_day=4.0)  # noqa: E731
    elif cond == "benign":
        cfg_kw = dict(p_cpu_spike_per_tick=0.018, p_novel_endpoint_per_tick=0.012, p_visitor_per_tick=0.003)
        w = dict(DEFAULT_WEIGHTS)
        w[ScenarioType.HARD_NEGATIVE] = 6.0
        plan_fn = lambda cfg: build_plan(cfg, dur, warm, 4.0, weights=w)  # noqa: E731
    elif cond == "noisy":
        cfg_kw = dict(gps_sigma_m=10.0)
        transform = make_noise(seed)
    elif cond == "drift":
        transform = make_drift(seed, t_warm + 20 * 3600)
    elif cond == "gaps":
        transform = make_gaps(seed)
    else:
        raise ValueError(cond)

    cfg = replace(cfg0, **cfg_kw)
    plan = plan_fn(cfg) if plan_fn else build_plan(cfg, dur, warm, 4.0)
    engine = DetectionEngine(cfg.device_ids())
    idx = {d: i for i, d in enumerate(cfg.device_ids())}
    by_dev: dict[str, list[tuple[int, Episode]]] = {d: [] for d in cfg.device_ids()}
    for k, e in enumerate(plan):
        by_dev[e.device_id].append((k, e))
    buf = timedelta(seconds=POST_EPISODE_BUFFER_S)
    n_ticks = int(dur.total_seconds() // cfg.tick_s)
    cap = DEVICES * max(0, n_ticks - int(np.ceil(warm.total_seconds() / cfg.tick_s)))
    a_ts = np.empty(cap, np.float64); a_dev = np.empty(cap, np.int16)
    a_raw = np.empty((cap, len(MODALITIES)), np.float32); a_soft = np.empty((cap, len(MODALITIES)), np.float32)
    a_feat = np.empty((cap, len(FEATURE_NAMES)), np.float32)
    a_label = np.empty(cap, np.int8); a_ign = np.empty(cap, bool); a_ep = np.empty(cap, np.int32)
    a_sc = np.empty(cap, np.int8); a_mag = np.empty(cap, np.float32); a_learn = np.empty(cap, bool)
    n = 0
    for t in Fleet(cfg, plan).run(dur):
        if transform is not None:
            t = transform(t)
            if t is None:
                continue
        s = engine.process(t)
        ts = t.timestamp.timestamp()
        if ts < t_warm:
            continue
        ev: dict[str, float] = {}
        for r in s.results.values():
            ev |= r.evidence
        feat = [max(-10.0, min(10.0, ev.get(nm, 0.0))) for nm in Z_NAMES] + [s.features.get(nm, 0.0) for nm in RULE_NAMES]
        label, ignore, epi, sc, mag = 0, False, -1, -1, 0.0
        for k, e in by_dev[t.device_id]:
            if e.start <= t.timestamp < e.end:
                label, epi, sc, mag = int(e.is_malicious), k, SCENARIO_CODES.index(e.scenario), e.magnitude
                break
            if e.end <= t.timestamp < e.end + buf:
                ignore, epi = True, k
        a_ts[n], a_dev[n] = ts, idx[t.device_id]
        for j, m in enumerate(MODALITIES):
            a_raw[n, j], a_soft[n, j] = s.results[m].score, s.results[m].soft
        a_feat[n] = feat
        a_label[n], a_ign[n], a_ep[n], a_sc[n], a_mag[n], a_learn[n] = label, ignore, epi, sc, mag, s.learning
        n += 1
    return RowSet(seed, DEVICES, a_ts[:n].copy(), a_dev[:n].copy(), a_raw[:n].copy(), a_soft[:n].copy(), a_feat[:n].copy(),
                  a_label[:n].copy(), a_ign[:n].copy(), a_ep[:n].copy(), a_sc[:n].copy(), a_mag[:n].copy(), a_learn[:n].copy(), plan)


def _job(args: tuple) -> dict:
    cond, seed, out = args
    rs = build_condition(cond, seed)
    rs.save(Path(out))
    return {"condition": cond, "seed": seed, "rows": len(rs), "episodes": len(rs.episodes), "malicious_rows": int(rs.label.sum())}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--condition", required=True, choices=[*CONDITIONS, "all"])
    ap.add_argument("--seeds", type=int, nargs=2, metavar=("FIRST", "LAST"))
    ap.add_argument("--out-dir", type=Path, default=Path("data/stress"))
    ap.add_argument("--workers", type=int, default=5)
    a = ap.parse_args()
    conds = list(CONDITIONS) if a.condition == "all" else [a.condition]
    jobs = []
    for c in conds:
        first = a.seeds[0] if a.seeds else CONDITIONS[c]
        last = a.seeds[1] if a.seeds else first + N_SEEDS - 1
        for s in range(first, last + 1):
            assert s >= 200, "stress seeds are 200+; 10-19 are validation and 100-109 are the spent test seeds"
            out = a.out_dir / c / f"seed{s}.npz"
            if not out.exists():
                jobs.append((c, s, out))
    from concurrent.futures import ProcessPoolExecutor
    with ProcessPoolExecutor(max_workers=a.workers) as pool:
        for r in pool.map(_job, jobs):
            print(json.dumps(r), flush=True)


if __name__ == "__main__":
    main()
