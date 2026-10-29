"""R3 (declared signal sets) and R4 (partial-observation wording): docs/preregistration/R3-R4-observation-semantics.md."""

import json
import random
from datetime import datetime, timedelta, timezone

import pytest

from backend.api.runtime import build_snapshot
from backend.correlation.engine import FROZEN_CORRELATION, CorrelationEngine
from backend.correlation.explain import explain, observation
from backend.correlation.fusion import NoisyOr
from backend.correlation.thresholds import FULL, Operating, ThresholdTable
from backend.database.store import EventStore
from backend.detection.config import DetectionConfig
from backend.detection.engine import DetectionEngine
from backend.services.alerts import AlertConfig, AlertManager
from contracts.scenarios import Modality
from contracts.telemetry import SIGNAL_FIELDS, Telemetry

T0 = datetime(2026, 10, 5, 12, 0, tzinfo=timezone.utc)
CFG = DetectionConfig(min_samples=20, occ_min_samples=20, endpoint_learn_s=60)
NET_ONLY = {"D-001": frozenset({"network"})}
NET = 2  # availability pattern: only the network signal observed


def tele(i=0, lost=(), spike=False, **over) -> Telemetry:
    rng = random.Random(i)
    f = dict(
        device_id="D-001", timestamp=T0 + timedelta(seconds=5 * i), latitude=18.5206, longitude=73.8569, battery=80.0, cpu_usage=20.0,
        memory_usage=30.0, temperature=40.0, uptime_s=100 + i, network_connections=5 + rng.randint(0, 2),
        bytes_sent=1000 + rng.randint(0, 200) + (10_000_000 if spike else 0), bytes_received=2000 + rng.randint(0, 300),
        remote_endpoints=["10.0.0.1"] + (["203.0.113.9", "203.0.113.10"] if spike else []), nearby_devices=[],
    )
    f.update(over)
    for g in lost:
        for name in SIGNAL_FIELDS[g]:
            f[name] = None
    return Telemetry(**f)


def table() -> ThresholdTable:
    return ThresholdTable({FULL: Operating(0.9, 0.8), NET: Operating(0.5, 0.4), 13: Operating(0.7, 0.6), 5: Operating(0.6, 0.5)})


class Rig:
    """Detection + correlation + alerts for one device, with an optional declaration."""

    def __init__(self, declared=None, cfg=CFG):
        self.eng = DetectionEngine(["D-001"], cfg, declared=declared)
        self.ce = CorrelationEngine(NoisyOr(), FROZEN_CORRELATION, table())
        self.am = AlertManager(AlertConfig(raise_at=0.5, clear_at=0.4, clear_after_s=30), FROZEN_CORRELATION)
        self.events, self.prev = [], None

    def feed(self, t: Telemetry):
        f = self.ce.update(self.eng.process(t))
        self.events += self.am.update(f)
        self.snap = self.prev = build_snapshot(t, f, self.prev)
        return f


NETONLY_LOST = ("spatial", "device", "proximity")


# ---- R3 -----------------------------------------------------------------------------------------------


def test_R3a_a_network_only_device_with_a_healthy_network_signal_is_not_degraded():
    rig = Rig(NET_ONLY)
    for i in range(40):
        f = rig.feed(tele(i, lost=NETONLY_LOST))
    assert f.observability == "full" and f.alertable and f.pattern == NET
    assert f.unsupported == {Modality.SPATIAL, Modality.DEVICE, Modality.PROXIMITY} and not f.missing and not f.recovering
    s = rig.snap
    assert s["state"] == "normal" and s["observability"] == "full"
    assert s["unsupported"] == ["device", "proximity", "spatial"] and s["missing"] == [] and s["recovering"] == []


