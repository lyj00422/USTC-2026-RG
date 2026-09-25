"""Typed contracts for real and simulated hardware links."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


class ArmMode(str, Enum):
    UNKNOWN = "UNKNOWN"
    LOCKED = "LOCKED"
    READY = "READY"
    BUSY = "BUSY"
    FAULT = "FAULT"


@dataclass(frozen=True)
class EncoderState:
    valid: bool
    rpm: tuple[float, float, float, float]
    distance_mm: tuple[float, float, float, float]
    timestamp_ms: int

    def __post_init__(self) -> None:
        if len(self.rpm) != 4 or len(self.distance_mm) != 4:
            raise ValueError("encoder values must contain four wheels")
        if self.timestamp_ms < 0:
            raise ValueError("timestamp_ms must be non-negative")

    @classmethod
    def unavailable(cls, timestamp_ms: int = 0) -> "EncoderState":
        return cls(False, (0.0, 0.0, 0.0, 0.0), (0.0, 0.0, 0.0, 0.0), timestamp_ms)


@dataclass(frozen=True)
class DeviceHealth:
    connected: bool = False
    last_rx_ms: int | None = None
    last_error: str | None = None


@dataclass(frozen=True)
class ArmState:
    mode: ArmMode = ArmMode.UNKNOWN
    routine: int | None = None
    step: int | None = None
    suction_commanded: bool = False
    calibrated: bool | None = None
