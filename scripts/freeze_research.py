"""Write an immutable research manifest: every artifact behind the published claims, with its hash and the commits that made it.

  python scripts/freeze_research.py v1        # writes docs/research/v1/manifest.json

A manifest is never edited afterwards. A change to the model, the thresholds, a protocol or a result is a NEW version (v2, ...) with its own
manifest and its own evaluation on fresh seeds; `tests/test_frozen_artifact.py` fails if anything a manifest lists has changed.
"""

import hashlib
import json
import platform
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def sha256(p: Path) -> str:
    h = hashlib.sha256()
    with open(p, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def git(*a: str) -> str:
    return subprocess.run(["git", *a], capture_output=True, text=True, cwd=ROOT).stdout.strip()


# Each experiment: the protocol, the commit that froze it, the code, the raw results, the seeds, what it supports.
EXPERIMENTS = [
    {"id": "E1", "question": "Does keeping weak evidence (soft scores) close the late-vs-early fusion gap?", "seeds": "validation 10-19",
     "protocol": "manual §5.5 (local)", "results": ["docs/results/e1.json", "docs/results/e1_sweep_ext.json"], "code": ["ml/validate.py"]},
    {"id": "E2", "question": "Does a time window help with staggered deviations?", "seeds": "validation 10-19",
     "protocol": "manual §5.6 (local)", "results": ["docs/results/e2.json"], "code": ["ml/validate.py"]},
    {"id": "F1", "question": "Final test of late fusion against single detectors and controls", "seeds": "test 100-109 (spent)",
     "protocol": "manual §5.7 (local)", "preregistration_commit": "8294f17", "results": ["docs/results/f1.json", "docs/results/f1_dry.json"],
     "code": ["ml/final_eval.py", "ml/validate.py"], "guard": "data/results/F1_TEST_SEEDS_USED"},
    {"id": "AUDIT", "question": "Per-seed spread, alert-level precision/F1, six stress conditions, two real datasets (EXPLORATORY, post hoc)",
     "seeds": "re-scores 100-109; fresh 200-254", "protocol": "manual §5.10 (local)", "commit": "37cc1f2",
     "results": ["docs/results/audit.json", "docs/results/audit_summary.txt", "docs/results/toniot.json", "docs/results/iot23.json"],
     "code": ["ml/audit.py", "ml/stress_build.py", "ml/audit_report.py", "ml/realdata_toniot.py", "ml/realdata_iot23.py"]},
    {"id": "D106", "question": "Does missingness-aware fusion degrade more gracefully than reading a missing signal as zero?", "seeds": "test 300-309 (spent)",
     "protocol": "docs/preregistration/D106-missing-signals.md", "preregistration_commit": "4733306", "code_commit": "8ca1069",
     "results": ["docs/results/d106_final.json", "docs/results/d106_dry.json", "docs/results/d106_ablation.json"],
     "code": ["ml/missing.py", "ml/missing_ablation.py"], "guard": "data/results/D106_TEST_SEEDS_USED"},
    {"id": "D107", "question": "Does the shipped availability handling (explicit nulls, per-pattern thresholds, fallback, Degraded state) work end to end?",
     "seeds": "test 400-409 (spent)", "protocol": "docs/preregistration/D107-availability-implementation.md", "preregistration_commit": "8eacdbb",
     "addenda_commits": ["5eca50a", "232dbc0", "dbfd2f4"], "results_commit": "cbda3e8", "results": ["docs/results/d107_final.json", "docs/results/d107_final.log"],
     "code": ["ml/d107.py", "ml/d107_eval.py", "ml/export_availability.py"], "guard": "data/results/D107_TEST_SEEDS_USED"},
]
ARTIFACTS = [
    ("models/frozen_design.json", "the learned late-fusion model and alert thresholds served by the API (fitted on validation seeds 10-15, thresholds 16-19)"),
    ("models/availability_thresholds.json", "validated alert thresholds per availability pattern (calibrated on validation seeds 16-19, budget 3.0 false onsets per device-day)"),
    ("docs/preregistration/D106-missing-signals.md", "D106 protocol (frozen before its code and seeds)"),
    ("docs/preregistration/D107-availability-implementation.md", "D107 protocol with its three pre-scoring addenda"),
]
DATA = {
    "validation rows (fit and thresholds)": ("data/rows", [f"seed{s}.npz" for s in range(10, 20)]),
    "F1 test rows": ("data/rows", [f"seed{s}.npz" for s in range(100, 110)]),
    "D106 test rows": ("data/rows", [f"seed{s}.npz" for s in range(300, 310)]),
}


def main(version: str) -> None:
    out_dir = ROOT / "docs" / "research" / version
    if (out_dir / "manifest.json").exists():
        raise SystemExit(f"{out_dir / 'manifest.json'} exists: a manifest is immutable. Make a new version instead.")
    files: dict[str, dict] = {}
    for rel, role in ARTIFACTS:
        files[rel] = {"sha256": sha256(ROOT / rel), "role": role, "last_commit": git("log", "-1", "--format=%h", "--", rel)}
    for e in EXPERIMENTS:
        for rel in e["results"] + e["code"]:
            files.setdefault(rel, {"sha256": sha256(ROOT / rel), "role": f"{e['id']} " + ("result" if rel in e["results"] else "code"),
                                   "last_commit": git("log", "-1", "--format=%h", "--", rel)})
    data = {}
    for name, (d, names) in DATA.items():
        data[name] = {n: sha256(ROOT / d / n) for n in names if (ROOT / d / n).exists()}
    d107 = {}
    for p in sorted((ROOT / "data" / "d107").glob("*/*.npz")) if (ROOT / "data" / "d107").exists() else []:
        d107[str(p.relative_to(ROOT / "data")).replace("\\", "/")] = sha256(p)
    data["D107 end-to-end runs (data/d107)"] = d107
    manifest = {
        "version": version, "created": datetime.now(timezone.utc).isoformat(),
        "frozen_at_commit": git("rev-parse", "--short", "HEAD"), "git_tag": f"research-{version}",
        "statement": "These artifacts support the headline claims in docs/RESEARCH.md. They are never overwritten: a change is a new version "
                     "with a new evaluation on fresh seeds. Raw data files are not in git; their hashes are here so a regenerated copy can be checked.",
        "environment": {"python": sys.version.split()[0], "platform": platform.platform(),
                        "packages": {k: v for k, v in (l.split("==") for l in subprocess.run([sys.executable, "-m", "pip", "freeze"], capture_output=True, text=True).stdout.split() if "==" in l)
                                     if k.lower() in ("numpy", "scikit-learn", "scipy", "pydantic", "fastapi", "redis")}},
        "experiments": EXPERIMENTS, "files": files, "data": data,
        "regenerate": {
            "validation and test rows": "python -m ml.dataset --seeds 10 19 ; --seeds 100 109 ; --seeds 300 309  (20 devices, 72 h, 33 h warm-up, 4 episodes/device/day)",
            "D107 runs": "python -m ml.d107 build   (50 end-to-end runs through the product pipeline; Windows: if the process pool dies with WinError 6, run build_run per job)",
            "models": "python -m ml.export_design ; python -m ml.export_availability",
            "evaluations": "python -m ml.final_eval (F1, refuses: guard) ; python -m ml.missing --mode final (D106, guard) ; python -m ml.d107_eval (D107, guard)",
        },
        "spent_seeds": {"validation": "10-19 (fit/threshold use only)", "F1": "100-109", "D106": "300-309", "D107": "400-409", "stress and audit": "200-254"},
    }
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "manifest.json").write_text(json.dumps(manifest, indent=2))
    print("wrote", out_dir / "manifest.json", "with", len(files), "files and", sum(len(v) for v in data.values()), "data hashes")


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else "v1")
