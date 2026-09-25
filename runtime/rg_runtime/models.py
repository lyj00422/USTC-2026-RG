"""Typed data contracts shared by detectors, state machines, and adapters."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from enum import Enum
import math
from typing import Any


class _ValueEnum(str, Enum):
    def __str__(self) -> str:
        return self.value


class BlockColor(_ValueEnum):
    NONE = "none"
    ORANGE = "orange"
    PURPLE = "purple"


class LineIntersection(_ValueEnum):
    NONE = "none"
    LEFT = "left"
    RIGHT = "right"
    CROSS = "cross"
    T = "t"


class MotionMode(_ValueEnum):
    FORWARD = "forward"
    TURN_LEFT = "turn_left"
    TURN_RIGHT = "turn_right"
    STOP = "stop"
    HOLD = "hold"


class TaskAction(_ValueEnum):
    NONE = "none"
    PICKUP = "pickup"
    RELEASE = "release"
    HOME = "home"


@dataclass(frozen=True)
class PoseCamera:
    rvec: tuple[float, float, float]
    tvec: tuple[float, float, float]

    def to_dict(self) -> dict[str, Any]:
        return {"rvec": list(self.rvec), "tvec": list(self.tvec)}


def _validate_timestamp(value: int, name: str) -> None:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ValueError(f"{name} must be a non-negative integer")


@dataclass(frozen=True)
class TagObservation:
    id: int
    family: str
    corners_px: tuple[tuple[float, float], ...]
    center_px: tuple[float, float]
    decision_margin: float
    timestamp_ns: int
    frame_index: int
    pose_camera: PoseCamera | None = None

    def __post_init__(self) -> None:
        if self.id < 0:
            raise ValueError("id must be non-negative")
        if self.family != "36H11":
            raise ValueError("family must be 36H11")
        if len(self.corners_px) != 4:
            raise ValueError("corners_px must contain four points")
        if not math.isfinite(float(self.decision_margin)):
            raise ValueError("decision_margin must be finite")
        _validate_timestamp(self.timestamp_ns, "timestamp_ns")
        _validate_timestamp(self.frame_index, "frame_index")

    def to_dict(self) -> dict[str, Any]:
        result = asdict(self)
        result["corners_px"] = [list(point) for point in self.corners_px]
        result["center_px"] = list(self.center_px)
        return result


@dataclass(frozen=True)
class BlockObservation:
    color: BlockColor
    bounding_box: tuple[int, int, int, int]
    center_px: tuple[float, float]
    area: float
    confidence: float
    frame_index: int = 0
    timestamp_ns: int = 0
    color_channels: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if len(self.bounding_box) != 4 or any(value < 0 for value in self.bounding_box):
            raise ValueError("bounding_box must contain non-negative x, y, width and height")
        if not math.isfinite(float(self.area)) or self.area <= 0:
            raise ValueError("area must be positive and finite")
        if not math.isfinite(float(self.confidence)) or not 0 <= self.confidence <= 1:
            raise ValueError("confidence must be between 0 and 1")
        _validate_timestamp(self.frame_index, "frame_index")
        _validate_timestamp(self.timestamp_ns, "timestamp_ns")

    def to_dict(self) -> dict[str, Any]:
        result = asdict(self)
        result["color"] = self.color.value
        result["bounding_box"] = list(self.bounding_box)
        result["center_px"] = list(self.center_px)
        result["color_channels"] = list(self.color_channels)
        return result


@dataclass(frozen=True)
class LineState:
    line_error: float | None
    sensor_mask: int
    intersection: LineIntersection
    line_lost: bool
    turn_completed: bool
    timestamp_ms: int

    def __post_init__(self) -> None:
        if self.line_error is not None and not math.isfinite(float(self.line_error)):
            raise ValueError("line_error must be finite or None")
        if self.sensor_mask < 0:
            raise ValueError("sensor_mask must be non-negative")
        _validate_timestamp(self.timestamp_ms, "timestamp_ms")

    def to_dict(self) -> dict[str, Any]:
        result = asdict(self)
        result["intersection"] = self.intersection.value
        return result


@dataclass(frozen=True)
class MotionCommand:
    mode: MotionMode
    speed: float | None
    duration_ms: int | None
    reason: str
    timestamp_ms: int

    def __post_init__(self) -> None:
        if self.speed is not None and (
            not math.isfinite(float(self.speed)) or not -1.0 <= self.speed <= 1.0
        ):
            raise ValueError("speed must be finite and between -1 and 1")
        if self.duration_ms is not None and self.duration_ms < 0:
            raise ValueError("duration_ms must be non-negative or None")
        _validate_timestamp(self.timestamp_ms, "timestamp_ms")

    def to_dict(self) -> dict[str, Any]:
        result = asdict(self)
        result["mode"] = self.mode.value
        return result


@dataclass(frozen=True)
class TaskCommand:
    action: TaskAction
    target_color: BlockColor
    reason: str
    timestamp_ms: int

    def __post_init__(self) -> None:
        _validate_timestamp(self.timestamp_ms, "timestamp_ms")
        if self.action == TaskAction.NONE and self.target_color != BlockColor.NONE:
            raise ValueError("none action must use none target_color")

    def to_dict(self) -> dict[str, Any]:
        result = asdict(self)
        result["action"] = self.action.value
        result["target_color"] = self.target_color.value
        return result