def test_R3b_a_network_only_device_that_loses_its_network_goes_degraded_then_recovers():
    rig = Rig(NET_ONLY)
    seen = {}
    for i in range(80):
        f = rig.feed(tele(i, lost=NETONLY_LOST + (("network",) if i == 40 else ())))
        seen[i] = (f.observability, sorted(m.value for m in f.missing), sorted(m.value for m in f.recovering), f.alertable, rig.snap["state"])
    assert seen[20] == ("full", [], [], True, "normal")
    assert seen[40] == ("blind", ["network"], [], False, "degraded")  # the only declared signal is gone
    assert seen[45][:3] == ("blind", [], ["network"]) and seen[45][3] is False  # back, but judgment is still withheld
    assert seen[70] == ("full", [], [], True, "normal")


def test_R3c_a_signal_a_full_device_loses_is_missing_as_before():
    rig = Rig()
    for i in range(10):
        f = rig.feed(tele(i, lost=("spatial",)))
    assert f.observability == "degraded" and f.missing == {Modality.SPATIAL} and not f.unsupported and rig.snap["state"] == "degraded"


def test_R3d_declaring_a_network_only_device_changes_labels_not_numbers():
    declared, plain = Rig(NET_ONLY), Rig()
    for i in range(120):
        t = tele(i, lost=NETONLY_LOST, spike=100 <= i < 108)
        a, b = declared.feed(t), plain.feed(t)
        assert (a.score, a.severity, a.pattern, a.alertable, a.effective, a.learning) == (b.score, b.severity, b.pattern, b.alertable, b.effective, b.learning)
        assert a.unavailable == b.unavailable
    strip = lambda evs: [{k: v for k, v in e.items() if k not in ("unsupported", "missing", "recovering", "observation", "explanation")} for e in evs]
    assert declared.events and strip(declared.events) == strip(plain.events)  # an alert really opened, and is the same alert


def test_R3e_a_value_sent_for_an_unsupported_signal_is_ignored_and_counted():
    nulls, sends = Rig(NET_ONLY), Rig(NET_ONLY)
    for i in range(60):
        a = nulls.feed(tele(i, lost=NETONLY_LOST))
        b = sends.feed(tele(i, lost=("device", "proximity"), latitude=18.5 + (i % 7) * 0.01))  # location sent although undeclared
        assert a.score == b.score and a.effective == b.effective and Modality.SPATIAL in b.unsupported
    assert nulls.eng.ignored_undeclared == 0 and sends.eng.ignored_undeclared == 60


def test_R3f_no_declaration_is_the_same_as_declaring_all_four():
    a, b = Rig(), Rig({"D-001": frozenset(m.value for m in Modality)})
    for i in range(60):
        t = tele(i, lost=("proximity",) if 20 <= i < 24 else ())
        x, y = a.feed(t), b.feed(t)
        assert (x.score, x.observability, x.unavailable, x.missing, x.recovering, x.unsupported) == (y.score, y.observability, y.unavailable, y.missing, y.recovering, y.unsupported)


def test_R3g_the_declaration_is_stored_returned_and_validated(tmp_path):
    db = EventStore(str(tmp_path / "d.db"))
    assert db.register_device("D-001", "Cam", "camera", "now", None, ["network"])
    assert db.register_device("D-002", "Badge", "badge", "now", None)
    assert db.get_device("D-001")["signals"] == ["network"] and db.get_device("D-002")["signals"] is None
    assert {d["device_id"]: d["signals"] for d in db.list_devices()} == {"D-001": ["network"], "D-002": None}
    db.close()
    db = EventStore(str(tmp_path / "d.db"))  # a restart reads it back
    assert db.get_device("D-001")["signals"] == ["network"]
    db.close()


def test_R3g_an_older_database_without_the_column_is_upgraded(tmp_path):
    import sqlite3

    p = str(tmp_path / "old.db")
    c = sqlite3.connect(p)
    c.execute("CREATE TABLE devices (device_id TEXT PRIMARY KEY, name TEXT NOT NULL, type TEXT NOT NULL, created_at TEXT NOT NULL, key_hash TEXT)")
    c.execute("INSERT INTO devices VALUES ('D-009', 'Old', 'badge', 'then', NULL)")
    c.commit()
    c.close()
    db = EventStore(p)
    assert db.get_device("D-009")["signals"] is None
    db.close()


