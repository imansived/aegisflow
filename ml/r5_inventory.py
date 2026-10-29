"""R5 metadata-only inventory (protocol: docs/preregistration/R5-detection-delay.md, sections 3 and 4).

Reads ONLY schedule and documentation files:
  data/r5/docs/annotations.zip    only the per-device `<mac>.csv` schedule files (start,end,features,attack-name)
  data/r5/docs/attackinfo.xlsx    device MACs, types, monitoring dates
  data/r5/docs/*.html             the pages' lists of daily capture dates
and makes HTTP HEAD requests (existence and size) for capture files. It never opens a pcap, the flow-counter data or the
`*-packet-anomaly.log` files, so no traffic content (not even a timestamp inside a capture) informs eligibility.
Everything it needs for E1 to E4 is in the schedule, which is why the inventory can be blind.

  python -m ml.r5_inventory
"""

import hashlib
import json
import re
import urllib.request
import zipfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pandas as pd

DOCS = Path("data/r5/docs")
OUT = Path("docs/results/r5_inventory.json")
BASE = "https://iotanalytics.unsw.edu.au/"
PREFIX_MIN_S = 6 * 3600  # E2: clean prefix
MIN_TICKS = 20  # E3
TICK = 5
GATE = {"episodes": 30, "sessions": 8, "types": 2, "per_type_claim": 10}
SYD_DST_START = datetime(2018, 10, 7, 2, 0, tzinfo=timezone(timedelta(hours=10)))  # AEDT begins 2018-10-07


