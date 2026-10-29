"""Turn a fused result into words an analyst can act on (manual §3.16, spec §18)."""

from contracts.scenarios import Modality

from .engine import Fused

Z_SHOW = 2.0  # only quote a z-score when it is actually unusual
MIN_FACTOR_SCORE = 0.10  # a modality below this effective score is not listed as a contributing factor

_RULES = {
    "restricted_zone": lambda v: "entered a restricted zone",
    "speed_mps": lambda v: f"moving at an implausible {v:.1f} m/s for a person on foot",
    "zone_rarity": lambda v: "is in a zone it rarely uses at this time of day",
}
_Z_PHRASES = {
    "log_sent": "upload volume is {z:.1f} standard deviations above normal",
    "log_recv": "download volume is {z:.1f} standard deviations above normal",
    "conns": "connection count is {z:.1f} standard deviations above normal",
    "unfamiliar_1m": "contacting endpoints it does not normally use ({z:.1f} standard deviations above normal)",
    "cpu": "CPU load is {z:.1f} standard deviations above normal",
    "temp": "temperature is {z:.1f} standard deviations above normal",
    "drain": "battery is draining {z:.1f} standard deviations faster than normal",
    "unenrolled": "unknown (unenrolled) devices are nearby ({z:.1f} standard deviations above normal)",
    "persist_s": "an unknown device has stayed nearby unusually long ({z:.1f} standard deviations above normal)",
}


def reasons(evidence: dict[str, float]) -> list[str]:
    """Plain-language reasons for one modality.

    Rule findings (restricted zone, impossible speed, rare zone) come first: they are
    categorical, not on the same scale as a z-score, so ranking them together would be
    meaningless. Statistical findings follow, largest z first.
    """
    rules = [text(v) for key, text in _RULES.items() if (v := evidence.get(key)) is not None]
    stats = sorted(
        ((v, _Z_PHRASES[k].format(z=v)) for k, v in evidence.items() if k in _Z_PHRASES and v >= Z_SHOW),
        reverse=True,
    )
    return rules + [text for _, text in stats]


def factors(f: Fused) -> list[dict]:
    """Ranked contributing signals: [{modality, contribution, effective, reasons}]."""
    rows = []
    for m in Modality:
        eff = f.effective.get(m, 0.0)
        # Soft scores are rarely exactly 0; listing every faint signal would bury the real ones.
        if eff < MIN_FACTOR_SCORE:
            continue
        rows.append(
            {
                "modality": m.value,
                "contribution": round(f.contributions.get(m, 0.0), 3),
                "effective_score": round(eff, 3),
                "reasons": reasons(f.evidence.get(m, {})),
            }
        )
    rows.sort(key=lambda r: (r["contribution"], r["effective_score"]), reverse=True)
    return rows


_NAME = {Modality.SPATIAL: "location", Modality.NETWORK: "network", Modality.DEVICE: "device-health", Modality.PROXIMITY: "proximity"}


def _names(ms) -> str:
    n = [_NAME[m] for m in Modality if m in ms]
    return n[0] if len(n) == 1 else ", ".join(n[:-1]) + " and " + n[-1]


def _signals(ms) -> str:
    return _names(ms) + (" signal" if len(ms) == 1 else " signals")


def observation(f: Fused) -> str:
    """What evidence the result rests on, built from the same sets that go in the alert record (R4).

    Neutral by construction: a signal that is not observed is never evidence for or against an anomaly."""
    sentences = []
    if f.unsupported:
        sentences.append(f"The {_signals(f.unsupported)} {'is' if len(f.unsupported) == 1 else 'are'} not supported by this device.")
    if f.missing:
        sentences.append(f"The {_signals(f.missing)} stopped reporting.")
    if f.recovering:
        sentences.append(f"The {_signals(f.recovering)} {'is' if len(f.recovering) == 1 else 'are'} recovering after an outage; "
                         "judgment on it is withheld until its baseline window refills.")
    observed = [m for m in Modality if m not in f.unavailable]
    if f.unavailable and observed:
        sentences.append(f"This alert is based on {_names(observed)} telemetry only. Signals that were not observed neither confirm nor rule out an attack.")
    elif f.unavailable:
        sentences.append("No signal was observed. Signals that were not observed neither confirm nor rule out an attack.")
    else:
        sentences.append("All four signals were observed.")
    return " ".join(sentences)


def explain(f: Fused) -> str:
    lines = [f"{f.severity.upper()} behavioural anomaly on {f.device_id} (anomaly score {f.score:.2f}, fusion: {f.fusion})"]
    for i, r in enumerate(factors(f), 1):
        why = "; ".join(r["reasons"]) or "elevated, no single dominant feature"
        lines.append(f"  {i}. {r['modality']} (contributes {r['contribution']:.2f}): {why}")
    lines.append("  Observation: " + observation(f))
    lines.append("  Note: the score ranks how unusual this is; it is not a probability that an attack occurred.")
    return "\n".join(lines)
