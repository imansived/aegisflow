"""Names and helpers for the Redis side."""

import redis

STREAM = "telemetry"
DEAD_LETTER = "telemetry.dead"
GROUP = "processors"

DEFAULT_URL = "redis://localhost:6379/0"


def connect(url: str = DEFAULT_URL) -> redis.Redis:
    # decode_responses=True: we get str, not bytes. Everything on the wire is JSON text.
    return redis.Redis.from_url(url, decode_responses=True)


def ensure_group(r: redis.Redis, stream: str = STREAM, group: str = GROUP) -> None:
    """Create the consumer group (and the stream if absent). Safe to call repeatedly.

    id="0" means a NEW group reads the stream from the beginning, so events published before
    the processor first started are not skipped.
    """
    try:
        r.xgroup_create(stream, group, id="0", mkstream=True)
    except redis.ResponseError as e:
        if "BUSYGROUP" not in str(e):  # group already exists is fine
            raise


def stats(r: redis.Redis, stream: str = STREAM, group: str = GROUP) -> dict:
    """Operational numbers: how big is the stream, how far behind is the group.

    lag     = entries added to the stream that the group has not yet been delivered.
    pending = entries delivered but not yet acknowledged (in flight, or stuck).
    """
    out = {"length": r.xlen(stream), "lag": None, "pending": 0}
    try:
        groups = r.xinfo_groups(stream)
    except redis.ResponseError:  # the stream does not exist yet (nothing published, no processor started)
        return out
    for g in groups:
        if g["name"] == group:
            out["lag"] = g.get("lag")
            out["pending"] = g["pending"]
    return out
