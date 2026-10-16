"""The telemetry message a device sends.

This is the boundary where untrusted input enters the system (manual §3.12), so it is
strict: unknown fields are rejected, ranges are checked, timestamps must carry a timezone.

Deliberately NOT here:
  * ground-truth labels (manual §3.14) - a label inside telemetry leaks the answer.
  * the zone the device is in - the device reports lat/lon only; the processor derives the
    zone. A device that could self-report its zone could lie about it.
"""

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

DEVICE_ID_PATTERN = r"^D-\d{3}$"


class Telemetry(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: int = 1
    device_id: str = Field(pattern=DEVICE_ID_PATTERN)
    # Event time: when the reading was taken on the device, not when we received it.
    timestamp: datetime

    # Spatial
    latitude: float = Field(ge=-90, le=90)
    longitude: float = Field(ge=-180, le=180)

    # Device
    battery: float = Field(ge=0, le=100)
    cpu_usage: float = Field(ge=0, le=100)
    memory_usage: float = Field(ge=0, le=100)
    temperature: float = Field(ge=-40, le=150)  # degrees C
    uptime_s: int = Field(ge=0)

    # Network. remote_endpoints is an addition to the original spec's example: "known vs
    # unfamiliar endpoint" (spec §11) cannot be computed from a bare connection count.
    network_connections: int = Field(ge=0)
    bytes_sent: int = Field(ge=0)
    bytes_received: int = Field(ge=0)
    remote_endpoints: list[str] = Field(default_factory=list, max_length=100)

    # Proximity
    nearby_devices: list[str] = Field(default_factory=list, max_length=50)

    @field_validator("timestamp")
    @classmethod
    def _timestamp_is_aware(cls, v: datetime) -> datetime:
        # Naive datetimes are ambiguous: the same string means different instants on
        # different machines. Reject them rather than guess.
        if v.tzinfo is None or v.utcoffset() is None:
            raise ValueError("timestamp must include a timezone (e.g. 'Z')")
        return v

    @field_validator("nearby_devices")
    @classmethod
    def _nearby_ids_valid(cls, v: list[str]) -> list[str]:
        import re

        for d in v:
            if not re.fullmatch(DEVICE_ID_PATTERN, d):
                raise ValueError(f"bad device id in nearby_devices: {d!r}")
        return v

    @model_validator(mode="after")
    def _not_near_self(self) -> "Telemetry":
        if self.device_id in self.nearby_devices:
            raise ValueError("a device cannot be near itself")
        return self
