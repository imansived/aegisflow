"""Independent check on REAL data: TON_IoT Windows host telemetry (UNSW Canberra), manual §5.9.

What this dataset can and cannot validate
-----------------------------------------
Each row is one second-scale snapshot of one Windows host's performance counters, labelled normal or one of several
attack types launched against it in a testbed. It carries two of AegisFlow's four signal families in the SAME row:
host health (CPU, process, memory, disk counters; our "device" signal) and network (NIC byte/packet counters; our
"network" signal). It has NO location and NO proximity signal, no per-device identity beyond one host, and the
published Train_Test files hold no timestamps (the rows are in capture order inside each label block, but spacing is
undocumented). So it can test ONE thing about the central claim: does fusing independent per-group anomaly scores
beat the best single group and the independent max, on real attacks? It cannot test spatial or proximity detection,
the correlation window, per-device baselines, alert onsets, or time to detect. The attack classes (ddos, password
guessing, scanning ...) are network attacks on a host, not the "weak, simultaneous, below-threshold" behaviour the
simulator was built to produce, so a small fusion advantage here would be evidence about real data, but a null result
would not refute the simulator-based claim either.

Protocol (fixed before looking at any result):
  - three feature groups: network (NIC counters), cpu_proc (Processor + Process), mem_disk (Memory + LogicalDisk);
  - normal rows in file order are split 50% fit / 25% threshold / 25% test (chronological within the capture);
  - every group detector is unsupervised: a robust per-feature z-score against the fit rows (log1p scale), group
    evidence = mean of the three largest |z|, soft score 1 - exp(-0.5 (z/3)^2) as in the frozen design;
  - thresholds: each system's score quantile on the threshold-normal rows at a 1% row false-positive rate (the
    simulator's 0.1% cannot be resolved with ~2,500 normal test rows);
  - fusion systems are the frozen design's classes (max, weighted sum, noisy-OR, LearnedFusion) fed the three group
    scores; trainable systems (learned fusion, supervised gradient boosting) are scored leave-one-attack-type-out so
    they never see the attack type they are tested on.
"""

import csv
import json
import sys
from pathlib import Path

import numpy as np
from sklearn.ensemble import HistGradientBoostingClassifier, IsolationForest
from sklearn.metrics import roc_auc_score

from backend.correlation.fusion import LearnedFusion, MaxFusion, NoisyOr, WeightedSum

SIGMA = 3.0
FPR = 0.01
GROUPS = ("network", "cpu_proc", "mem_disk")


def load(path: Path):
    with open(path, encoding="utf-8", errors="ignore") as fh:
        r = csv.reader(fh)
        header = [h.lstrip("﻿") for h in next(r)]
        rows = list(r)
    li, ti = header.index("label"), header.index("type")
    feat_cols = [i for i, h in enumerate(header) if i not in (li, ti)]
    def num(x: str) -> float:
        try:
            v = float(x)
            return v if np.isfinite(v) else 0.0
        except ValueError:
            return 0.0  # blank or "-" cells: treated as zero, and counted below

    X = np.array([[num(row[i]) for i in feat_cols] for row in rows], np.float64)
    names = [header[i] for i in feat_cols]
    return names, X, np.array([int(row[li]) for row in rows]), np.array([row[ti] for row in rows])


def group_of(name: str) -> str:
    n = name.lower()
    if n.startswith("network"):
        return "network"
    if n.startswith("memory") or n.startswith("logicaldisk"):
        return "mem_disk"
    return "cpu_proc"  # Processor_*, Process_*


