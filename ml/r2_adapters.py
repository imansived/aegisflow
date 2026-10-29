"""R2 data adapters (protocol: docs/preregistration/R2-sparse-network-detector.md, section 3).

  UNSW IoT-testbed packet metadata (CSV) or pcap/pcapng  ->  one trace per IoT device
  IoT-23 Zeek flow log                                   ->  one trace for the capture's busiest private host (the R1 adapter)

A trace is a dict: id, group, t0, n (ticks of 5 s), sent, recv, conns (int arrays), ends (tick -> set of remote IPs), mal (bool array or None).
Only the network signal is available; callers add explicit nulls for the rest.
"""

import struct
import tarfile
import zipfile
from pathlib import Path

import numpy as np
import pandas as pd

from .replay_iot23 import parse, to_ticks

TICK = 5
# The 23 IoT device MACs of the published List_Of_Devices.txt. Excluded by protocol: the gateway, the phones/iPhone, tablet, laptop and MacBooks.
IOT_MACS = {
    "d0:52:a8:00:67:5e": "SmartThings", "44:65:0d:56:cc:d3": "Amazon Echo", "70:ee:50:18:34:43": "Netatmo Welcome", "f4:f2:6d:93:51:f1": "TP-Link cloud camera",
    "00:16:6c:ab:6b:88": "Samsung SmartCam", "30:8c:fb:2f:e4:b2": "Dropcam", "00:62:6e:51:27:2e": "Insteon camera (wired)", "e8:ab:fa:19:de:4f": "Insteon camera (wireless)",
    "00:24:e4:11:18:a8": "Withings baby monitor", "ec:1a:59:79:f4:89": "Belkin Wemo switch", "50:c7:bf:00:56:39": "TP-Link plug", "74:c6:3b:29:d7:1d": "iHome",
    "ec:1a:59:83:28:11": "Belkin Wemo motion", "18:b4:30:25:be:e4": "NEST Protect", "70:ee:50:03:b8:ac": "Netatmo weather", "00:24:e4:1b:6f:96": "Withings scale",
    "74:6a:89:00:2e:25": "Blipcare BP meter", "00:24:e4:20:28:c6": "Withings Aura", "d0:73:d5:01:83:08": "LiFX bulb", "18:b7:9e:02:20:44": "Triby speaker",
    "e0:76:d0:33:bb:85": "PIX-STAR photo frame", "70:5a:0f:e4:9b:c0": "HP printer", "30:8c:fb:b6:ea:45": "Nest Dropcam",
}
COLS = ("t", "size", "src", "dst", "ipsrc", "ipdst", "proto", "sport", "dport")
ETH_IPV4 = bytes([0x08, 0x00])
PCAPNG_MAGIC = bytes([0x0A, 0x0D, 0x0D, 0x0A])


def mac_int(mac: str) -> int:
    return int(mac.replace(":", ""), 16)


def _ip_int(ip: str) -> int:
    a, b, c, d = (int(x) for x in ip.split("."))
    return (a << 24) | (b << 16) | (c << 8) | d


def ip_str(v: int) -> str:
    return f"{v >> 24 & 255}.{v >> 16 & 255}.{v >> 8 & 255}.{v & 255}"


# ------------------------------------------------------------------------------------ packet sources ----


def read_csv_zip(path: Path) -> dict[str, np.ndarray]:
    """UNSW packet-metadata CSV (zipped) -> packet arrays (IPv4 rows)."""
    with zipfile.ZipFile(path) as z:
        with z.open(z.infolist()[0]) as f:
            df = pd.read_csv(f, usecols=["TIME", "Size", "eth.src", "eth.dst", "IP.src", "IP.dst", "IP.proto", "port.src", "port.dst"],
                             dtype={"IP.proto": "float64", "port.src": "float64", "port.dst": "float64"})
    df = df.dropna(subset=["IP.src", "IP.dst"])
    macs = {m: mac_int(m) for m in pd.unique(pd.concat([df["eth.src"], df["eth.dst"]]))}
    ips = {i: _ip_int(i) for i in pd.unique(pd.concat([df["IP.src"], df["IP.dst"]])) if isinstance(i, str) and i.count(".") == 3}
    df = df[df["IP.src"].isin(ips) & df["IP.dst"].isin(ips)]
    return {"t": df["TIME"].to_numpy(np.int64), "size": df["Size"].to_numpy(np.int64), "src": df["eth.src"].map(macs).to_numpy(np.int64),
            "dst": df["eth.dst"].map(macs).to_numpy(np.int64), "ipsrc": df["IP.src"].map(ips).to_numpy(np.int64), "ipdst": df["IP.dst"].map(ips).to_numpy(np.int64),
            "proto": df["IP.proto"].fillna(0).to_numpy(np.int64), "sport": df["port.src"].fillna(0).to_numpy(np.int64), "dport": df["port.dst"].fillna(0).to_numpy(np.int64)}


def _frame(cols: dict, buf: bytes, orig: int, sec: int) -> bool:
    if len(buf) < 34 or buf[12:14] != ETH_IPV4 or buf[23] not in (6, 17):  # the metadata CSV holds TCP and UDP only
        return False
    ihl = (buf[14] & 15) * 4
    proto = buf[23]
    sport = dport = 0
    if proto in (6, 17) and len(buf) >= 14 + ihl + 4:
        sport, dport = struct.unpack("!HH", buf[14 + ihl:18 + ihl])
    cols["t"].append(sec)
    cols["size"].append(orig)
    cols["dst"].append(int.from_bytes(buf[0:6], "big"))
    cols["src"].append(int.from_bytes(buf[6:12], "big"))
    cols["ipsrc"].append(int.from_bytes(buf[26:30], "big"))
    cols["ipdst"].append(int.from_bytes(buf[30:34], "big"))
    cols["proto"].append(proto)
    cols["sport"].append(sport)
    cols["dport"].append(dport)
    return True


