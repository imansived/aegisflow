"""The stream consumer: validate -> store -> handle -> acknowledge.

Delivery is at-least-once, so the order of operations is the whole design (manual §3.19):

    read -> validate -> [ BEGIN; insert (dedupe); handler; COMMIT ] -> XACK

A crash anywhere before XACK leaves the message pending, and it is redelivered. Because the
insert is keyed on (device, event time), redelivery is harmless. The handler runs only for
newly stored events, so non-idempotent state (e.g. a running mean) is not updated twice.
Handler state lives in memory, so after a crash it is rebuilt by replaying the store.
"""

import time
from collections.abc import Callable

import redis
from pydantic import ValidationError

from contracts.telemetry import Telemetry
from backend.database.store import EventStore

from .bus import DEAD_LETTER, GROUP, STREAM, ensure_group
from .metrics import Metrics

Handler = Callable[[Telemetry], None]
Message = tuple[str, dict[str, str]]


class Processor:
    def __init__(
        self,
        r: redis.Redis,
        store: EventStore,
        handler: Handler | None = None,
        *,
        stream: str = STREAM,
        group: str = GROUP,
        consumer: str = "worker-1",
        dead_letter: str = DEAD_LETTER,
        batch: int = 200,
        max_deliveries: int = 5,
        # None = do not check. A replay of recorded data has event times far from the wall
        # clock, so the check is only meaningful for live ingestion.
        max_future_skew_s: float | None = None,
        clock: Callable[[], float] = time.time,
    ):
        self.r, self.store, self.handler = r, store, handler
        self.stream, self.group, self.consumer = stream, group, consumer
        self.dead_letter = dead_letter
        self.batch, self.max_deliveries = batch, max_deliveries
        self.max_future_skew_s, self.clock = max_future_skew_s, clock
        self.metrics = Metrics()
        ensure_group(r, stream, group)

    # ---- reading ------------------------------------------------------------------------

    def run_once(self, block_ms: int = 500) -> int:
        """Read up to `batch` new messages and process them. Returns how many were read."""
        resp = self.r.xreadgroup(
            self.group, self.consumer, {self.stream: ">"}, count=self.batch, block=block_ms
        )
        if not resp:
            return 0
        messages: list[Message] = resp[0][1]
        self._process(messages)
        return len(messages)

    def recover(self, min_idle_ms: int = 30_000) -> int:
        """Take over messages that were delivered but never acknowledged.

        This is what rescues work after a crash. `min_idle_ms` stops us stealing a message
        another healthy consumer is still working on. Messages delivered too many times are
        poison: dead-lettered instead of retried forever.
        """
        _next, claimed, _deleted = self.r.xautoclaim(
            self.stream, self.group, self.consumer, min_idle_time=min_idle_ms, start_id="0-0", count=self.batch
        )
        if not claimed:
            return 0
        deliveries = {
            e["message_id"]: e["times_delivered"]
            for e in self.r.xpending_range(self.stream, self.group, claimed[0][0], claimed[-1][0], len(claimed) + 50)
        }
        fresh: list[Message] = []
        for msg_id, fields in claimed:
            if fields is None:  # entry was trimmed away while pending
                self.r.xack(self.stream, self.group, msg_id)
            elif deliveries.get(msg_id, 0) > self.max_deliveries:
                self._dead_letter(msg_id, fields, "max deliveries exceeded")
                self.metrics.dead_lettered += 1
                self.r.xack(self.stream, self.group, msg_id)
            else:
                fresh.append((msg_id, fields))
        self._process(fresh)
        return len(claimed)

    # ---- processing ---------------------------------------------------------------------

    def _process(self, messages: list[Message]) -> None:
        if not messages:
            return
        to_ack: list[str] = []
        self.store.begin()
        for msg_id, fields in messages:
            t = self._validate(msg_id, fields)
            if t is None:  # already dead-lettered
                to_ack.append(msg_id)
                continue
            self.store.savepoint()
            try:
                if self.store.insert_event(t):
                    if self.handler:
                        self.handler(t)
                    self.metrics.processed += 1
                    self._record_latency(fields)
                else:
                    self.metrics.duplicates += 1
                self.store.release()
                to_ack.append(msg_id)
            except Exception:
                # Undo just this message; the rest of the batch is unaffected. It stays
                # pending (no XACK), so it will be redelivered via recover().
                self.store.rollback_to()
                self.metrics.failed += 1
        self.store.commit()
        # ACK only after the commit: if we crashed before this line the data is safely
        # stored and the redelivery is deduplicated.
        if to_ack:
            self.r.xack(self.stream, self.group, *to_ack)

    def _validate(self, msg_id: str, fields: dict[str, str]) -> Telemetry | None:
        try:
            t = Telemetry.model_validate_json(fields["data"])
        except (KeyError, ValidationError, ValueError) as e:
            self._dead_letter(msg_id, fields, f"invalid: {type(e).__name__}")
            self.metrics.invalid += 1
            return None
        if self.max_future_skew_s is not None and t.timestamp.timestamp() > self.clock() + self.max_future_skew_s:
            self._dead_letter(msg_id, fields, "timestamp in the future")
            self.metrics.invalid += 1
            return None
        return t

    def _dead_letter(self, msg_id: str, fields: dict[str, str], reason: str) -> None:
        self.r.xadd(
            self.dead_letter,
            {"reason": reason, "orig_id": msg_id, "data": str(fields.get("data", ""))[:2000]},
            maxlen=10_000,
            approximate=True,
        )

    def _record_latency(self, fields: dict[str, str]) -> None:
        if "pub_ms" in fields:
            self.metrics.latencies_ms.append(time.time() * 1000 - int(fields["pub_ms"]))