class GroupDetectors:
    """Robust per-feature z-scores against fit rows; one soft score per feature group."""

    def __init__(self, strict: bool = False):
        self.strict = strict  # strict: ignore counters that never vary on normal rows (they act as session fingerprints)

    def fit(self, names, X):
        L = np.log1p(np.clip(X, 0, None))
        self.med = np.median(L, axis=0)
        mad = 1.4826 * np.median(np.abs(L - self.med), axis=0)
        sd = L.std(axis=0)
        self.scale = np.where(np.maximum(mad, sd) > 1e-9, np.maximum(mad, sd), 1.0)  # constant-on-normal: 1 e-fold = 1 unit
        keep = (sd > 1e-9) if self.strict else np.ones(len(names), bool)
        self.cols = {g: [i for i, nme in enumerate(names) if group_of(nme) == g and keep[i]] for g in GROUPS}
        self.n_dropped = int((~keep).sum())
        return self

    def z(self, X):
        return np.abs(np.log1p(np.clip(X, 0, None)) - self.med) / self.scale

    def soft(self, X):
        Z = self.z(X)
        out = np.zeros((len(X), 3))
        for k, g in enumerate(GROUPS):
            top = np.sort(Z[:, self.cols[g]], axis=1)[:, -3:].mean(axis=1)
            out[:, k] = 1.0 - np.exp(-0.5 * (np.minimum(top, 10.0) / SIGMA) ** 2)
        return out, Z


def recall_at(s_att, thr):
    return float((s_att > thr).mean()) if len(s_att) else float("nan")