def read_pcap_stream(f, max_packets: int | None = None) -> dict[str, np.ndarray]:
    """libpcap or pcapng stream -> the packet arrays. IPv4 only; Size is the original frame length."""
    cols: dict[str, list] = {k: [] for k in COLS}
    n = 0
    first = f.read(4)
    if first == PCAPNG_MAGIC:
        tsres = [1e-6]  # index 0 unused; one entry per interface block, default microseconds
        pending = first
        while True:
            head = pending + f.read(8 - len(pending)) if pending else f.read(8)
            pending = b""
            if len(head) < 8:
                break
            btype, blen = struct.unpack("<II", head)
            body = f.read(blen - 8)
            if len(body) < blen - 8:
                break
            if btype == 1:  # interface description: if_tsresol is option 9
                res, o = 1e-6, 8
                while o + 4 <= len(body) - 4:
                    code, ln = struct.unpack("<HH", body[o:o + 4])
                    if code == 0:
                        break
                    if code == 9:
                        v = body[o + 4]
                        res = 2.0 ** -(v & 0x7F) if v & 0x80 else 10.0 ** -v
                    o += 4 + ((ln + 3) & ~3)
                tsres.append(res)
            elif btype == 6:  # enhanced packet block
                iface, hi, lo, cap, orig = struct.unpack("<IIIII", body[:20])
                res = tsres[iface + 1] if iface + 1 < len(tsres) else 1e-6
                if _frame(cols, body[20:20 + cap], orig, int(((hi << 32) | lo) * res)):
                    n += 1
                    if max_packets and n >= max_packets:
                        break
    else:  # classic libpcap
        head = first + f.read(20)
        little = head[:4] in (bytes([0xD4, 0xC3, 0xB2, 0xA1]), bytes([0x4D, 0x3C, 0xB2, 0xA1]))
        hdr = struct.Struct(("<" if little else ">") + "IIII")
        while True:
            h = f.read(16)
            if len(h) < 16:
                break
            sec, _frac, incl, orig = hdr.unpack(h)
            buf = f.read(incl)
            if len(buf) < incl:
                break
            if _frame(cols, buf, orig, sec):
                n += 1
                if max_packets and n >= max_packets:
                    break
    return {k: np.array(v, np.int64) for k, v in cols.items()}


class _Tolerant:
    """A truncated .gz raises EOFError mid-stream; treat that as end of file so the packets read so far are kept."""

    def __init__(self, f):
        self.f = f

    def read(self, n):
        try:
            return self.f.read(n)
        except (EOFError, OSError, tarfile.ReadError):
            return b""


def read_pcap_targz(path: Path, max_packets: int | None = None) -> dict[str, np.ndarray]:
    """First pcap inside a .tar.gz (also works on a truncated download: the packets read so far are used)."""
    tf = tarfile.open(path, "r|gz")
    try:
        for member in tf:
            if member.isfile():
                return read_pcap_stream(_Tolerant(tf.extractfile(member)), max_packets)
    except (EOFError, tarfile.ReadError, OSError):
        pass
    raise RuntimeError("no pcap found")


# ------------------------------------------------------------------------------------ device traces ----


def device_traces(pk: dict[str, np.ndarray], day: str, t0: int | None = None, t1: int | None = None) -> list[dict]:
    t0 = int(pk["t"].min()) if t0 is None else t0
    t1 = int(pk["t"].max()) if t1 is None else t1
    n = int((t1 - t0) // TICK) + 1
    k_all = (pk["t"] - t0) // TICK
    out = []
    for mac, name in IOT_MACS.items():
        m = mac_int(mac)
        o, i = pk["src"] == m, pk["dst"] == m
        if o.sum() + i.sum() == 0:
            continue
        sent = np.bincount(k_all[o], pk["size"][o], n).astype(np.int64)
        recv = np.bincount(k_all[i], pk["size"][i], n).astype(np.int64)
        key = (pk["ipdst"][o] << 24) | (pk["dport"][o] << 8) | pk["proto"][o]
        pairs = pd.DataFrame({"k": k_all[o], "key": key}).drop_duplicates()
        conns = np.bincount(pairs["k"].to_numpy(np.int64), minlength=n).astype(np.int64)
        ends: dict[int, set] = {}
        rem = pd.DataFrame({"k": np.r_[k_all[o], k_all[i]], "ip": np.r_[pk["ipdst"][o], pk["ipsrc"][i]]}).drop_duplicates()
        for kk, ip in zip(rem["k"].to_numpy(), rem["ip"].to_numpy()):
            ends.setdefault(int(kk), set()).add(ip_str(int(ip)))
        out.append({"id": f"{day}/{name}", "group": "unsw", "host": mac, "t0": float(t0), "n": n, "sent": sent, "recv": recv, "conns": conns, "ends": ends, "mal": None})
    return out


def iot23_trace(path: Path) -> dict:
    t = to_ticks(parse(path))
    t.update({"id": path.name.replace(".conn.log.labeled", ""), "group": "iot23"})
    return t
