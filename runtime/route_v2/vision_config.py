from __future__ import annotations

from dataclasses import dataclass
from types import MappingProxyType
from typing import Mapping, Sequence


def _triplet(value: Sequence[int], name: str) -> tuple[int, int, int]:
    if not isinstance(value, (list, tuple)) or len(value) != 3:
        raise ValueError(f"{name} must contain three channel values")
    result = tuple(int(item) for item in value)
    if any(item < 0 or item > 255 for item in result):
        raise ValueError(f"{name} channel values must be in 0..255")
    return result


@dataclass(frozen=True)
class Rect:
    left: float
    top: float
    right: float
    bottom: float

    @classmethod
    def from_value(cls, value: Sequence[float], name: str) -> "Rect":
        if not isinstance(value, (list, tuple)) or len(value) != 4:
            raise ValueError(f"{name} must be [left, top, right, bottom]")
        rect = cls(*(float(item) for item in value))
        if not (0 <= rect.left < rect.right <= 1 and 0 <= rect.top < rect.bottom <= 1):
            raise ValueError(f"{name} must be a normalized non-empty rectangle")
        return rect


@dataclass(frozen=True)
class ScalarRange:
    minimum: float
    maximum: float

    @classmethod
    def from_value(cls, value: Sequence[float], name: str, *, allow_zero: bool = True) -> "ScalarRange":
        if not isinstance(value, (list, tuple)) or len(value) != 2:
            raise ValueError(f"{name} must be [minimum, maximum]")
        result = cls(float(value[0]), float(value[1]))
        floor = 0 if allow_zero else 0.0
        if result.minimum < floor or result.maximum <= result.minimum:
            raise ValueError(f"{name} must be an increasing non-negative range")
        return result


@dataclass(frozen=True)
class ColorBand:
    lower: tuple[int, int, int]
    upper: tuple[int, int, int]

    @classmethod
    def from_mapping(cls, value: Mapping[str, object], name: str) -> "ColorBand":
        lower = _triplet(value.get("lower", ()), f"{name}.lower")
        upper = _triplet(value.get("upper", ()), f"{name}.upper")
        if any(lo > hi for lo, hi in zip(lower, upper)):
            raise ValueError(f"{name} lower channels must not exceed upper channels")
        return cls(lower, upper)


