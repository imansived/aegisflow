"""SQLite event store: the source of truth that the stream processor writes to.

The primary key (device_id, ts) is what makes processing idempotent: a message that Redis
delivers twice is inserted once (manual §3.6, §3.19).
"""

import sqlite3
from datetime import timezone

from contracts.telemetry import Telemetry

SCHEMA = """
CREATE TABLE IF NOT EXISTS events (
    device_id TEXT NOT NULL,
    ts        TEXT NOT NULL,   -- event time, UTC ISO-8601 (not the time we processed it)
    payload   TEXT NOT NULL,   -- the validated Telemetry as JSON
    PRIMARY KEY (device_id, ts)
) WITHOUT ROWID;

CREATE TABLE IF NOT EXISTS alerts (
    alert_id   TEXT PRIMARY KEY,   -- deterministic: device + start time, so re-processing upserts
    device_id  TEXT NOT NULL,
    started    TEXT NOT NULL,
    ended      TEXT,               -- NULL while the alert is still open
    peak_score REAL NOT NULL,
    severity   TEXT NOT NULL,
    payload    TEXT NOT NULL       -- full alert as JSON: factors, explanation text, fusion name
);
CREATE INDEX IF NOT EXISTS alerts_device ON alerts (device_id, started);

-- The enrolled-device registry. Only a SHA-256 hash of each device's API key is stored, never the key.
CREATE TABLE IF NOT EXISTS devices (
    device_id  TEXT PRIMARY KEY,
    name       TEXT NOT NULL,
    type       TEXT NOT NULL,
    created_at TEXT NOT NULL,
    key_hash   TEXT                -- NULL for devices registered without a key (demo fleet)
);

-- Sampled fused scores over time, for the dashboard's history charts.
CREATE TABLE IF NOT EXISTS anomaly_history (
    device_id TEXT NOT NULL,
    ts        TEXT NOT NULL,
    score     REAL NOT NULL,
    severity  TEXT NOT NULL,
    effective TEXT NOT NULL,       -- per-modality effective scores as JSON
    PRIMARY KEY (device_id, ts)
) WITHOUT ROWID;
"""


