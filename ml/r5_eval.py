"""R5 false-alarm part (protocol: docs/preregistration/R5-detection-delay.md). The detection part is NOT implemented here:
the feasibility gate failed (docs/results/r5_inventory.json), so by the protocol it is not run.

  python -m ml.r5_eval prep 16-09-23        # score every device of one day with all arms (cached), no decisions
  python -m ml.r5_eval validate             # thresholds per arm on validation benign (16-10-12 + three unopened days)
  python -m ml.r5_eval test                 # the test days, once (guard data/results/R5_TEST_USED)
"""

import hashlib
import json
import sys
from pathlib import Path

import numpy as np

from .r2_eval import ARMS, BUDGET, BURN, arm_score, arrays, benign_exposure, calibrate, false_alarm_report, onsets, paired_difference, rising, unsw_traces

VAL_DAYS = ["16-10-12", "16-09-23", "16-09-24", "16-09-25"]
TEST_DAYS = ["16-09-26", "16-09-27", "16-09-28", "16-09-29", "16-09-30", "16-10-01", "16-10-02", "16-10-03", "16-10-04"]
GUARD = Path("data/results/R5_TEST_USED")
R2_THR = {a: json.loads(Path("docs/results/r2_validation.json").read_text())["arms"][a]["false_alarms"]["threshold"] for a in ARMS}


def day_arrays(day: str) -> list[tuple[str, dict]]:
    return [(t["id"], arrays(t)) for t in unsw_traces(day)]


def stage_prep(day: str) -> None:
    ben = day_arrays(day)
    print(day, len(ben), "devices scored (cached)", flush=True)


def stage_validate() -> None:
    ben = [x for d in VAL_DAYS for x in day_arrays(d)]
    out = {"split": "validation", "days": VAL_DAYS, "benign_traces": len(ben), "arms": {}}
    for arm in ARMS:
        thr = calibrate([arm_score(a, arm) for _, a in ben])
        out["arms"][arm] = {"threshold": thr, "false_alarms": false_alarm_report(ben, arm, thr)}
        f = out["arms"][arm]["false_alarms"]
        print(f"{arm:9s} thr {thr:.3f}  validation {f['rate']:.2f}/device-day [{f['ci95'][0]:.2f},{f['ci95'][1]:.2f}] over {f['benign_device_days']:.1f} dd")
    Path("docs/results/r5_validation.json").write_text(json.dumps(out, indent=1, default=float))


def sha(p: Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()


def stage_test() -> None:
    if GUARD.exists():
        sys.exit("R5 test data already used: the test is run once")
    val = json.loads(Path("docs/results/r5_validation.json").read_text())
    thr = {a: val["arms"][a]["threshold"] for a in ARMS}
    GUARD.parent.mkdir(parents=True, exist_ok=True)
    GUARD.write_text("R5 test started; thresholds from docs/results/r5_validation.json")
    ben = [x for d in TEST_DAYS for x in day_arrays(d)]
    out = {"split": "test", "days": TEST_DAYS, "thresholds": thr, "r2_thresholds_transfer": R2_THR, "benign_traces": len(ben),
           "detection": "not run: the feasibility gate failed (docs/results/r5_inventory.json)", "arms": {}, "transfer_r2_thresholds": {}}
    out["R1-shipped_false_onsets_per_device_day"] = float(sum(rising(a["hot_shipped"][BURN:]).sum() for _, a in ben) / sum(benign_exposure(len(a["hot_shipped"])) for _, a in ben))
    for arm in ARMS:
        out["arms"][arm] = {"false_alarms": false_alarm_report(ben, arm, thr[arm])}
        out["transfer_r2_thresholds"][arm] = false_alarm_report(ben, arm, R2_THR[arm])
        f, g = out["arms"][arm]["false_alarms"], out["transfer_r2_thresholds"][arm]
        print(f"{arm:9s} thr {thr[arm]:.3f} test {f['rate']:.2f}/dd [{f['ci95'][0]:.2f},{f['ci95'][1]:.2f}] over {f['benign_device_days']:.1f} dd | R2 thr {R2_THR[arm]:.3f}: {g['rate']:.2f} [{g['ci95'][0]:.2f},{g['ci95'][1]:.2f}]")
    sc = {arm: [arm_score(a, arm) for _, a in ben] for arm in ARMS}
    out["paired"] = {"A_minus_R1-recal": paired_difference(sc["A"], sc["R1-recal"], thr["A"], thr["R1-recal"]), "B_minus_R1-recal": paired_difference(sc["B"], sc["R1-recal"], thr["B"], thr["R1-recal"])}

    def claim_f(f: dict) -> str:
        if f["benign_device_days"] < 120:
            return "INCONCLUSIVE"
        return "PASS" if f["ci95"][1] <= 3.75 else "INCONCLUSIVE" if f["rate"] <= BUDGET else "FAIL"

    out["claim_F"] = {arm: claim_f(out["arms"][arm]["false_alarms"]) for arm in ARMS}
    out["claim_F_primary_arm_A"] = out["claim_F"]["A"]
    out["claim_B_better_than_recalibration_A"] = ("PASS" if out["claim_F"]["A"] == "PASS" and out["paired"]["A_minus_R1-recal"]["ci95"][1] < 0 else "NOT SHOWN")
    out["claims_D1_D2"] = "not run: the feasibility gate failed"
    print("claim F", out["claim_F"], "paired", out["paired"])
    Path("docs/results/r5_test.json").write_text(json.dumps(out, indent=1, default=float))


if __name__ == "__main__":
    {"prep": lambda: stage_prep(sys.argv[2]), "validate": stage_validate, "test": stage_test}[sys.argv[1]]()