@dataclass(frozen=True)
class BlockVisionProfile:
    roi: Rect
    capture_window: Rect
    hsv_bands: tuple[ColorBand, ...]
    lab_bands: tuple[ColorBand, ...]
    area_px: ScalarRange
    aspect_ratio: ScalarRange
    height_px: ScalarRange
    center_y: ScalarRange
    min_rectangularity: float
    morphology_kernel: int
    bottom_y: ScalarRange | None = None
    near_field: Rect | None = None
    min_near_field_fill: float = 0.0
    ycrcb_bands: tuple[ColorBand, ...] = ()
    centroid_hsv_bands: tuple[ColorBand, ...] = ()
    centroid_lab_bands: tuple[ColorBand, ...] = ()
    centroid_min_area_px: float = 0.0

    @classmethod
    def from_mapping(cls, value: Mapping[str, object], name: str) -> "BlockVisionProfile":
        hsv = tuple(ColorBand.from_mapping(item, f"{name}.hsv_bands") for item in value.get("hsv_bands", ()))
        lab = tuple(ColorBand.from_mapping(item, f"{name}.lab_bands") for item in value.get("lab_bands", ()))
        ycrcb = tuple(ColorBand.from_mapping(
            item, f"{name}.ycrcb_bands"
        ) for item in value.get("ycrcb_bands", ()))
        centroid_hsv = tuple(ColorBand.from_mapping(
            item, f"{name}.centroid_hsv_bands"
        ) for item in value.get("centroid_hsv_bands", ()))
        centroid_lab = tuple(ColorBand.from_mapping(
            item, f"{name}.centroid_lab_bands"
        ) for item in value.get("centroid_lab_bands", ()))
        if not hsv and not lab and not ycrcb:
            raise ValueError(f"{name} needs at least one HSV, Lab, or YCrCb band")
        bottom_y_value = value.get("bottom_y")
        near_field_value = value.get("near_field")
        result = cls(
            roi=Rect.from_value(value.get("roi", ()), f"{name}.roi"),
            capture_window=Rect.from_value(value.get("capture_window", ()), f"{name}.capture_window"),
            hsv_bands=hsv,
            lab_bands=lab,
            area_px=ScalarRange.from_value(value.get("area_px", ()), f"{name}.area_px"),
            aspect_ratio=ScalarRange.from_value(value.get("aspect_ratio", ()), f"{name}.aspect_ratio"),
            height_px=ScalarRange.from_value(value.get("height_px", ()), f"{name}.height_px"),
            center_y=ScalarRange.from_value(value.get("center_y", ()), f"{name}.center_y"),
            min_rectangularity=float(value.get("min_rectangularity", 0)),
            morphology_kernel=int(value.get("morphology_kernel", 3)),
            bottom_y=None if bottom_y_value is None else ScalarRange.from_value(
                bottom_y_value, f"{name}.bottom_y"
            ),
            near_field=None if near_field_value is None else Rect.from_value(
                near_field_value, f"{name}.near_field"
            ),
            min_near_field_fill=float(value.get("min_near_field_fill", 0)),
            ycrcb_bands=ycrcb,
            centroid_hsv_bands=centroid_hsv,
            centroid_lab_bands=centroid_lab,
            centroid_min_area_px=float(value.get("centroid_min_area_px", 0)),
        )
        if not 0 < result.min_rectangularity <= 1:
            raise ValueError(f"{name}.min_rectangularity must be in (0, 1]")
        if result.morphology_kernel < 1 or result.morphology_kernel % 2 == 0:
            raise ValueError(f"{name}.morphology_kernel must be a positive odd integer")
        if result.bottom_y is not None and result.bottom_y.maximum > 1:
            raise ValueError(f"{name}.bottom_y must stay within normalized image coordinates")
        if result.near_field is None and result.min_near_field_fill != 0:
            raise ValueError(f"{name}.min_near_field_fill requires near_field")
        if result.near_field is not None and not 0 < result.min_near_field_fill <= 1:
            raise ValueError(f"{name}.min_near_field_fill must be in (0, 1]")
        if result.centroid_min_area_px < 0:
            raise ValueError(f"{name}.centroid_min_area_px must be non-negative")
        if (result.centroid_min_area_px > 0
                and not result.centroid_hsv_bands
                and not result.centroid_lab_bands):
            raise ValueError(f"{name}.centroid_min_area_px requires centroid color bands")
        return result


@dataclass(frozen=True)
class TagVisionGate:
    target_id: int
    center_window: Rect
    edge_px: ScalarRange
    confirm_frames: int

    @classmethod
    def from_mapping(cls, value: Mapping[str, object], name: str) -> "TagVisionGate":
        result = cls(
            target_id=int(value.get("target_id", -1)),
            center_window=Rect.from_value(value.get("center_window", ()), f"{name}.center_window"),
            edge_px=ScalarRange.from_value(value.get("edge_px", ()), f"{name}.edge_px"),
            confirm_frames=int(value.get("confirm_frames", 0)),
        )
        if result.target_id < 0 or result.confirm_frames < 1:
            raise ValueError(f"{name} needs a non-negative id and positive confirm_frames")
        return result


@dataclass(frozen=True)
class MotionGuard:
    max_distance_cm: float
    timeout_s: float

    @classmethod
    def from_mapping(cls, value: Mapping[str, object], name: str) -> "MotionGuard":
        result = cls(float(value.get("max_distance_cm", 0)), float(value.get("timeout_s", 0)))
        if result.max_distance_cm <= 0 or result.timeout_s <= 0:
            raise ValueError(f"{name} distance and timeout must be positive")
        return result


