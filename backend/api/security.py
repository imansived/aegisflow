"""Authentication helpers and rate limiting (manual §3.12, §3.22).

Implemented, and nothing more: role tokens (viewer/admin), per-device API keys stored only as a
SHA-256 hash, constant-time comparison, and token-bucket rate limits. NOT implemented: TLS
(terminate it at a reverse proxy), key rotation, per-user accounts, audit logging.
"""

import hashlib
import hmac
import secrets
import time
from collections.abc import Callable

from .config import Settings


def hash_key(key: str) -> str:
    return hashlib.sha256(key.encode()).hexdigest()


def new_device_key() -> str:
    return "ak_" + secrets.token_urlsafe(24)


def constant_time_equal(a: str, b: str) -> bool:
    # compare_digest avoids leaking, through timing, how many leading characters matched.
    return hmac.compare_digest(a.encode(), b.encode())


def role_of(token: str | None, s: Settings) -> str | None:
    """'admin', 'viewer', or None. With auth off everyone is admin (local demo only)."""
    if not s.auth:
        return "admin"
    if not token:
        return None
    if s.admin_token and constant_time_equal(token, s.admin_token):
        return "admin"
    if s.viewer_token and constant_time_equal(token, s.viewer_token):
        return "viewer"
    return None


class RateLimiter:
    """Token bucket per key: `rate` tokens/second, up to `burst` stored.

    Bounded memory: at most `max_keys` buckets are kept; the stalest are dropped first, so an
    attacker inventing keys cannot grow the table without limit.
    """

    def __init__(self, rate: float, burst: int, clock: Callable[[], float] = time.monotonic, max_keys: int = 10_000):
        self.rate, self.burst, self.clock, self.max_keys = rate, float(burst), clock, max_keys
        self._buckets: dict[str, tuple[float, float]] = {}  # key -> (tokens, last_time)

    def allow(self, key: str) -> bool:
        now = self.clock()
        tokens, last = self._buckets.get(key, (self.burst, now))
        tokens = min(self.burst, tokens + (now - last) * self.rate)
        ok = tokens >= 1.0
        self._buckets[key] = (tokens - 1.0 if ok else tokens, now)
        if len(self._buckets) > self.max_keys:
            for k in sorted(self._buckets, key=lambda k: self._buckets[k][1])[: len(self._buckets) - self.max_keys]:
                del self._buckets[k]
        return ok

    def retry_after(self, key: str) -> float:
        tokens, _ = self._buckets.get(key, (self.burst, 0.0))
        return max(0.0, (1.0 - tokens) / self.rate)
