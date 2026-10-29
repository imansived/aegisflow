"""R6/R7 stage 2: continuity from packet timestamps and Ethernet addresses ONLY (protocol R6 section 3, R7).

Reads, per packet: the capture timestamp and the 12 bytes of Ethernet destination and source address. Nothing else: no IP, no ports, no payload,
no scores, no attack windows. The rules are those written in the protocol before this code existed:
  * chain break between consecutive day files if the gap between the last packet of one and the first of the next exceeds 5 minutes, and also
    wherever the whole capture is silent for more than 5 minutes inside a file;
  * a device silent for more than 2 hours inside the 24 hours before an onset: that episode is excluded;
  * a device's MAC absent from a file: its stream is excluded for those dates (an episode whose 24 h before the onset touch such a file is excluded);
  * E2'(a): at least 24 hours of verified chain end at the onset (the first 2 hours of a chain are burn-in and do not count towards the 24).
Episodes can only be removed here; nothing is added. The gate is re-applied to the test split.

  python -m ml.r7_stage2 fetch            # download the day files inside the devices' monitoring ranges (resumable)
  python -m ml.r7_stage2 scan <file.pcap> # one file -> data/r7/stage2/<name>.json
  python -m ml.r7_stage2 gate             # all scans + the stage-1 audit -> docs/results/r7_stage2.json
"""

import json
from concurrent.futures import ThreadPoolExecutor
import struct
import subprocess
import sys
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import pandas as pd

ROOT = Path("data/r7")
SCAN = ROOT / "stage2"
CAP = ROOT / "pcap"
AUDIT = Path("docs/results/r7_audit.json")
OUT = Path("docs/results/r7_stage2.json")
BASE = "https://iotanalytics.unsw.edu.au/anomaly-data/pcap/"
BREAK_S, DEVICE_GAP_S, CHAIN_MIN_S, BURN_S, REPORT_GAP_S = 5 * 60, 2 * 3600, 24 * 3600, 2 * 3600, 30 * 60
GATE = {"episodes": 30, "sessions": 8, "tools": 2}


def macs() -> dict[int, str]:
    info = pd.read_excel("data/r5/docs/attackinfo.xlsx", sheet_name="Attacks")
    return {int(m.lower().replace(":", ""), 16): m.lower().replace(":", "") for m in info["MAC"]}


def name_of(kind: str, day: str) -> str:
    return f"{kind}_{day}"


def fetch() -> None:
    aud = json.loads(AUDIT.read_text())
    need = {d for r in aud["devices"].values() for d in r["dates_with_file"]}
    CAP.mkdir(parents=True, exist_ok=True)
    jobs = []
    for key, size in aud["file_sizes_bytes"].items():
        kind, day = key.split("/")
        if "20" + day in need and size:
            jobs.append((kind, day, size))
    def one(job):
        kind, day, size = job
        dst = CAP / (name_of(kind, day) + ".pcap")
        if dst.exists() and dst.stat().st_size == size:
            return
        folder = "benign" if kind == "benign" else "AttackAndBenign"
        subprocess.run(["curl", "-sS", "-L", "-C", "-", "-A", "Mozilla/5.0", "-o", str(dst), f"{BASE}{folder}/{day}.pcap"], check=False)

    with ThreadPoolExecutor(4) as ex:
        list(ex.map(one, jobs))
    bad = [(k, d) for k, d, s in jobs if not (CAP / (name_of(k, d) + ".pcap")).exists() or (CAP / (name_of(k, d) + ".pcap")).stat().st_size != s]
    print("fetched", len(jobs), "files; incomplete:", bad)


