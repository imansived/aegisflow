"""Labelled detector-output rows: the input to fusion training and evaluation.

One simulation run -> one `RowSet` saved as .npz. It stores the RAW per-modality detector
scores and the engineered features per event, plus ground truth from the episode plan. The
time window (decay half-life) is applied OFFLINE (`hold_offline`), so it can be swept without
re-simulating, which takes minutes per seed.

  python -m ml.dataset --seed 1 --devices 20 --hours 72 --warmup-hours 33 --out data/rows/seed1.npz
"""

import argparse
import json
from dataclasses import dataclass
from datetime import timedelta
from pathlib import Path

import numpy as np

from backend.correlation.fusion import MODALITIES, Fusion
from backend.detection.config import DetectionConfig
from backend.detection.engine import DetectionEngine
from contracts.scenarios import Episode, ScenarioType
from simulator.config import SimConfig
from simulator.fleet import Fleet
from simulator.plan import build_plan

# z-scored features (from detector evidence; 0 when not available or still learning), then
# rule features (from the engineered features).
Z_NAMES = ["log_sent", "log_recv", "conns", "unfamiliar_1m", "cpu", "temp", "drain", "unenrolled", "persist_s"]
RULE_NAMES = ["in_restricted", "zone_rarity", "speed"]
FEATURE_NAMES = Z_NAMES + RULE_NAMES
SCENARIO_CODES = list(ScenarioType)
POST_EPISODE_BUFFER_S = 120.0  # lingering scores right after an episode are not judged


