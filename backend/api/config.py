"""Server settings, read from environment variables.

Fails CLOSED: with authentication on (the default), the server refuses to start unless both role
tokens are configured. There are no built-in default passwords.
"""

import os
from dataclasses import dataclass
from pathlib import Path

from backend.streaming import bus


class ConfigError(RuntimeError):
    pass


def _flag(name: str, default: bool) -> bool:
    v = os.environ.get(name)
    return default if v is None else v.strip().lower() in ("1", "true", "yes", "on")


@dataclass(frozen=True)
class Settings:
    db_path: str = "data/aegis.db"
    redis_url: str = bus.DEFAULT_URL
    stream: str = bus.STREAM
    dead_letter: str = bus.DEAD_LETTER
    group: str = bus.GROUP

    auth: bool = True
    admin_token: str | None = None  # can register devices and drive the demo
    viewer_token: str | None = None  # read-only dashboard access
    cors_origins: tuple[str, ...] = ("http://localhost:5173",)

    run_processor: bool = True
    fusion: str = "auto"  # "auto" | "noisy_or" | "learned"
    design_path: Path = Path("models/frozen_design.json")

    # Per-device ingestion limit (telemetry is ~0.2/s per device; this leaves room for bursts and
    # catches a flooding device) and a per-token limit on the read API.
    telemetry_rate_per_s: float = 2.0
    telemetry_burst: int = 20
    api_rate_per_s: float = 20.0
    api_burst: int = 100

    @classmethod
    def from_env(cls) -> "Settings":
        s = cls(
            db_path=os.environ.get("AEGIS_DB", cls.db_path),
            redis_url=os.environ.get("AEGIS_REDIS_URL", cls.redis_url),
            auth=_flag("AEGIS_AUTH", True),
            admin_token=os.environ.get("AEGIS_ADMIN_TOKEN") or None,
            viewer_token=os.environ.get("AEGIS_VIEWER_TOKEN") or None,
            cors_origins=tuple(o for o in os.environ.get("AEGIS_CORS_ORIGINS", "http://localhost:5173").split(",") if o),
            run_processor=_flag("AEGIS_RUN_PROCESSOR", True),
            fusion=os.environ.get("AEGIS_FUSION", "auto"),
        )
        s.validate()
        return s

    def validate(self) -> None:
        if self.fusion not in ("auto", "noisy_or", "learned"):
            raise ConfigError(f"AEGIS_FUSION must be auto, noisy_or or learned, not {self.fusion!r}")
        if self.db_path == ":memory:":
            raise ConfigError("the API opens one connection per request, so an in-memory database cannot be shared; use a file")
        if self.auth:
            missing = [n for n, v in (("AEGIS_ADMIN_TOKEN", self.admin_token), ("AEGIS_VIEWER_TOKEN", self.viewer_token)) if not v]
            if missing:
                raise ConfigError(
                    f"authentication is on but {', '.join(missing)} is not set. Set both tokens, or set "
                    "AEGIS_AUTH=off for a local demo (never on a network you do not control)."
                )
            if self.admin_token == self.viewer_token:
                raise ConfigError("the admin and viewer tokens must differ")
            if min(len(self.admin_token), len(self.viewer_token)) < 16:
                raise ConfigError("tokens must be at least 16 characters")