def scan(path: Path, limit: int | None = None) -> dict:
    """pcapng (and classic pcap) -> timestamps and Ethernet addresses only."""
    dev = macs()
    last: dict[int, float] = {}
    first: dict[int, float] = {}
    count: dict[int, int] = {m: 0 for m in dev}
    gaps: dict[int, list] = {m: [] for m in dev}
    cap_gaps, n, t_first, t_last, prev = [], 0, None, None, None
    tsres = [1e-6]
    f = open(path, "rb")
    head = f.read(4)
    pcapng = head == bytes([0x0A, 0x0D, 0x0D, 0x0A])
    if not pcapng:
        raise SystemExit("classic pcap not expected in this collection; stage 2 stops rather than guess")
    buf, pos = head + f.read(1 << 26), 0
    done = False
    while not done:
        while len(buf) - pos < 12:
            more = f.read(1 << 26)
            if not more:
                done = True
                break
            buf = buf[pos:] + more
            pos = 0
        if done:
            break
        btype, blen = struct.unpack_from("<II", buf, pos)
        while len(buf) - pos < blen:
            more = f.read(1 << 26)
            if not more:
                done = True
                break
            buf = buf[pos:] + more
            pos = 0
        if done:
            break
        if btype == 1:  # interface description: only the timestamp resolution option is read
            res, o = 1e-6, pos + 16
            end = pos + blen - 4
            while o + 4 <= end:
                code, ln = struct.unpack_from("<HH", buf, o)
                if code == 0:
                    break
                if code == 9:
                    v = buf[o + 4]
                    res = 2.0 ** -(v & 0x7F) if v & 0x80 else 10.0 ** -v
                o += 4 + ((ln + 3) & ~3)
            tsres.append(res)
        elif btype == 6:  # enhanced packet block
            iface, hi, lo, cap, _orig = struct.unpack_from("<IIIII", buf, pos + 8)
            res = tsres[iface + 1] if iface + 1 < len(tsres) else 1e-6
            ts = ((hi << 32) | lo) * res
            if t_first is None:
                t_first = ts
            if prev is not None and ts - prev > BREAK_S:
                cap_gaps.append([prev, ts])
            if prev is None or ts > prev:
                prev = ts
            t_last = prev
            if cap >= 12:
                dst = int.from_bytes(buf[pos + 28:pos + 34], "big")
                src = int.from_bytes(buf[pos + 34:pos + 40], "big")
                for m in (dst, src):
                    if m in dev:
                        l = last.get(m)
                        if l is None:
                            first[m] = ts
                        elif ts - l > REPORT_GAP_S:
                            gaps[m].append([l, ts])
                        if l is None or ts > l:
                            last[m] = ts
                        count[m] += 1
            n += 1
            if limit and n >= limit:
                break
        pos += blen
    return {"file": path.name, "packets": n, "first": t_first, "last": t_last, "capture_gaps_over_5min": cap_gaps,
            "devices": {dev[m]: {"packets": count[m], "first": first.get(m), "last": last.get(m), "gaps_over_30min": gaps[m]} for m in dev}}


def episode_rows(aud: dict, scans: dict[str, dict]) -> tuple[list[dict], dict]:
    """Re-judge every stage-1 eligible episode against what the timestamps show."""
    # per-device chains: file intervals joined when the boundary gap is at most 5 minutes; whole-capture silences over 5 minutes split a file
    segs = []
    for name, s in sorted(scans.items(), key=lambda kv: kv[1]["first"] or 0):
        a = s["first"]
        for g0, g1 in s["capture_gaps_over_5min"]:
            segs.append([a, g0, name])
            a = g1
        segs.append([a, s["last"], name])
    chains = []
    for a, b, name in segs:
        if chains and a - chains[-1]["end"] <= BREAK_S:
            chains[-1]["end"] = max(chains[-1]["end"], b)
            chains[-1]["files"].add(name)
        else:
            chains.append({"start": a, "end": b, "files": {name}})
    out = []
    for e in aud["episodes"]:
        if not e["eligible_stage1"]:
            continue
        mac, onset = e["device"], e["onset_epoch"]
        reasons = []
        ch = next((c for c in chains if c["start"] <= onset <= c["end"]), None)
        if ch is None:
            reasons.append("onset not inside any verified chain (capture gap or no file)")
            verified_h = None
        else:
            verified_h = (onset - ch["start"]) / 3600
            if onset - ch["start"] - BURN_S < CHAIN_MIN_S:
                reasons.append(f"E2a verified chain {verified_h:.1f} h (24 h plus 2 h burn-in required)")
            lo = max(onset - CHAIN_MIN_S, ch["start"])
            for name in ch["files"]:
                d = scans[name]["devices"].get(mac)
                if d is None or d["packets"] == 0:
                    if scans[name]["first"] is not None and scans[name]["last"] >= lo and scans[name]["first"] <= onset:
                        reasons.append(f"device absent from {name}")
                        break
            # device silence over 2 h in the 24 h before the onset, across files
            events = []
            for name in ch["files"]:
                d = scans[name]["devices"].get(mac)
                if d and d["first"] is not None:
                    events.append((d["first"], d["last"], d["gaps_over_30min"]))
            events.sort()
            prev_last = None
            for f0, l0, gp in events:
                if prev_last is not None and f0 - prev_last > DEVICE_GAP_S and f0 > lo and prev_last < onset:
                    reasons.append("device silent over 2 h at a file boundary in the 24 h before onset")
                    break
                for g0, g1 in gp:
                    if g1 - g0 > DEVICE_GAP_S and g1 > lo and g0 < onset:
                        reasons.append("device silent over 2 h in the 24 h before onset")
                        break
                prev_last = l0 if prev_last is None else max(prev_last, l0)
            if events and prev_last is not None and onset - prev_last > DEVICE_GAP_S:
                reasons.append("device silent over 2 h immediately before onset")
        out.append({**{k: e[k] for k in ("device", "tool", "name", "onset_epoch", "capture_id", "split", "stratum", "sydney_date")}, "verified_chain_hours": None if verified_h is None else round(verified_h, 1),
                    "eligible_stage2": not reasons, "reasons": reasons})
    return out, {"chains": [{"start": c["start"], "end": c["end"], "hours": round((c["end"] - c["start"]) / 3600, 1), "files": sorted(c["files"])} for c in chains]}


