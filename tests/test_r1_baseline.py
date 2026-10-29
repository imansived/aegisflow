"""R1 is the baseline R2 is judged against. Its headline numbers must stay as published (the files are also hash-frozen by the manifest)."""

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def test_r1_headline_is_unchanged():
    r = json.loads((ROOT / "docs" / "results" / "r1_replay.json").read_text())
    rich = {"CTU-Honeypot-Capture-4-1", "CTU-Honeypot-Capture-5-1", "CTU-IoT-Malware-Capture-42-1", "CTU-IoT-Malware-Capture-20-1", "CTU-IoT-Malware-Capture-21-1"}
    cs = [c for c in r["development_A"] if c["capture"] in rich]
    onsets = sum(c["false_onsets_per_device_day_shipped"]["count"] for c in cs)
    dd = sum(c["judged_benign_device_days"] for c in cs)
    assert onsets == 91 and round(dd, 2) == 2.88 and round(onsets / dd, 1) == 31.6
