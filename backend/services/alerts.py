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


class _Open:
    def __init__(self, alert_id: str, started: datetime, fused: Fused):
        self.alert_id, self.started = alert_id, started
        self.peak = fused.score
        self.peak_fused = fused
        self.below_since: float | None = None


def _iso(ts: datetime) -> str:
    return ts.astimezone(timezone.utc).isoformat()


class AlertManager:
    def __init__(self, cfg: AlertConfig | None = None, corr: CorrelationConfig | None = None):
        self.cfg = cfg or AlertConfig()
        self.corr = corr or CorrelationConfig()
        self._open: dict[str, _Open] = {}

    def update(self, f: Fused) -> list[dict]:
        """Feed one fused result; returns alert events ('opened'/'escalated'/'closed') to publish."""
        events: list[dict] = []
        cur = self._open.get(f.device_id)
        now = f.ts.timestamp()

        if cur is None:
            if f.score >= self.cfg.raise_at and not f.learning:
                cur = self._open[f.device_id] = _Open(f"{f.device_id}@{_iso(f.ts)}", f.ts, f)
                events.append(self._event("opened", cur, None))
            return events

        if f.score > cur.peak:
            before = severity(cur.peak, self.corr)
            cur.peak, cur.peak_fused = f.score, f  # keep the explanation for the WORST moment
            if _ORDER[severity(f.score, self.corr)] > _ORDER[before]:
                events.append(self._event("escalated", cur, None))

        if f.score < self.cfg.clear_at:
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
            "severity": severity(cur.peak, self.corr),
            "fusion": pf.fusion,
            "factors": factors(pf),
            "explanation": explain(pf),
        }
