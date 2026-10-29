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


R1 = {
    "experiments": [{"id": "R1", "question": "How does the unchanged research-v1 system behave on real IoT-23 traces (network signal only)?",
                     "seeds": "n/a (public captures)", "protocol": "docs/preregistration/R1-iot23-replay.md", "preregistration_commit": "e6c9658",
                     "results": ["docs/results/r1_replay.json", "docs/results/r1_compare.json"],
                     "code": ["ml/replay_iot23.py", "ml/replay_compare.py"], "system_under_test": "git tag research-v1 (unchanged)"}],
    "artifacts": [("docs/preregistration/R1-iot23-replay.md", "R1 protocol, with its addendum")],
    "data": {"IoT-23 captures replayed (public, Stratosphere Laboratory)": ("data/real/iot23", None)},
}


def main(version: str) -> None:
    if version == "r1":
        return main_r1()
    if version == "r2":
        return main_r2()
    if version == "r3r4":
        return main_r3r4()
    if version == "r5inv":
        return main_r5inv()
    if version == "r5":
        return main_r5()
    if version == "r6audit":
        return main_r6audit()
    if version == "r7audit":
        return main_r7audit()
    return main_v(version)


def main_r1() -> None:
    out_dir = ROOT / "docs" / "research" / "r1"
    if (out_dir / "manifest.json").exists():
        raise SystemExit("docs/research/r1/manifest.json exists: a manifest is immutable.")
    files = {}
    for rel, role in R1["artifacts"]:
        files[rel] = {"sha256": sha256(ROOT / rel), "role": role, "last_commit": git("log", "-1", "--format=%h", "--", rel)}
    for e in R1["experiments"]:
        for rel in e["results"] + e["code"]:
            files[rel] = {"sha256": sha256(ROOT / rel), "role": "R1 " + ("result" if rel in e["results"] else "code"), "last_commit": git("log", "-1", "--format=%h", "--", rel)}
    caps = {f"real/iot23/{p.name}": sha256(p) for p in sorted((ROOT / "data" / "real" / "iot23").glob("*.labeled"))}
    manifest = {"version": "r1", "created": datetime.now(timezone.utc).isoformat(), "frozen_at_commit": git("rev-parse", "--short", "HEAD"), "git_tag": "replay-r1",
                "statement": "Exploratory replay of the UNCHANGED research-v1 system on real traces. research-v1's own manifest is not touched; a design change prompted by "
                             "these results is a new version with its own protocol and evaluation.",
                "system_under_test": {"tag": "research-v1", "commit": git("rev-parse", "--short", "research-v1")},
                "experiments": R1["experiments"], "files": files, "data": {"IoT-23 captures replayed (public, Stratosphere Laboratory)": caps}}
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "manifest.json").write_text(json.dumps(manifest, indent=2))
    print("wrote", out_dir / "manifest.json", len(files), "files", len(caps), "capture hashes")


def main_r2() -> None:
    out_dir = ROOT / "docs" / "research" / "r2"
    if (out_dir / "manifest.json").exists():
        raise SystemExit("docs/research/r2/manifest.json exists: a manifest is immutable.")
    rels = {"docs/preregistration/R2-sparse-network-detector.md": "R2 protocol (committed before any candidate code or test data)",
            "docs/results/r2_validation.json": "R2 validation result: per-arm thresholds and the candidate choice, committed before the test data was opened",
            "docs/results/r2_test.json": "R2 test result (single run, guard data/results/R2_TEST_USED)",
            "ml/r2_adapters.py": "R2 code", "ml/r2_detectors.py": "R2 code", "ml/r2_eval.py": "R2 code"}
    files = {r: {"sha256": sha256(ROOT / r), "role": role, "last_commit": git("log", "-1", "--format=%h", "--", r)} for r, role in rels.items()}
    data = {}
    for pat in ("data/real/unsw/16-10-1[234].*", "data/real/iot23_test/*.raw"):
        for q in sorted(ROOT.glob(pat)):
            if q.is_file():
                data[q.relative_to(ROOT / "data").as_posix()] = sha256(q)
    manifest = {"version": "r2", "created": datetime.now(timezone.utc).isoformat(), "frozen_at_commit": git("rev-parse", "--short", "HEAD"), "git_tag": "sparse-r2",
                "statement": "Offline evaluation of two candidate sparse-network detectors beside the unchanged research-v1 system. Nothing in backend/, models/ or contracts/ is changed.",
                "system_under_test": {"tag": "research-v1", "commit": git("rev-parse", "--short", "research-v1")},
                "files": files, "data": {"R2 validation and test data (public: UNSW IoT testbed, Stratosphere IoT-23)": data}}
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "manifest.json").write_text(json.dumps(manifest, indent=2))
    print("wrote", out_dir / "manifest.json", len(files), "files", len(data), "data hashes")


