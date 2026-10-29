"""R7 stage-1 audit: metadata only (protocol: docs/preregistration/R7-separated-baseline-gap.md, on top of R6). Differs from ml/r6_audit.py in three places only:
the 60-minute gap (CLEAN_PRE_S), the strata S0/S1/S2, and the output path.

Reads only: data/r5/docs/annotations.zip (the per-device `<mac>.csv` schedules), attackinfo.xlsx, the two documentation pages (date lists),
and HTTP HEAD for file size (browser User-Agent). It opens no capture, no flow data and no `*-packet-anomaly.log`, and reads no timestamp
from inside any capture. Everything below is derived from the schedule and the listings, so the audit is blind to traffic.

  python -m ml.r7_audit
"""

import hashlib
import json
import re
import urllib.request
import zipfile
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import pandas as pd

DOCS = Path("data/r5/docs")
OUT = Path("docs/results/r7_audit.json")
BASE = "https://iotanalytics.unsw.edu.au/anomaly-data/pcap/"
SYD_DST = datetime(2018, 10, 7, 2, 0, tzinfo=timezone(timedelta(hours=10)))
CLEAN_PRE_S, CHAIN_MIN_S, MIN_TICKS, TICK = 3600, 24 * 3600, 20, 5
GUARD_BEFORE_S, GUARD_AFTER_S = 60, 30 * 60
BURN_S = 2 * 3600
GATE = {"episodes": 30, "sessions": 8, "tools": 2, "stratum_claim": 10}
MIN_TEST_DD = 100


