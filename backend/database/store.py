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
"""


class EventStore:
    def __init__(self, path: str = ":memory:"):
        # isolation_level=None = autocommit; we issue BEGIN/COMMIT/SAVEPOINT ourselves so a
        # whole batch is one transaction (one disk sync instead of one per event).
        self.db = sqlite3.connect(path, isolation_level=None)
        if path != ":memory:":
            self.db.execute("PRAGMA journal_mode=WAL")
            self.db.execute("PRAGMA synchronous=NORMAL")
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

    def close(self) -> None:
        self.db.close()
