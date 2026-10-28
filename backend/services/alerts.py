"""Alert lifecycle: one alert per incident, not one per second (manual §3.16).

Raise when the fused score crosses `raise_at`. The alert then stays open, tracking its peak,
until the score has been below `clear_at` for `clear_after_s` (hysteresis: raising and
clearing use different thresholds, so a score hovering at the boundary cannot flap).
"""

from dataclasses import dataclass
from datetime import datetime, timezone

from backend.correlation.engine import CorrelationConfig, Fused, severity
from backend.correlation.explain import explain, factors

_ORDER = {"normal": 0, "low": 1, "medium": 2, "high": 3}


@dataclass(frozen=True)
class AlertConfig:
    raise_at: float = 0.60
    clear_at: float = 0.40
    clear_after_s: float = 60.0


ACTIVE_SIGNAL = 0.30  # a modality at or above this effective score counts as "contributing" to an alert


def _n_active(f: Fused) -> int:
    return sum(1 for v in f.effective.values() if v >= ACTIVE_SIGNAL)


class _Open:
    def __init__(self, alert_id: str, started: datetime, fused: Fused):
        self.alert_id, self.started = alert_id, started
        self.peak = fused.score
        self.peak_fused = fused
        self.n_active = _n_active(fused)
        self.below_since: float | None = None
        self.last_seen = fused.ts  # the newest event seen while the alert was open
        self.sev = fused.severity  # worst severity reached


def _iso(ts: datetime) -> str:
    return ts.astimezone(timezone.utc).isoformat()


class AlertManager:
    def __init__(self, cfg: AlertConfig | None = None, corr: CorrelationConfig | None = None):
        self.cfg = cfg or AlertConfig()
        self.corr = corr or CorrelationConfig()
        self._open: dict[str, _Open] = {}

    def reset_device(self, device_id: str) -> list[dict]:
        """Close any open alert for a device whose clock jumped backwards, so none is left open forever.

        Returns the 'closed' event (ended at the alert's last seen moment) to publish and store."""
        cur = self._open.pop(device_id, None)
        return [self._event("closed", cur, cur.last_seen)] if cur else []

    def close_all(self) -> list[dict]:
        """Close every open alert (the analysis is being reset, or the source has stopped), each ended at the
        last moment it was seen. Without this a stopped or restarted system leaves 'open' alerts behind forever."""
        events = [self._event("closed", cur, cur.last_seen) for cur in self._open.values()]
        self._open.clear()
        return events

    def update(self, f: Fused) -> list[dict]:
        """Feed one fused result; returns alert events to publish.

        Kinds: 'opened', 'escalated' (severity rose), 'updated' (another signal joined) and 'closed'."""
        events: list[dict] = []
        cur = self._open.get(f.device_id)
        now = f.ts.timestamp()

        # The thresholds that judge THIS tick: the validated ones for the signals available now, else the configured ones.
        raise_at = f.operating.raise_at if f.operating else self.cfg.raise_at
        clear_at = f.operating.clear_at if f.operating else self.cfg.clear_at

        if cur is None:
            if f.alertable and f.score >= raise_at and not f.learning:
                cur = self._open[f.device_id] = _Open(f"{f.device_id}@{_iso(f.ts)}", f.ts, f)
                events.append(self._event("opened", cur, None))
            return events

        cur.last_seen = f.ts
        before = cur.sev
        n = _n_active(f)
        joined = n > cur.n_active
        if joined:
            cur.n_active = n
        if f.score > cur.peak or joined:
            # The explanation follows the worst moment, OR a moment when another signal joined. A saturating
            # fusion's score stops rising once one signal is enough, so "peak only" would hide the very signals
            # that arrive afterwards (found in the Phase 8 demo, manual §4.9).
            cur.peak, cur.peak_fused = max(cur.peak, f.score), f
        if f.alertable and _ORDER[f.severity] > _ORDER[cur.sev]:
            cur.sev = f.severity
        if _ORDER[cur.sev] > _ORDER[before]:
            events.append(self._event("escalated", cur, None))
        elif joined:
            events.append(self._event("updated", cur, None))

        if not f.alertable or f.score < clear_at:  # an unjudgeable tick cannot keep an alert alive either
            cur.below_since = cur.below_since if cur.below_since is not None else now
            if now - cur.below_since >= self.cfg.clear_after_s:
                events.append(self._event("closed", cur, f.ts))
                del self._open[f.device_id]
        else:
            cur.below_since = None
        return events

    def _event(self, kind: str, cur: _Open, ended: datetime | None) -> dict:
        pf = cur.peak_fused
        return {
            "event": kind,
            "alert_id": cur.alert_id,
            "device_id": pf.device_id,
            "started": _iso(cur.started),
            "ended": _iso(ended) if ended else None,
            "peak_score": round(cur.peak, 3),
            "severity": cur.sev,
            "unavailable": sorted(m.value for m in pf.unavailable),
            "fusion": pf.fusion,
            "factors": factors(pf),
            "explanation": explain(pf),
        }
