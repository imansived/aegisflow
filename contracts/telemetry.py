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


# The four signal groups. A group is either fully reported or explicitly unavailable (every one of its fields null):
# never partly present, and never a silent zero. "Unavailable" is NOT "normal" (manual §5.11, D106).
SIGNAL_FIELDS: dict[str, tuple[str, ...]] = {
    "spatial": ("latitude", "longitude"),
    "device": ("battery", "cpu_usage", "memory_usage", "temperature", "uptime_s"),
    "network": ("network_connections", "bytes_sent", "bytes_received", "remote_endpoints"),
    "proximity": ("nearby_devices",),
}


class Telemetry(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: int = 1
    device_id: str = Field(pattern=DEVICE_ID_PATTERN)
    # Event time: when the reading was taken on the device, not when we received it.
    timestamp: datetime

    # Spatial. A group's fields are all numbers or all null; null means the device could not measure that signal
    # this tick (GPS lost, sensor off). The key must still be present: omitting a field is rejected, so an
    # unavailable signal is always something the sender said, never something the receiver guessed.
    latitude: float | None = Field(ge=-90, le=90)
    longitude: float | None = Field(ge=-180, le=180)

    # Device
    battery: float | None = Field(ge=0, le=100)
    cpu_usage: float | None = Field(ge=0, le=100)
    memory_usage: float | None = Field(ge=0, le=100)
    temperature: float | None = Field(ge=-40, le=150)  # degrees C
    uptime_s: int | None = Field(ge=0)

    # Network. remote_endpoints is an addition to the original spec's example: "known vs
    # unfamiliar endpoint" (spec §11) cannot be computed from a bare connection count.
    network_connections: int | None = Field(ge=0)
    bytes_sent: int | None = Field(ge=0)
    bytes_received: int | None = Field(ge=0)
    remote_endpoints: list[str] | None = Field(default_factory=list, max_length=100)

    # Proximity. An empty list means "nothing nearby", a real observation; null means "could not scan".
    nearby_devices: list[str] | None = Field(default_factory=list, max_length=50)

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
    def _nearby_ids_valid(cls, v: list[str] | None) -> list[str] | None:
        import re

        for d in v or []:
            if not re.fullmatch(DEVICE_ID_PATTERN, d):
                raise ValueError(f"bad device id in nearby_devices: {d!r}")
        return v

    @model_validator(mode="after")
    def _not_near_self(self) -> "Telemetry":
        if self.device_id in (self.nearby_devices or []):
            raise ValueError("a device cannot be near itself")
        return self

    @model_validator(mode="after")
    def _groups_all_or_none(self) -> "Telemetry":
        for group, fields in SIGNAL_FIELDS.items():
            present = [getattr(self, f) is not None for f in fields]
            if any(present) and not all(present):
                raise ValueError(f"the {group} signal must be fully reported or entirely null, not partly missing: {fields}")
        return self

    @property
    def unavailable(self) -> frozenset[str]:
        """The signal groups this event reports as unavailable ('spatial', 'device', 'network', 'proximity')."""
        return frozenset(g for g, fields in SIGNAL_FIELDS.items() if getattr(self, fields[0]) is None)