class EventStore:
    def __init__(self, path: str = ":memory:", check_same_thread: bool = True):
        # isolation_level=None = autocommit; we issue BEGIN/COMMIT/SAVEPOINT ourselves so a
        # whole batch is one transaction (one disk sync instead of one per event).
        # check_same_thread=False is for the API's per-request connections: FastAPI may open a
        # request's dependency on one worker thread and close it on another. Each such connection is
        # used by one request at a time, so that is safe. The processor's write connection keeps the
        # strict default so a cross-thread mistake there fails loudly.
        self.db = sqlite3.connect(path, isolation_level=None, timeout=10, check_same_thread=check_same_thread)
        if path != ":memory:":
            self.db.execute("PRAGMA journal_mode=WAL")  # readers do not block the writer
            self.db.execute("PRAGMA synchronous=NORMAL")
            self.db.execute("PRAGMA busy_timeout=10000")
        self.db.executescript(SCHEMA)

    # --- transaction control -------------------------------------------------------------
    def begin(self) -> None:
        self.db.execute("BEGIN")

    def commit(self) -> None:
        self.db.execute("COMMIT")

    def savepoint(self) -> None:
        self.db.execute("SAVEPOINT msg")

    def release(self) -> None:
        self.db.execute("RELEASE msg")

    def rollback_to(self) -> None:
        self.db.execute("ROLLBACK TO msg")
        self.db.execute("RELEASE msg")

    # --- data ----------------------------------------------------------------------------
    def insert_event(self, t: Telemetry) -> bool:
        """True if newly stored, False if this (device, time) was already there."""
        ts = t.timestamp.astimezone(timezone.utc).isoformat()
        cur = self.db.execute(
            "INSERT OR IGNORE INTO events (device_id, ts, payload) VALUES (?, ?, ?)",
            (t.device_id, ts, t.model_dump_json()),
        )
        return cur.rowcount == 1

    def count(self) -> int:
        return self.db.execute("SELECT COUNT(*) FROM events").fetchone()[0]

    def events_for(self, device_id: str) -> list[Telemetry]:
        rows = self.db.execute(
            "SELECT payload FROM events WHERE device_id = ? ORDER BY ts", (device_id,)
        ).fetchall()
        return [Telemetry.model_validate_json(r[0]) for r in rows]

    def upsert_alert(self, a: dict) -> None:
        """Insert or update an alert by id: safe to repeat (idempotent, §3.6)."""
        import json

        self.db.execute(
            "INSERT INTO alerts (alert_id, device_id, started, ended, peak_score, severity, payload) "
            "VALUES (?, ?, ?, ?, ?, ?, ?) "
            "ON CONFLICT(alert_id) DO UPDATE SET ended=excluded.ended, peak_score=excluded.peak_score, "
            "severity=excluded.severity, payload=excluded.payload",
            (a["alert_id"], a["device_id"], a["started"], a["ended"], a["peak_score"], a["severity"], json.dumps(a)),
        )

    def alerts(self, device_id: str | None = None) -> list[dict]:
        import json

        q, args = "SELECT payload FROM alerts", ()
        if device_id:
            q, args = q + " WHERE device_id = ?", (device_id,)
        return [json.loads(p) for (p,) in self.db.execute(q + " ORDER BY started", args)]

    def close_stale_alerts(self) -> int:
        """Close alerts left 'open' by a previous process. At startup nothing is tracking them (the alert state
        lives in memory), so they would stay open forever. Each ends at its device's newest stored event, or at
        its own start if there is none. Returns how many were closed."""
        import json

        rows = self.db.execute("SELECT alert_id, device_id, started, payload FROM alerts WHERE ended IS NULL").fetchall()
        for alert_id, device_id, started, payload in rows:
            latest = self.db.execute("SELECT MAX(ts) FROM events WHERE device_id = ?", (device_id,)).fetchone()[0]
            ended = max(started, latest) if latest else started
            a = json.loads(payload)
            a["ended"], a["event"] = ended, "closed"
            self.db.execute("UPDATE alerts SET ended = ?, payload = ? WHERE alert_id = ?", (ended, json.dumps(a), alert_id))
        return len(rows)

    def latest_event_ts(self) -> str | None:
        """ISO time of the newest stored event, or None if there are none."""
        row = self.db.execute("SELECT MAX(ts) FROM events").fetchone()
        return row[0] if row else None

    def query_alerts(self, device_id: str | None = None, status: str | None = None, limit: int = 100) -> list[dict]:
        """Newest first. status: 'open' (not yet closed), 'closed', or None for both."""
        import json

        where, args = [], []
        if device_id:
            where.append("device_id = ?")
            args.append(device_id)
        if status == "open":
            where.append("ended IS NULL")
        elif status == "closed":
            where.append("ended IS NOT NULL")
        q = "SELECT payload, ended FROM alerts" + (" WHERE " + " AND ".join(where) if where else "")
        rows = self.db.execute(q + " ORDER BY started DESC LIMIT ?", (*args, int(limit))).fetchall()
        out = []
        for payload, ended in rows:
            a = json.loads(payload)
            a["status"] = "open" if ended is None else "closed"
            out.append(a)
        return out

    def get_alert(self, alert_id: str) -> dict | None:
        import json

        row = self.db.execute("SELECT payload, ended FROM alerts WHERE alert_id = ?", (alert_id,)).fetchone()
        if not row:
            return None
        a = json.loads(row[0])
        a["status"] = "open" if row[1] is None else "closed"
        return a

    def latest_events(self, device_id: str, limit: int = 100) -> list[Telemetry]:
        """The device's most recent events, newest first."""
        rows = self.db.execute(
            "SELECT payload FROM events WHERE device_id = ? ORDER BY ts DESC LIMIT ?", (device_id, int(limit))
        ).fetchall()
        return [Telemetry.model_validate_json(r[0]) for r in rows]

    # --- device registry -----------------------------------------------------------------
    def register_device(self, device_id: str, name: str, type_: str, created_at: str, key_hash: str | None) -> bool:
        """True if newly registered, False if the id already exists (nothing is changed)."""
        cur = self.db.execute(
            "INSERT OR IGNORE INTO devices (device_id, name, type, created_at, key_hash) VALUES (?, ?, ?, ?, ?)",
            (device_id, name, type_, created_at, key_hash),
        )
        return cur.rowcount == 1

    def get_device(self, device_id: str) -> dict | None:
        r = self.db.execute(
            "SELECT device_id, name, type, created_at, key_hash FROM devices WHERE device_id = ?", (device_id,)
        ).fetchone()
        return None if r is None else {"device_id": r[0], "name": r[1], "type": r[2], "created_at": r[3], "key_hash": r[4]}

    def list_devices(self) -> list[dict]:
        return [
            {"device_id": r[0], "name": r[1], "type": r[2], "created_at": r[3], "has_key": r[4] is not None}
            for r in self.db.execute("SELECT device_id, name, type, created_at, key_hash FROM devices ORDER BY device_id")
        ]

    # --- anomaly history -----------------------------------------------------------------
    def record_score(self, device_id: str, ts: str, score: float, severity: str, effective: dict) -> None:
        import json

        self.db.execute(
            "INSERT OR REPLACE INTO anomaly_history (device_id, ts, score, severity, effective) VALUES (?, ?, ?, ?, ?)",
            (device_id, ts, float(score), severity, json.dumps(effective)),
        )

    def score_history(self, device_id: str | None = None, limit: int = 500) -> list[dict]:
        import json

        q, args = "SELECT device_id, ts, score, severity, effective FROM anomaly_history", []
        if device_id:
            q, args = q + " WHERE device_id = ?", [device_id]
        rows = self.db.execute(q + " ORDER BY ts DESC LIMIT ?", (*args, int(limit))).fetchall()
        return [{"device_id": r[0], "ts": r[1], "score": r[2], "severity": r[3], "effective": json.loads(r[4])} for r in rows]

    def iter_events(self):
        """Every stored event in event-time order. Used to rebuild detector state after a restart."""
        for (payload,) in self.db.execute("SELECT payload FROM events ORDER BY ts, device_id"):
            yield Telemetry.model_validate_json(payload)

    def close(self) -> None:
        self.db.close()