@dataclass(frozen=True)
class PickupAreaVisionConfig:
    profile: str
    search_right: MotionGuard
    search_left: MotionGuard
    search_speed: int
    coarse_speed: int
    fine_speed: int
    confirm_frames: int
    settle_s: float
    return_tolerance_cm: float
    alignment_max_distance_cm: float
    alignment_timeout_s: float
    locked_reacquire_timeout_s: float
    max_alignment_reversals: int
    # How far PAST the distance it actually has to cover the RETURN_BASELINE phase
    # may travel.  A sweep stops a little past its own guard (measured: 77.49 cm
    # against a 75 cm guard on run 20260924_213236), so bounding the return by the
    # SWEEP's guard faulted it 2.5 cm short of the baseline and the car never
    # searched the other side.  Measured overshoot is ~2.5 cm; 15 leaves room for a
    # slower approach without making the guard meaningless.
    return_guard_margin_cm: float = 15.0
    # The post-grab return-to-line speed, as a magnitude.  Separate from
    # fine_speed because they are different jobs that shared one number: the
    # fine speed is the last, slow approach onto a block (pickup_vision's
    # alignment), while this drives the baseline return and the swing that hunts
    # for the line afterwards.  Operator, 2026-09-24, on the swing: 「先左右10
    # 再左右20 最后左右30 速度提高一倍」 -- doubling fine_speed would have
    # doubled the alignment speed too, which was never asked for.
    #
    # Last with a default, so adding it did not touch the existing fields.
    # 0 means "same as fine_speed": an area that does not name it behaves exactly
    # as it did before this field existed.
    return_speed: int = 0

    @classmethod
    def from_mapping(cls, value: Mapping[str, object], name: str) -> "PickupAreaVisionConfig":
        coarse_speed = int(value.get("coarse_speed", 0))
        fine_speed = int(value.get("fine_speed", 0))
        result = cls(
            profile=str(value.get("profile", "")),
            search_right=MotionGuard.from_mapping(value.get("search_right", {}), f"{name}.search_right"),
            search_left=MotionGuard.from_mapping(value.get("search_left", {}), f"{name}.search_left"),
            search_speed=int(value.get("search_speed", coarse_speed)),
            coarse_speed=coarse_speed,
            fine_speed=fine_speed,
            return_speed=int(value.get("return_speed", fine_speed) or fine_speed),
            confirm_frames=int(value.get("confirm_frames", 0)),
            settle_s=float(value.get("settle_s", 0)),
            return_tolerance_cm=float(value.get("return_tolerance_cm", 0)),
            alignment_max_distance_cm=float(value.get("alignment_max_distance_cm", 0)),
            alignment_timeout_s=float(value.get("alignment_timeout_s", 0)),
            locked_reacquire_timeout_s=float(value.get("locked_reacquire_timeout_s", 0)),
            max_alignment_reversals=int(value.get("max_alignment_reversals", 0)),
            # Defaulted rather than required: every config that predates it keeps the
            # behaviour this field exists to fix the bound of (it only ever widens the
            # return's allowance by the margin).
            return_guard_margin_cm=float(value.get("return_guard_margin_cm", 15.0)),
        )
        if not result.profile:
            raise ValueError(f"{name}.profile is required")
        if (not 1 <= abs(result.search_speed) <= 100
                or not 1 <= abs(result.coarse_speed) <= 100
                or not 1 <= abs(result.fine_speed) <= 100
                or not 1 <= abs(result.return_speed) <= 100):
            raise ValueError(f"{name} lateral speeds must be non-zero and within 1..100")
        if abs(result.fine_speed) >= abs(result.coarse_speed):
            raise ValueError(f"{name}.fine_speed must be slower than coarse_speed")
        if result.confirm_frames < 1 or result.settle_s < 0 or result.return_tolerance_cm < 0:
            raise ValueError(f"{name} confirmation and settling values are invalid")
        if (result.alignment_max_distance_cm <= 0 or result.alignment_timeout_s <= 0
                or result.locked_reacquire_timeout_s <= 0
                or result.max_alignment_reversals < 1):
            raise ValueError(f"{name} alignment guards must be positive")
        if result.return_guard_margin_cm < 0:
            raise ValueError(f"{name}.return_guard_margin_cm cannot be negative")
        return result