def sha(p: Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()


def syd_offset(t: datetime) -> timedelta:
    return timedelta(hours=11) if t >= SYD_DST_START else timedelta(hours=10)


def head(url: str) -> dict:
    try:
        r = urllib.request.urlopen(urllib.request.Request(url, method="HEAD"), timeout=30)
        return {"status": r.status, "bytes": int(r.headers.get("Content-Length", -1))}
    except Exception as e:  # noqa: BLE001 - reported, not hidden
        return {"status": getattr(e, "code", None) or str(e)[:60], "bytes": None}


def date_lists(html: Path) -> list[list[str]]:
    s = html.read_text(encoding="utf-8", errors="replace")
    return [re.findall(r"'([^']+)'", m.group(2)) for m in re.finditer(r"(\w*[Dd]ates\w*)\s*=\s*\[(.*?)\]", s, re.S)]


def family(name: str) -> tuple[str, str, str]:
    """'ArpSpoof100L2D' -> ('ArpSpoof', '100', 'L2D'): attack tool, packets per second, layer path."""
    m = re.fullmatch(r"(.*?)(\d+)(L2D2L|L2D2W|W2D2W|L2D|W2D|L2L|W2L)", name)
    return (m.group(1), m.group(2), m.group(3)) if m else (name, "", "")


def main() -> None:
    info = pd.read_excel(DOCS / "attackinfo.xlsx", sheet_name="Attacks")
    dev = {r["MAC"].lower().replace(":", ""): {"type": r["Device Type"], "from": pd.Timestamp(r["Start Date"]).date().isoformat(),
                                               "to": pd.Timestamp(r["End Data"]).date().isoformat()} for _, r in info.iterrows()}
    attack_days = set(date_lists(DOCS / "unsw_attack_data.html")[0]) | set(date_lists(DOCS / "unsw_attack_data.html")[1])  # benign + AttackAndBenign lists
    ab_days = set(date_lists(DOCS / "unsw_attack_data.html")[1])  # the AttackAndBenign list
    read = []
    eps = []
    with zipfile.ZipFile(DOCS / "annotations.zip") as z:
        for n in sorted(z.namelist()):
            if not re.fullmatch(r"annotations/[0-9a-f]{12}\.csv", n):
                continue  # *-packet-anomaly.log files are not opened
            read.append(n)
            mac = n.split("/")[1][:12]
            rows = []
            for line in z.read(n).decode("utf-8", "replace").splitlines():
                p = line.strip().split(",")
                if len(p) >= 4 and p[0].isdigit():
                    rows.append((int(p[0]), int(p[1]), p[2], p[3]))
            rows.sort()
            prev_end = None
            for start, end, feats, name in rows:
                tool, rate, layer = family(name)
                t0 = datetime.fromtimestamp(start, timezone.utc)
                dur = end - start
                d_utc = t0.date()
                d_syd = (t0 + syd_offset(t0)).date()
                # a capture file for either convention (UTC date or Sydney date) must be listed as AttackAndBenign
                listed = d_utc.strftime("%y-%m-%d") in ab_days and d_syd.strftime("%y-%m-%d") in ab_days
                monitored = mac in dev and dev[mac]["from"] <= d_utc.isoformat() <= dev[mac]["to"]
                # conservative start of the covered period: the LATER of the two possible day-file starts, never crossing days
                start_utc = datetime(d_utc.year, d_utc.month, d_utc.day, tzinfo=timezone.utc)
                start_syd = datetime(d_syd.year, d_syd.month, d_syd.day, tzinfo=timezone.utc) - syd_offset(t0)
                cover = max(start_utc, start_syd).timestamp()
                clean_from = max(cover, prev_end) if prev_end is not None else cover
                prefix = start - clean_from
                reasons = []
                if not monitored:
                    reasons.append("E4: device/date not in attackinfo monitoring range")
                if not listed:
                    reasons.append("E4: capture file for the onset's UTC or Sydney date is not listed")
                if prefix < PREFIX_MIN_S:
                    reasons.append(f"E2: clean prefix {prefix / 3600:.2f} h < 6 h")
                if dur // TICK < MIN_TICKS:
                    reasons.append("E3: fewer than 20 ticks")
                eps.append({"device": mac, "device_type": dev.get(mac, {}).get("type"), "tool": tool, "pps": rate, "layer": layer, "name": name,
                            "onset_epoch": start, "end_epoch": end, "duration_s": dur, "utc_date": d_utc.isoformat(), "sydney_date": d_syd.isoformat(),
                            "capture_id": "AttackAndBenign/" + d_syd.strftime("%y-%m-%d"), "tier": "A", "clean_prefix_h": round(prefix / 3600, 3),
                            "eligible": not reasons, "reasons": reasons})
                prev_end = max(prev_end or 0, end)
    el = [e for e in eps if e["eligible"]]
    sessions = sorted({(e["device"], e["sydney_date"]) for e in el})
    types = {}
    for e in el:
        types[e["tool"]] = types.get(e["tool"], 0) + 1
    gate = {"episodes": len(el), "sessions": len(sessions), "types": len(types), "types_with_claim_size": sorted(k for k, v in types.items() if v >= GATE["per_type_claim"])}
    gate["passed"] = len(el) >= GATE["episodes"] and len(sessions) >= GATE["sessions"] and len(types) >= GATE["types"]
    # tau rule of the protocol (section 5, D2): half of the episodes need follow-up of at least 1800 s, else tau = 300 s
    follow = [e["duration_s"] for e in el]
    tau = 1800 if follow and sum(f >= 1800 for f in follow) * 2 >= len(follow) else 300

    # benign exposure for the false-alarm claim: the 2016 testbed days that were never opened, ascending, skipping 12, 13, 14
    listed_2016 = sorted(date_lists(DOCS / "unsw_iottraces.html")[0])
    unopened = [d for d in listed_2016 if d not in ("16-10-12", "16-10-13", "16-10-14")]
    val_days, test_days = unopened[:3], unopened[3:12]
    benign = {"listed_days": listed_2016, "validation_benign_days": ["16-10-12"] + val_days, "test_benign_days": test_days,
              "csv_head": {d: head(f"{BASE}iottestbed/csv/{d}.csv.zip") for d in ["16-10-12"] + val_days + test_days}}
    caps = sorted({e["capture_id"].split("/")[1] for e in el})
    cap_head = {c: head(f"{BASE}anomaly-data/pcap/AttackAndBenign/{c}.pcap") for c in caps}

    out = {
        "protocol": "docs/preregistration/R5-detection-delay.md (revision 2)",
        "blindness": {"files_read": ["annotations.zip: " + ", ".join(read), "attackinfo.xlsx", "unsw_attack_data.html", "unsw_iottraces.html"],
                      "not_opened": ["any pcap or csv capture", "flowdata.zip", "annotations/*-packet-anomaly.log"], "network_requests": "page text, schedule files, HTTP HEAD only"},
        "input_hashes": {p.name: sha(p) for p in sorted(DOCS.glob("*")) if p.name != "tmp.html"},
        "source": "UNSW IoT attack traces (Hamza et al., ACM SOSR 2019): scheduled attacks with annotated epoch start and end per device",
        "episodes_total": len(eps), "episodes_eligible": len(el), "tier_counts": {"A": len(eps), "B": 0, "C": 0},
        "ineligible_reasons": {}, "gate": {"required": GATE, "measured": gate}, "tau_s": tau, "tau_margin_s": 0.10 * tau,
        "eligible_by_tool": types, "eligible_by_device_type": {}, "eligible_sessions": [list(s) for s in sessions],
        "capture_files_needed": {"AttackAndBenign": cap_head}, "benign_exposure": benign, "episodes": eps,
    }
    for e in eps:
        for r in e["reasons"]:
            k = r.split(":")[0] + ":" + re.sub(r"[\d.]+ h", "x h", r.split(":", 1)[1])
            out["ineligible_reasons"][k] = out["ineligible_reasons"].get(k, 0) + 1
    for e in el:
        out["eligible_by_device_type"][e["device_type"]] = out["eligible_by_device_type"].get(e["device_type"], 0) + 1
    OUT.write_text(json.dumps(out, indent=1))
    print(json.dumps({k: out[k] for k in ("episodes_total", "episodes_eligible", "ineligible_reasons", "gate", "tau_s", "eligible_by_tool", "eligible_by_device_type")}, indent=1))
    print("benign: validation", benign["validation_benign_days"], "test", benign["test_benign_days"])


if __name__ == "__main__":
    main()