def main_r3r4() -> None:
    out_dir = ROOT / "docs" / "research" / "r3r4"
    if (out_dir / "manifest.json").exists():
        raise SystemExit("docs/research/r3r4/manifest.json exists: a manifest is immutable.")
    rels = {"docs/preregistration/R3-R4-observation-semantics.md": "R3/R4 specification and acceptance criteria (committed before the implementation)",
            "docs/results/r3r4_release.json": "R3/R4 release evidence: test results, instability finding, remaining limitations",
            "tests/test_observation.py": "R3/R4 acceptance tests"}
    files = {r: {"sha256": sha256(ROOT / r), "role": role, "last_commit": git("log", "-1", "--format=%h", "--", r)} for r, role in rels.items()}
    code = ["backend/detection/engine.py", "backend/detection/types.py", "backend/correlation/engine.py", "backend/correlation/explain.py", "backend/services/alerts.py",
            "backend/services/pipeline.py", "backend/database/store.py", "backend/api/app.py", "backend/api/runtime.py"]
    manifest = {"version": "r3r4", "created": datetime.now(timezone.utc).isoformat(), "frozen_at_commit": git("rev-parse", "--short", "HEAD"), "git_tag": "observation-r3r4",
                "statement": "Release evidence for declared signal sets and partial-observation wording. The product code is pinned by the git tag, not hashed here, so later fixes do not "
                             "invalidate the evidence; a change to behaviour these tests cover needs a new version.",
                "code_blob_ids_at_freeze": {c: git("rev-parse", f"HEAD:{c}") for c in code},
                "files": files, "data": {}}
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "manifest.json").write_text(json.dumps(manifest, indent=2))
    print("wrote", out_dir / "manifest.json", len(files), "files")


def main_r5inv() -> None:
    out_dir = ROOT / "docs" / "research" / "r5inv"
    if (out_dir / "manifest.json").exists():
        raise SystemExit("docs/research/r5inv/manifest.json exists: a manifest is immutable.")
    inv = json.loads((ROOT / "docs" / "results" / "r5_inventory.json").read_text())
    rels = {"docs/preregistration/R5-detection-delay.md": "R5 protocol with revisions 1 and 2 (committed before the inventory ran)",
            "docs/results/r5_inventory.json": "Metadata-only inventory: every scheduled episode, its eligibility and reasons, the gate, the benign-day plan",
            "ml/r5_inventory.py": "The inventory script (reads schedule and listing files and HTTP HEAD only)"}
    files = {r: {"sha256": sha256(ROOT / r), "role": role, "last_commit": git("log", "-1", "--format=%h", "--", r)} for r, role in rels.items()}
    docs = {f"r5/docs/{q.name}": sha256(q) for q in sorted((ROOT / "data" / "r5" / "docs").glob("*")) if q.name != "tmp.html"}
    manifest = {"version": "r5inv", "created": datetime.now(timezone.utc).isoformat(), "frozen_at_commit": git("rev-parse", "--short", "HEAD"), "git_tag": "inventory-r5",
                "statement": "R5 feasibility inventory. Gate result: " + ("PASSED" if inv["gate"]["measured"]["passed"] else "FAILED") + " ("
                             + str(inv["gate"]["measured"]["episodes"]) + " eligible episodes of " + str(inv["gate"]["required"]["episodes"]) + " required). "
                             "No capture-to-split assignment exists because the protocol computes it only after the gate passes.",
                "gate": inv["gate"], "files": files, "data": {"R5 schedule and listing files (public, UNSW IoT analytics)": docs}}
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "manifest.json").write_text(json.dumps(manifest, indent=2))
    print("wrote", out_dir / "manifest.json")


def main_r5() -> None:
    out_dir = ROOT / "docs" / "research" / "r5"
    if (out_dir / "manifest.json").exists():
        raise SystemExit("docs/research/r5/manifest.json exists: a manifest is immutable.")
    rels = {"docs/preregistration/R5-detection-delay.md": "R5 protocol (revisions 1 and 2 before the inventory)", "docs/results/r5_inventory.json": "Inventory and failed gate",
            "docs/results/r5_validation.json": "R5 validation thresholds (committed before the test days were scored)", "docs/results/r5_test_inputs.json": "Hashes of the test files, recorded before scoring",
            "docs/results/r5_test.json": "R5 false-alarm test (single run, guard data/results/R5_TEST_USED); detection part not run", "ml/r5_eval.py": "R5 code", "ml/r5_inventory.py": "R5 inventory code"}
    files = {r: {"sha256": sha256(ROOT / r), "role": role, "last_commit": git("log", "-1", "--format=%h", "--", r)} for r, role in rels.items()}
    days = ["16-10-12", "16-09-23", "16-09-24", "16-09-25", "16-09-26", "16-09-27", "16-09-28", "16-09-29", "16-09-30", "16-10-01", "16-10-02", "16-10-03", "16-10-04"]
    data = {f"real/unsw/{d}.csv.zip": sha256(ROOT / "data" / "real" / "unsw" / f"{d}.csv.zip") for d in days}
    manifest = {"version": "r5", "created": datetime.now(timezone.utc).isoformat(), "frozen_at_commit": git("rev-parse", "--short", "HEAD"), "git_tag": "fa-r5",
                "statement": "R5 false-alarm results. The feasibility gate failed, so no detection result exists; detection of attacks is unmeasured.",
                "files": files, "data": {"UNSW IoT testbed benign days (validation and test)": data}}
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "manifest.json").write_text(json.dumps(manifest, indent=2))
    print("wrote", out_dir / "manifest.json")


