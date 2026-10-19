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


def explain(f: Fused) -> str:
    lines = [f"{f.severity.upper()} behavioural anomaly on {f.device_id} (anomaly score {f.score:.2f}, fusion: {f.fusion})"]
    for i, r in enumerate(factors(f), 1):
        why = "; ".join(r["reasons"]) or "elevated, no single dominant feature"
        lines.append(f"  {i}. {r['modality']} (contributes {r['contribution']:.2f}): {why}")
    lines.append("  Note: the score ranks how unusual this is; it is not a probability that an attack occurred.")
    return "\n".join(lines)