def tally(ep: list[dict]) -> dict:
    return {"episodes": len(ep), "sessions": len({(e["device"], e["sydney_date"]) for e in ep}), "tools": len({e["tool"] for e in ep}), "devices": len({e["device"] for e in ep}),
            "by_stratum": {s: sum(1 for e in ep if e["stratum"] == s) for s in ("S0", "S1", "S2")}, "by_tool": {t: sum(1 for e in ep if e["tool"] == t) for t in sorted({e["tool"] for e in ep})}}


def gate() -> None:
    aud = json.loads(AUDIT.read_text())
    scans = {p.stem: json.loads(p.read_text()) for p in sorted(SCAN.glob("*.json"))}
    rows, chains = episode_rows(aud, scans)
    keep = [r for r in rows if r["eligible_stage2"]]
    test = [r for r in keep if r["split"] == "test"]
    g = tally(test)
    passed = g["episodes"] >= GATE["episodes"] and g["sessions"] >= GATE["sessions"] and g["tools"] >= GATE["tools"]
    reasons: dict[str, int] = {}
    for r in rows:
        for x in r["reasons"]:
            k = x.split(" ")[0] + " " + " ".join(x.split(" ")[1:4])
            reasons[k] = reasons.get(k, 0) + 1
    files = {}
    for name, s in scans.items():
        day = name.split("_")[1]
        files[name] = {"packets": s["packets"], "first_utc": datetime.fromtimestamp(s["first"], timezone.utc).isoformat() if s["first"] else None,
                       "last_utc": datetime.fromtimestamp(s["last"], timezone.utc).isoformat() if s["last"] else None, "capture_gaps_over_5min": len(s["capture_gaps_over_5min"]),
                       "devices_present": sum(1 for d in s["devices"].values() if d["packets"]), "label": day}
    out = {"protocol": "docs/preregistration/R6 + R7", "stage": 2, "reads": "capture timestamps and Ethernet addresses only", "files": files, **chains,
           "stage1_eligible": len(rows), "stage2_eligible": len(keep), "removed": len(rows) - len(keep), "removal_reasons": reasons,
           "eligible_by_split": {"validation": tally([r for r in keep if r["split"] == "validation"]), "test": g},
           "gate_stage2": {"required": GATE, "measured_test_split": g, "passed": passed}, "episodes": rows}
    OUT.write_text(json.dumps(out, indent=1))
    print(json.dumps({k: out[k] for k in ("stage1_eligible", "stage2_eligible", "removed", "removal_reasons", "gate_stage2")}, indent=1))


if __name__ == "__main__":
    cmd = sys.argv[1]
    if cmd == "fetch":
        fetch()
    elif cmd == "scan":
        SCAN.mkdir(parents=True, exist_ok=True)
        p = Path(sys.argv[2])
        lim = int(sys.argv[3]) if len(sys.argv) > 3 else None
        res = scan(p, lim)
        if lim:
            print(json.dumps({k: res[k] for k in ("packets", "first", "last")}), "devices with packets:", sum(1 for d in res["devices"].values() if d["packets"]))
        else:
            (SCAN / (p.stem + ".json")).write_text(json.dumps(res))
            print(p.stem, res["packets"], "packets")
    elif cmd == "gate":
        gate()
