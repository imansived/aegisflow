"""Put telemetry onto the stream."""

import time
from collections.abc import Iterable

import redis

from contracts.telemetry import Telemetry

from .bus import STREAM

# Stream cap. Approximate trimming ("~") is far cheaper than exact, but trimming deletes the
# OLDEST entries whether or not a slow consumer has read them. The cap must therefore exceed
# the worst-case backlog; watch `bus.stats(...)["lag"]` (manual §3.6).
DEFAULT_MAXLEN = 500_000


def publish(
    r: redis.Redis,
    events: Iterable[Telemetry],
    stream: str = STREAM,
    maxlen: int | None = DEFAULT_MAXLEN,
) -> int:
    """XADD each event, pipelined (one network round trip per batch, not per event).

    Each entry carries the JSON payload and `pub_ms`, the wall-clock time it was published,
    which the processor uses to measure end-to-end latency.
    """
    pipe = r.pipeline(transaction=False)
    n = 0
    for t in events:
        pipe.xadd(
            stream,
            {"data": t.model_dump_json(), "pub_ms": str(int(time.time() * 1000))},
            maxlen=maxlen,
            approximate=True,
        )
        n += 1
        if n % 500 == 0:
            pipe.execute()
    pipe.execute()
    return n