@dataclass
class RowSet:
    seed: int
    n_devices: int
    ts: np.ndarray  # float64 epoch seconds
    device: np.ndarray  # int16
    raw: np.ndarray  # (N, 4) float32, HARD detector scores (z <= 2 -> 0) in MODALITIES order
    raw_soft: np.ndarray  # (N, 4) float32, SOFT scores that keep weak evidence (§5.5)
    feat: np.ndarray  # (N, len(FEATURE_NAMES)) float32
    label: np.ndarray  # int8: 1 = inside a MALICIOUS episode
    ignore: np.ndarray  # bool: just after an episode, not judged
    episode: np.ndarray  # int32 index into `episodes`, -1 = none
    scenario: np.ndarray  # int8 index into SCENARIO_CODES, -1 = none (ordinary behaviour)
    magnitude: np.ndarray  # float32, 0 outside episodes
    learning: np.ndarray  # bool: some baseline still in cold start
    episodes: list[Episode]

    def save(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        np.savez_compressed(
            path, seed=self.seed, n_devices=self.n_devices, ts=self.ts, device=self.device, raw=self.raw,
            raw_soft=self.raw_soft, feat=self.feat, label=self.label, ignore=self.ignore, episode=self.episode,
            scenario=self.scenario, magnitude=self.magnitude, learning=self.learning,
            episodes=np.array([e.model_dump_json() for e in self.episodes] or [""]),
        )

    @classmethod
    def load(cls, path: Path) -> "RowSet":
        z = np.load(path, allow_pickle=False)
        eps = [Episode.model_validate_json(s) for s in z["episodes"] if str(s)]
        if "raw_soft" not in z.files:
            raise ValueError(f"{path} predates soft scores (§5.5); rebuild it with ml.dataset")
        return cls(
            int(z["seed"]), int(z["n_devices"]), z["ts"], z["device"], z["raw"], z["raw_soft"], z["feat"],
            z["label"], z["ignore"], z["episode"], z["scenario"], z["magnitude"], z["learning"], eps,
        )

    def __len__(self) -> int:
        return len(self.ts)


def hold_offline(ts: np.ndarray, device: np.ndarray, x: np.ndarray, half_life_s: float) -> np.ndarray:
    """Per-device decayed peak of each column. Identical maths to CorrelationEngine (tested)."""
    out = np.empty_like(x)
    cur: dict[int, np.ndarray] = {}
    last: dict[int, float] = {}
    for i in range(len(ts)):
        d = int(device[i])
        prev = cur.get(d)
        e = x[i].copy() if prev is None else np.maximum(x[i], prev * 0.5 ** ((ts[i] - last[d]) / half_life_s))
        cur[d], last[d], out[i] = e, ts[i], e
    return out


def apply_fusion(fusion: Fusion, eff: np.ndarray) -> np.ndarray:
    """Score every row. Uses the fusion's vectorised `score_batch` (tested equal to per-row)."""
    return fusion.score_batch(np.asarray(eff, dtype=np.float64))


def build_rows(
    seed: int,
    n_devices: int = 20,
    hours: float = 72,
    warmup_hours: float = 33,
    episodes_per_device_per_day: float = 4.0,
    magnitude: tuple[float, float] | None = None,
    det_cfg: DetectionConfig | None = None,
) -> RowSet:
    cfg = SimConfig(seed=seed, n_devices=n_devices)
    dur, warm = timedelta(hours=hours), timedelta(hours=warmup_hours)
    plan = build_plan(cfg, dur, warm, episodes_per_device_per_day, magnitude=magnitude)
    engine = DetectionEngine(cfg.device_ids(), det_cfg)
    idx = {d: i for i, d in enumerate(cfg.device_ids())}
    by_dev: dict[str, list[tuple[int, Episode]]] = {d: [] for d in cfg.device_ids()}
    for k, e in enumerate(plan):
        by_dev[e.device_id].append((k, e))
    t_warm = (cfg.start + warm).timestamp()
    buf = timedelta(seconds=POST_EPISODE_BUFFER_S)

    # Preallocated arrays, not Python lists of lists (which cost ~800 bytes per row and made parallel
    # builds thrash a memory-starved machine; manual §4.6). Capacity = post-warm-up ticks x devices.
    n_ticks = int(dur.total_seconds() // cfg.tick_s)
    cap = n_devices * max(0, n_ticks - int(np.ceil(warm.total_seconds() / cfg.tick_s)))
    a_ts = np.empty(cap, np.float64)
    a_dev = np.empty(cap, np.int16)
    a_raw = np.empty((cap, len(MODALITIES)), np.float32)
    a_soft = np.empty((cap, len(MODALITIES)), np.float32)
    a_feat = np.empty((cap, len(FEATURE_NAMES)), np.float32)
    a_label = np.empty(cap, np.int8)
    a_ign = np.empty(cap, bool)
    a_ep = np.empty(cap, np.int32)
    a_sc = np.empty(cap, np.int8)
    a_mag = np.empty(cap, np.float32)
    a_learn = np.empty(cap, bool)
    n = 0
    for t in Fleet(cfg, plan).run(dur):
        s = engine.process(t)
        ts = t.timestamp.timestamp()
        if ts < t_warm:
            continue
        ev: dict[str, float] = {}
        for r in s.results.values():
            ev |= r.evidence
        feat = [max(-10.0, min(10.0, ev.get(n, 0.0))) for n in Z_NAMES] + [s.features.get(n, 0.0) for n in RULE_NAMES]

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

    return RowSet(
        seed, n_devices, a_ts[:n].copy(), a_dev[:n].copy(), a_raw[:n].copy(), a_soft[:n].copy(), a_feat[:n].copy(),
        a_label[:n].copy(), a_ign[:n].copy(), a_ep[:n].copy(), a_sc[:n].copy(), a_mag[:n].copy(), a_learn[:n].copy(), plan,
    )


def _build_and_save(args: tuple) -> dict:
    seed, devices, hours, warmup, rate, magnitude, out = args
    rs = build_rows(seed, devices, hours, warmup, rate, magnitude)
    rs.save(Path(out))
    return {"seed": seed, "rows": len(rs), "episodes": len(rs.episodes), "malicious_rows": int(rs.label.sum()), "out": str(out)}


def main() -> None:
    ap = argparse.ArgumentParser()
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--seed", type=int)
    g.add_argument("--seeds", type=int, nargs=2, metavar=("FIRST", "LAST"), help="inclusive range, built in parallel")
    ap.add_argument("--devices", type=int, default=20)
    ap.add_argument("--hours", type=float, default=72)
    ap.add_argument("--warmup-hours", type=float, default=33)
    ap.add_argument("--rate", type=float, default=4.0)
    ap.add_argument("--magnitude", type=float, nargs=2)
    ap.add_argument("--out", type=Path, help="file for --seed")
    ap.add_argument("--out-dir", type=Path, default=Path("data/rows"), help="directory for --seeds (seedN.npz)")
    ap.add_argument("--workers", type=int, default=6)
    a = ap.parse_args()
    mag = tuple(a.magnitude) if a.magnitude else None
    if a.seed is not None:
        jobs = [(a.seed, a.devices, a.hours, a.warmup_hours, a.rate, mag, a.out or a.out_dir / f"seed{a.seed}.npz")]
    else:
        jobs = [(s, a.devices, a.hours, a.warmup_hours, a.rate, mag, a.out_dir / f"seed{s}.npz") for s in range(a.seeds[0], a.seeds[1] + 1)]
    if len(jobs) == 1:
        print(json.dumps(_build_and_save(jobs[0])))
        return
    from concurrent.futures import ProcessPoolExecutor

    with ProcessPoolExecutor(max_workers=a.workers) as pool:
        for r in pool.map(_build_and_save, jobs):
            print(json.dumps(r), flush=True)


if __name__ == "__main__":
    main()