def sha(p: Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()


def head(url: str) -> int | None:
    try:
        r = urllib.request.urlopen(urllib.request.Request(url, method="HEAD", headers={"User-Agent": "Mozilla/5.0"}), timeout=40)
        return int(r.headers.get("Content-Length", -1))
    except Exception:  # noqa: BLE001 - a missing size is reported as null
        return None


def lists(html: Path) -> list[list[str]]:
    s = html.read_text(encoding="utf-8", errors="replace")
    return [re.findall(r"'([^']+)'", m.group(2)) for m in re.finditer(r"(\w*[Dd]ates\w*)\s*=\s*\[(.*?)\]", s, re.S)]


def split_of(capture_id: str) -> str:
    v = int.from_bytes(hashlib.sha256(capture_id.encode()).digest()[:8], "big") % 3
    return "validation" if v == 0 else "test"


def syd(t: datetime) -> date:
    return (t + (timedelta(hours=11) if t >= SYD_DST else timedelta(hours=10))).date()


def d8(s: str) -> date:
    return datetime.strptime("20" + s, "%Y-%m-%d").date()


def main() -> None:
    info = pd.read_excel(DOCS / "attackinfo.xlsx", sheet_name="Attacks")
    dev = {r["MAC"].lower().replace(":", ""): {"type": r["Device Type"], "from": pd.Timestamp(r["Start Date"]).date(), "to": pd.Timestamp(r["End Data"]).date()} for _, r in info.iterrows()}
    both = lists(DOCS / "unsw_attack_data.html")
    benign_days, ab_days = sorted(both[0]), sorted(both[1])
    kind = {d8(d): "benign" for d in benign_days} | {d8(d): "attack" for d in ab_days}
    sizes = {f"{k}/{d}": head(f"{BASE}{'benign' if k == 'benign' else 'AttackAndBenign'}/{d}.pcap") for k, ds in (("benign", benign_days), ("attack", ab_days)) for d in ds}

    # per-device calendar: dates in the monitoring range that have a file, and the first date of the unbroken run of dates ending at each date
    cal, chain_start = {}, {}
    for mac, r in dev.items():
        days, d, start = [], r["from"], None
        while d <= r["to"]:
            if d in kind:
                start = start or d
                days.append(d)
            else:
                start = None
            chain_start[(mac, d)] = start
            d += timedelta(days=1)
        cal[mac] = {"type": r["type"], "range": [r["from"].isoformat(), r["to"].isoformat()], "dates_with_file": [x.isoformat() for x in days],
                    "date_gaps_in_range": [x.isoformat() for x in pd.date_range(r["from"], r["to"]).date if x not in kind]}

    eps, windows = [], {}
    with zipfile.ZipFile(DOCS / "annotations.zip") as z:
        read = []
        for n in sorted(z.namelist()):
            if not re.fullmatch(r"annotations/[0-9a-f]{12}\.csv", n):
                continue
            read.append(n)
            mac = n.split("/")[1][:12]
            rows = sorted((int(p[0]), int(p[1]), p[3]) for p in (ln.split(",") for ln in z.read(n).decode("utf-8", "replace").splitlines()) if len(p) >= 4 and p[0].isdigit())
            windows[mac] = [(s, e) for s, e, _ in rows]
            prev_end = None
            for s, e, name in rows:
                t0 = datetime.fromtimestamp(s, timezone.utc)
                du, ds = t0.date(), syd(t0)
                tool = re.sub(r"\d+(L2D2L|L2D2W|W2D2W|L2D|W2D|L2L|W2L)$", "", name)
                monitored = mac in dev and dev[mac]["from"] <= du <= dev[mac]["to"] and dev[mac]["from"] <= ds <= dev[mac]["to"]
                has_files = du in kind and ds in kind
                # optimistic chain length: the unbroken run of file dates, conservatively starting 11 h after its first midnight (UTC or Sydney convention)
                cs = chain_start.get((mac, ds))
                chain_s = (s - (datetime(cs.year, cs.month, cs.day, tzinfo=timezone.utc).timestamp() + 11 * 3600)) if cs else None
                since_prev = (s - prev_end) if prev_end is not None else None
                reasons = []
                if not monitored:
                    reasons.append("E4 monitoring range")
                if not has_files:
                    reasons.append("E4 file for onset date not listed")
                if chain_s is None or chain_s < CHAIN_MIN_S:
                    reasons.append("E2a chain shorter than 24 h")
                if since_prev is not None and since_prev < CLEAN_PRE_S:
                    reasons.append("E2b attack within 60 min (campaign continuation)")
                if (e - s) // TICK < MIN_TICKS:
                    reasons.append("E3 short")
                stratum = "S1" if since_prev is None or since_prev >= 24 * 3600 else "S2" if since_prev >= 6 * 3600 else "S0"
                cid = "R6/" + ds.strftime("%y-%m-%d")
                eps.append({"device": mac, "tool": tool, "name": name, "onset_epoch": s, "end_epoch": e, "sydney_date": ds.isoformat(), "capture_id": cid, "split": split_of(cid),
                            "stratum": stratum, "hours_since_previous_attack": None if since_prev is None else round(since_prev / 3600, 2),
                            "chain_hours_optimistic": None if chain_s is None else round(chain_s / 3600, 1), "eligible_stage1": not reasons, "reasons": reasons})
                prev_end = max(prev_end or 0, e)

    # benign exposure per split, device-days: F1 = benign-list days; F2 = attack-day time outside [onset - 60 s, end + 30 min] windows; first 2 h of a chain are burn-in
    exposure = {"validation": {"F1": 0.0, "F2": 0.0}, "test": {"F1": 0.0, "F2": 0.0}}
    per_day = {}
    for mac, r in dev.items():
        for d in cal[mac]["dates_with_file"]:
            d = date.fromisoformat(d)
            cid = "R6/" + d.strftime("%y-%m-%d")
            sp = split_of(cid)
            day0 = datetime(d.year, d.month, d.day, tzinfo=timezone.utc).timestamp() - 11 * 3600  # Sydney midnight (AEDT, wider than the true offset; conservative boundaries are not needed here)
            lo, hi = day0, day0 + 86400
            burn = BURN_S if chain_start[(mac, d)] == d else 0
            lo += burn
            if kind[d] == "benign":
                dd = max(0.0, hi - lo) / 86400
                exposure[sp]["F1"] += dd
            else:
                bad = sorted((max(lo, s - GUARD_BEFORE_S), min(hi, e + GUARD_AFTER_S)) for s, e in windows.get(mac, []) if e + GUARD_AFTER_S > lo and s - GUARD_BEFORE_S < hi)
                covered, cur_end = 0.0, lo
                for a, b in bad:
                    a = max(a, cur_end)
                    if b > a:
                        covered += b - a
                        cur_end = b
                dd = max(0.0, (hi - lo) - covered) / 86400
                exposure[sp]["F2"] += dd
            per_day[f"{mac}/{d.isoformat()}"] = {"split": sp, "kind": kind[d], "device_days": round(dd, 3)}

    def tally(ep):
        return {"episodes": len(ep), "sessions": len({(e["device"], e["sydney_date"]) for e in ep}), "tools": len({e["tool"] for e in ep}),
                "by_stratum": {s: sum(1 for e in ep if e["stratum"] == s) for s in ("S0", "S1", "S2")}, "by_tool": {t: sum(1 for e in ep if e["tool"] == t) for t in sorted({e["tool"] for e in ep})},
                "devices": len({e["device"] for e in ep})}

    el = [e for e in eps if e["eligible_stage1"]]
    test_el, val_el = [e for e in el if e["split"] == "test"], [e for e in el if e["split"] == "validation"]
    g = tally(test_el)
    gate1 = {"required": GATE, "measured_test_split": g, "passed": g["episodes"] >= GATE["episodes"] and g["sessions"] >= GATE["sessions"] and g["tools"] >= GATE["tools"]}
    ex_test = exposure["test"]["F1"] + exposure["test"]["F2"]
    out = {
        "protocol": "docs/preregistration/R7-separated-baseline-gap.md (on R6)",
        "blindness": {"read": ["annotations.zip: " + ", ".join(read), "attackinfo.xlsx", "unsw_attack_data.html"], "not_opened": ["any capture", "flowdata.zip", "annotations/*.log"], "network": "HTTP HEAD (size) only"},
        "input_hashes": {p.name: sha(p) for p in sorted(DOCS.glob("*")) if p.name in ("annotations.zip", "attackinfo.xlsx", "unsw_attack_data.html", "unsw_iottraces.html")},
        "devices": cal, "file_sizes_bytes": sizes, "total_bytes": sum(v for v in sizes.values() if v),
        "split_rule": "sha256('R6/<yy-mm-dd>') first 8 bytes big-endian mod 3: 0 validation, 1-2 test",
        "split_by_date": {d.strftime("%y-%m-%d"): {"split": split_of("R6/" + d.strftime("%y-%m-%d")), "list": kind[d]} for d in sorted(kind)},
        "benign_exposure_device_days": {k: {kk: round(vv, 2) for kk, vv in v.items()} for k, v in exposure.items()},
        "test_exposure_total": round(ex_test, 2), "test_exposure_meets_100": ex_test >= MIN_TEST_DD,
        "episodes_total": len(eps), "episodes_eligible_stage1": len(el), "eligible_by_split": {"validation": tally(val_el), "test": tally(test_el)},
        "ineligible_reasons": {r: sum(1 for e in eps if r in e["reasons"]) for r in sorted({r for e in eps for r in e["reasons"]})},
        "gate_stage1": gate1, "episodes": eps, "per_device_day": per_day,
        "unverifiable_from_metadata": ["no recording gap at day-file boundaries", "no device reset or reconfiguration", "benign-list and attack-list days recorded under the same conditions",
                                       "benign-list days free of unlabelled attacks", "file contents match their labelled dates"],
    }
    OUT.write_text(json.dumps(out, indent=1))
    print(json.dumps({k: out[k] for k in ("total_bytes", "benign_exposure_device_days", "test_exposure_total", "episodes_total", "episodes_eligible_stage1", "eligible_by_split", "ineligible_reasons", "gate_stage1")}, indent=1)[:4000])
    print({d: v["split"] for d, v in out["split_by_date"].items()})


if __name__ == "__main__":
    main()