def main_r6audit() -> None:
    out_dir = ROOT / "docs" / "research" / "r6audit"
    if (out_dir / "manifest.json").exists():
        raise SystemExit("docs/research/r6audit/manifest.json exists: a manifest is immutable.")
    aud = json.loads((ROOT / "docs" / "results" / "r6_audit.json").read_text())
    rels = {"docs/preregistration/R6-matched-testbed-detection.md": "R6 protocol (committed before the audit ran)", "docs/results/r6_audit.json": "Stage-1 metadata audit: calendar, eligibility, strata, split, exposure, optimistic gate",
            "docs/results/r5_inventory_erratum.md": "Erratum to the R5 inventory (HEAD helper fault)", "ml/r6_audit.py": "The audit script (schedule, device table, listings and HTTP HEAD only)"}
    files = {r: {"sha256": sha256(ROOT / r), "role": role, "last_commit": git("log", "-1", "--format=%h", "--", r)} for r, role in rels.items()}
    docs = {f"r5/docs/{q.name}": sha256(q) for q in sorted((ROOT / "data" / "r5" / "docs").glob("*")) if q.name in ("annotations.zip", "attackinfo.xlsx", "unsw_attack_data.html", "unsw_iottraces.html")}
    g = aud["gate_stage1"]
    manifest = {"version": "r6audit", "created": datetime.now(timezone.utc).isoformat(), "frozen_at_commit": git("rev-parse", "--short", "HEAD"), "git_tag": "audit-r6",
                "statement": "R6 stage-1 audit. Optimistic gate on the test split: " + ("PASSED" if g["passed"] else "FAILED") + " (" + str(g["measured_test_split"]["episodes"]) + " eligible test episodes of "
                             + str(g["required"]["episodes"]) + " required). Per the protocol, a failed optimistic gate stops the detection part: no capture was downloaded or opened.",
                "gate_stage1": g, "split_rule": aud["split_rule"], "files": files, "data": {"R6 schedule and listing files (public, UNSW IoT analytics)": docs}}
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "manifest.json").write_text(json.dumps(manifest, indent=2))
    print("wrote", out_dir / "manifest.json")


def main_r7audit() -> None:
    out_dir = ROOT / "docs" / "research" / "r7audit"
    if (out_dir / "manifest.json").exists():
        raise SystemExit("docs/research/r7audit/manifest.json exists: a manifest is immutable.")
    aud = json.loads((ROOT / "docs" / "results" / "r7_audit.json").read_text())
    rels = {"docs/preregistration/R6-matched-testbed-detection.md": "R6 protocol (unchanged; R7 builds on it)", "docs/preregistration/R7-separated-baseline-gap.md": "R7 protocol (committed before the audit ran)",
            "docs/results/r7_audit.json": "R7 stage-1 metadata audit: calendar, eligibility, strata, split, exposure, optimistic gate", "ml/r7_audit.py": "The R7 audit script (R6's, three changes)"}
    files = {r: {"sha256": sha256(ROOT / r), "role": role, "last_commit": git("log", "-1", "--format=%h", "--", r)} for r, role in rels.items()}
    docs = {f"r5/docs/{q.name}": sha256(q) for q in sorted((ROOT / "data" / "r5" / "docs").glob("*")) if q.name in ("annotations.zip", "attackinfo.xlsx", "unsw_attack_data.html", "unsw_iottraces.html")}
    g = aud["gate_stage1"]
    manifest = {"version": "r7audit", "created": datetime.now(timezone.utc).isoformat(), "frozen_at_commit": git("rev-parse", "--short", "HEAD"), "git_tag": "audit-r7",
                "statement": "R7 stage-1 audit. Optimistic gate on the test split: " + ("PASSED" if g["passed"] else "FAILED") + " (" + str(g["measured_test_split"]["episodes"]) + " eligible test episodes of "
                             + str(g["required"]["episodes"]) + " required). The split is R6's, unchanged. No capture has been downloaded or opened.",
                "gate_stage1": g, "split_rule": aud["split_rule"], "files": files, "data": {"R6/R7 schedule and listing files (public, UNSW IoT analytics)": docs}}
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "manifest.json").write_text(json.dumps(manifest, indent=2))
    print("wrote", out_dir / "manifest.json")


def main_v(version: str) -> None:
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