def test_R3g_the_api_rejects_empty_unknown_and_repeated_declarations():
    from pydantic import ValidationError

    from backend.api.app import DeviceIn

    assert DeviceIn(device_id="D-001", name="x", signals=["network", "spatial"]).signals
    for bad in ([], ["network", "network"], ["wifi"]):
        with pytest.raises(ValidationError):
            DeviceIn(device_id="D-001", name="x", signals=bad)


# ---- R4 -----------------------------------------------------------------------------------------------

SCENARIOS = {
    "full": (None, ()),
    "network_only": (NET_ONLY, NETONLY_LOST),
    "location_lost": (None, ("spatial",)),
    "proximity_lost": (None, ("proximity",)),
    "net_only_net_recovering": (NET_ONLY, NETONLY_LOST),  # driven below: network drops then returns
    "device_lost_location_recovering": (None, ("device",)),
}
WORDS = {Modality.SPATIAL: "location", Modality.NETWORK: "network", Modality.DEVICE: "device-health", Modality.PROXIMITY: "proximity"}


def scenario_fused(name):
    declared, lost = SCENARIOS[name]
    rig = Rig(declared)
    for i in range(30):
        f = rig.feed(tele(i, lost=lost))
    if name == "net_only_net_recovering":
        rig.feed(tele(30, lost=lost + ("network",)))
        f = rig.feed(tele(31, lost=lost))
    if name == "device_lost_location_recovering":
        rig.feed(tele(30, lost=("device", "spatial")))
        f = rig.feed(tele(31, lost=("device",)))
    return f


@pytest.mark.parametrize("name", SCENARIOS)
def test_R4a_b_the_sentence_names_exactly_what_the_record_says(name):
    f = scenario_fused(name)
    text = observation(f)
    observed = [m for m in Modality if m not in f.unavailable]
    for m in Modality:
        mentioned = WORDS[m] in text
        involved = m in f.unavailable or (m in observed and bool(f.unavailable))
        assert mentioned == involved, (name, m, text)
    if f.unavailable and not observed:
        assert "No signal was observed" in text and "based on" not in text
    elif f.unavailable:
        tail = ", ".join(WORDS[m] for m in observed[:-1]) + (" and " if len(observed) > 1 else "") + WORDS[observed[-1]] if observed else ""
        assert f"based on {tail} telemetry only" in text
    else:
        assert "based on" not in text and "All four signals were observed" in text
    for label, group in (("not supported", f.unsupported), ("stopped reporting", f.missing), ("recovering", f.recovering)):
        assert (label in text) == bool(group), (name, label, text)


@pytest.mark.parametrize("name", SCENARIOS)
def test_R4c_the_wording_is_neutral_and_never_lists_a_missing_signal_as_a_factor(name):
    f = scenario_fused(name)
    text = explain(f)
    assert text.count("confirm") == text.count("neither confirm nor rule out") == (1 if f.unavailable else 0)
    for line in text.splitlines():
        if line.strip()[:2] in ("1.", "2.", "3.", "4."):  # the factor lines
            assert not any(WORDS[m] in line.split("(")[0] for m in f.unavailable)


def test_R4d_the_alert_record_and_its_text_agree():
    rig = Rig(NET_ONLY)
    for i in range(120):
        rig.feed(tele(i, lost=NETONLY_LOST, spike=100 <= i < 108))
    ev = rig.events[0]
    assert ev["unsupported"] == ["device", "proximity", "spatial"] and ev["missing"] == [] and ev["recovering"] == []
    assert ev["unavailable"] == sorted(ev["unsupported"] + ev["missing"] + ev["recovering"])
    assert ev["observation"] in ev["explanation"]
    assert "based on network telemetry only" in ev["observation"] and "not supported by this device" in ev["observation"]
    json.dumps(ev)  # storable


def test_R4e_a_fully_observed_alert_keeps_the_original_factor_wording():
    text = explain(scenario_fused("full"))
    assert "Observation: All four signals were observed." in text and "behavioural anomaly on D-001" in text