def run(path: Path, label: str, strict: bool = False) -> dict:
    names, X, y, typ = load(path)
    norm = np.flatnonzero(y == 0)
    n = len(norm)
    fit_i, thr_i, tst_i = norm[: n // 2], norm[n // 2: 3 * n // 4], norm[3 * n // 4:]
    att_types = sorted(set(typ[y == 1]))
    print(f"\n######## {label}: {len(X)} rows, {X.shape[1]} counters; normal {n} (fit {len(fit_i)} / threshold {len(thr_i)} / test {len(tst_i)})")
    print("attack rows by type:", {t: int((typ == t).sum()) for t in att_types})

    det = GroupDetectors(strict).fit(names, X[fit_i])
    if strict:
        print(f"strict mode: ignoring {det.n_dropped} counters that never vary on the fit rows")
    S, Z = det.soft(X)  # (n_rows, 3) in GROUPS order; Z = per-feature z for the early-fusion controls
    # slot mapping for the frozen fusion classes, which expect [spatial, network, device, proximity]:
    # mem_disk -> slot 0, network -> slot 1, cpu_proc -> slot 2, slot 3 stays 0 (this is slot reuse, not a claim).
    E = np.zeros((len(X), 4))
    E[:, 0], E[:, 1], E[:, 2] = S[:, 2], S[:, 0], S[:, 1]
    forest = IsolationForest(n_estimators=200, random_state=0).fit(np.minimum(Z[fit_i], 10.0))
    s_if = -forest.score_samples(np.minimum(Z, 10.0))

    results: dict = {}
    systems = ["network_group", "cpu_proc_group", "mem_disk_group", "max", "weighted_sum", "noisy_or", "learned", "early_iforest", "early_supervised"]
    fixed = {
        "network_group": S[:, 0], "cpu_proc_group": S[:, 1], "mem_disk_group": S[:, 2],
        "max": MaxFusion().score_batch(E), "weighted_sum": WeightedSum().score_batch(E), "noisy_or": NoisyOr().score_batch(E),
        "early_iforest": s_if,
    }
    rng = np.random.default_rng(0)
    for held in att_types:
        att_i = np.flatnonzero(typ == held)
        if len(att_i) < 30:
            continue
        train_att = np.flatnonzero((y == 1) & (typ != held))
        # trainable systems: fit on fit-normal + OTHER attack types, never the held-out one
        tr = np.concatenate([fit_i, train_att])
        if len(train_att) > 4 * len(fit_i):
            train_att = rng.choice(train_att, 4 * len(fit_i), replace=False)
            tr = np.concatenate([fit_i, train_att])
        lf = LearnedFusion().fit(E[tr], y[tr])
        w = np.where(y[tr] == 1, 0.5 / max(1, (y[tr] == 1).sum()), 0.5 / max(1, (y[tr] == 0).sum())) * len(tr)
        gb = HistGradientBoostingClassifier(random_state=0, max_iter=150).fit(np.minimum(Z[tr], 10.0), y[tr], sample_weight=w)
        scores = dict(fixed)
        scores["learned"] = lf.score_batch(E)
        scores["early_supervised"] = gb.predict_proba(np.minimum(Z, 10.0))[:, 1]
        row = {}
        for name in systems:
            s = scores[name]
            thr = float(np.quantile(s[thr_i], 1 - FPR))
            auc = float(roc_auc_score(np.r_[np.zeros(len(tst_i)), np.ones(len(att_i))], np.r_[s[tst_i], s[att_i]]))
            row[name] = {"recall": recall_at(s[att_i], thr), "fpr_test": float((s[tst_i] > thr).mean()), "auroc": auc}
        results[held] = {"n_attack_rows": int(len(att_i)), "systems": row}

    print(f"\nrecall at {FPR:.0%} false-positive rate on held-out normal rows (AUROC in brackets); trainable systems never saw the tested attack type")
    print(f"{'attack':11s} {'n':>5s} " + " ".join(f"{s[:13]:>14s}" for s in systems))
    for t, r in results.items():
        print(f"{t:11s} {r['n_attack_rows']:5d} " + " ".join(f"{r['systems'][s]['recall']:5.2f} ({r['systems'][s]['auroc']:.2f})" for s in systems))
    # macro across attack types (each type counts once; no type is dominated by its row count)
    macro = {s: float(np.mean([r["systems"][s]["recall"] for r in results.values()])) for s in systems}
    macro_auc = {s: float(np.mean([r["systems"][s]["auroc"] for r in results.values()])) for s in systems}
    print(f"{'MACRO':11s} {'':5s} " + " ".join(f"{macro[s]:5.2f} ({macro_auc[s]:.2f})" for s in systems))
    wins = {s: sum(r["systems"]["learned"]["auroc"] > r["systems"][s]["auroc"] for r in results.values()) for s in systems}
    print("attack types where learned fusion has a higher AUROC than each system:", {k: f"{v}/{len(results)}" for k, v in wins.items() if k != "learned"})
    return {"label": label, "results": results, "macro_recall": macro, "macro_auroc": macro_auc}


def session_check(path: Path) -> None:
    """Are two NORMAL capture blocks distinguishable from each other? If a detector fitted on one normal block flags
    the other normal block, the labels are confounded with the capture session and attack recall is inflated."""
    names, X, y, typ = load(path)
    norm = np.flatnonzero(y == 0)
    cuts = np.flatnonzero(np.diff(norm) > 1) + 1
    blocks = np.split(norm, cuts)
    print(f"\nsession check {path.name}: normal blocks of sizes {[len(b) for b in blocks]}")
    if len(blocks) < 2:
        print("  only one normal block: cannot check")
        return
    big = max(blocks, key=len)
    small = [b for b in blocks if b is not big][0]
    fit_i, hold_i = big[: len(big) // 2], big[len(big) // 2:]
    for strict in (False, True):
        det = GroupDetectors(strict).fit(names, X[fit_i])
        S, _ = det.soft(X)
        thr = np.quantile(S[hold_i], 0.99, axis=0)
        print(f"  strict={strict}: share of the OTHER normal block above the 1%-FPR threshold per group "
              f"{dict(zip(GROUPS, np.round((S[small] > thr).mean(axis=0), 2)))}  (a clean dataset would show ~0.01)")


if __name__ == "__main__":
    out = {}
    session_check(Path("data/real/toniot/Train_Test_Windows_10.csv"))
    for f, lab in (("Train_Test_Windows_10.csv", "Windows 10"), ("Train_Test_Windows_7.csv", "Windows 7")):
        out[lab] = run(Path("data/real/toniot") / f, lab)
        out[lab + " (strict)"] = run(Path("data/real/toniot") / f, lab + " (strict: constant counters ignored)", strict=True)
    Path("data/audit").mkdir(parents=True, exist_ok=True)
    Path("data/audit/toniot.json").write_text(json.dumps(out, indent=1))
    print("\nwrote data/audit/toniot.json")