@dataclass(frozen=True)
class RouteVisionConfig:
    calibrated: bool
    frame_timeout_s: float
    task_startup_timeout_s: float
    result_stall_timeout_s: float
    prescan_frames: int
    prescan_confirm_frames: int
    tag_takeover_max_cm: float
    tag_takeover_timeout_s: float
    action_wait_s: float
    purple_action_package: str
    purple_action_forward_speed: int
    purple_action_ack_timeout_s: float
    block_profiles: Mapping[str, BlockVisionProfile]
    tag_gates: Mapping[str, TagVisionGate]
    pickup_areas: Mapping[str, PickupAreaVisionConfig]

    @classmethod
    def from_mapping(cls, value: Mapping[str, object]) -> "RouteVisionConfig":
        profiles = {
            str(name): BlockVisionProfile.from_mapping(item, f"vision.block_profiles.{name}")
            for name, item in value.get("block_profiles", {}).items()
        }
        tags = {
            str(name): TagVisionGate.from_mapping(item, f"vision.tag_gates.{name}")
            for name, item in value.get("tag_gates", {}).items()
        }
        areas = {
            str(name): PickupAreaVisionConfig.from_mapping(item, f"vision.pickup_areas.{name}")
            for name, item in value.get("pickup_areas", {}).items()
        }
        expected_profiles = {
            "purple_j3_prescan", "purple_pickup_close", "orange_pickup_close",
            "build_occupancy",
        }
        if set(profiles) != expected_profiles:
            raise ValueError(f"vision.block_profiles must be exactly {sorted(expected_profiles)}")
        if set(tags) != {"pickup_seek_line", "pickup_2_seek_line"}:
            raise ValueError("vision.tag_gates must define pickup_seek_line and pickup_2_seek_line")
        if tags["pickup_seek_line"].target_id != 3 or tags["pickup_2_seek_line"].target_id != 4:
            raise ValueError("Tag gates are state scoped: pickup seek uses Tag3 and pickup 2 seek uses Tag4")
        if set(areas) != {"purple", "orange"}:
            raise ValueError("vision.pickup_areas must define purple and orange")
        for name, area in areas.items():
            if area.profile not in profiles:
                raise ValueError(f"vision.pickup_areas.{name}.profile is unknown")
        result = cls(
            calibrated=bool(value.get("calibrated", False)),
            frame_timeout_s=float(value.get("frame_timeout_s", 0)),
            task_startup_timeout_s=float(value.get("task_startup_timeout_s", 0)),
            result_stall_timeout_s=float(value.get("result_stall_timeout_s", 0)),
            prescan_frames=int(value.get("prescan_frames", 0)),
            prescan_confirm_frames=int(value.get("prescan_confirm_frames", 0)),
            tag_takeover_max_cm=float(value.get("tag_takeover_max_cm", 0)),
            tag_takeover_timeout_s=float(value.get("tag_takeover_timeout_s", 0)),
            action_wait_s=float(value.get("action_wait_s", 0)),
            purple_action_package=str(value.get("purple_action_package", "")),
            purple_action_forward_speed=int(value.get("purple_action_forward_speed", 0)),
            purple_action_ack_timeout_s=float(value.get("purple_action_ack_timeout_s", 0)),
            block_profiles=MappingProxyType(profiles),
            tag_gates=MappingProxyType(tags),
            pickup_areas=MappingProxyType(areas),
        )
        if (result.frame_timeout_s <= 0 or result.task_startup_timeout_s <= 0
                or result.tag_takeover_max_cm <= 0 or result.tag_takeover_timeout_s <= 0):
            raise ValueError("vision freshness and Tag takeover guards must be positive")
        if result.result_stall_timeout_s <= result.frame_timeout_s:
            raise ValueError("vision.result_stall_timeout_s must exceed frame_timeout_s")
        if result.prescan_frames < 1 or not 1 <= result.prescan_confirm_frames <= result.prescan_frames:
            raise ValueError("vision prescan confirmation must fit inside the scan window")
        if result.action_wait_s <= 0:
            raise ValueError("vision.action_wait_s must be positive")
        if not result.purple_action_package:
            raise ValueError("vision.purple_action_package is required")
        if not 1 <= result.purple_action_forward_speed <= 20:
            raise ValueError("vision.purple_action_forward_speed must be in 1..20")
        if result.purple_action_ack_timeout_s <= 0:
            raise ValueError("vision.purple_action_ack_timeout_s must be positive")
        return result
