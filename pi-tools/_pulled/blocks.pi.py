"""Configurable HSV and contour based orange/purple block detector."""

from __future__ import annotations

from dataclasses import dataclass
from types import MappingProxyType
from typing import Mapping

import numpy as np

from .models import BlockColor, BlockObservation
from route_v2.vision_config import BlockVisionProfile, ColorBand


@dataclass(frozen=True)
class HSVRange:
    lower: tuple[int, int, int]
    upper: tuple[int, int, int]


DEFAULT_RANGES = {
    BlockColor.ORANGE: HSVRange((5, 80, 80), (25, 255, 255)),
    BlockColor.PURPLE: HSVRange((125, 60, 60), (170, 255, 255)),
}


@dataclass(frozen=True)
class RejectedBlockCandidate:
    reason: str
    bounding_box: tuple[int, int, int, int]
    metrics: Mapping[str, float]
    frame_index: int
    timestamp_ns: int


@dataclass(frozen=True)
class BlockDetectionResult:
    accepted: tuple[BlockObservation, ...]
    rejected: tuple[RejectedBlockCandidate, ...]
    frame_index: int
    timestamp_ns: int


class ProfiledBlockDetector:
    """Detect one area-scoped color while preserving gate diagnostics."""

    def __init__(
        self,
        profile: BlockVisionProfile,
        *,
        color: BlockColor,
        cv2_module=None,
    ) -> None:
        if color not in (BlockColor.ORANGE, BlockColor.PURPLE):
            raise ValueError("profiled detector color must be orange or purple")
        if cv2_module is None:
            import cv2 as cv2_module
        self.profile = profile
        self.color = color
        self._cv2 = cv2_module

    def _band_mask(self, image, bands: tuple[ColorBand, ...]):
        mask = np.zeros(image.shape[:2], dtype=np.uint8)
        for band in bands:
            mask = self._cv2.bitwise_or(
                mask,
                self._cv2.inRange(
                    image,
                    np.asarray(band.lower, dtype=np.uint8),
                    np.asarray(band.upper, dtype=np.uint8),
                ),
            )
        return mask

    def detect(self, frame, *, timestamp_ns: int = 0, frame_index: int = 0) -> BlockDetectionResult:
        if frame is None or len(frame.shape) != 3:
            raise ValueError("frame must be a BGR image")
        height, width = frame.shape[:2]
        hsv = self._cv2.cvtColor(frame, self._cv2.COLOR_BGR2HSV)
        lab = self._cv2.cvtColor(frame, self._cv2.COLOR_BGR2LAB)
        channel_masks = {
            "hsv": self._band_mask(hsv, self.profile.hsv_bands),
            "lab": self._band_mask(lab, self.profile.lab_bands),
        }
        mask = self._cv2.bitwise_or(channel_masks["hsv"], channel_masks["lab"])
        centroid_mask = None
        if self.profile.centroid_hsv_bands or self.profile.centroid_lab_bands:
            centroid_mask = self._cv2.bitwise_or(
                self._band_mask(hsv, self.profile.centroid_hsv_bands),
                self._band_mask(lab, self.profile.centroid_lab_bands),
            )
        roi = self.profile.roi
        x1, x2 = int(round(roi.left * width)), int(round(roi.right * width))
        y1, y2 = int(round(roi.top * height)), int(round(roi.bottom * height))
        roi_mask = np.zeros_like(mask)
        roi_mask[y1:y2, x1:x2] = 255
        mask = self._cv2.bitwise_and(mask, roi_mask)
        if centroid_mask is not None:
            centroid_mask = self._cv2.bitwise_and(centroid_mask, roi_mask)
        kernel_size = self.profile.morphology_kernel
        kernel = self._cv2.getStructuringElement(
            self._cv2.MORPH_RECT, (kernel_size, kernel_size)
        )
        mask = self._cv2.morphologyEx(mask, self._cv2.MORPH_OPEN, kernel)
        mask = self._cv2.morphologyEx(mask, self._cv2.MORPH_CLOSE, kernel)
        if centroid_mask is not None:
            centroid_mask = self._cv2.morphologyEx(
                centroid_mask, self._cv2.MORPH_OPEN, kernel
            )
            centroid_mask = self._cv2.morphologyEx(
                centroid_mask, self._cv2.MORPH_CLOSE, kernel
            )
        contours, _ = self._cv2.findContours(
            mask, self._cv2.RETR_EXTERNAL, self._cv2.CHAIN_APPROX_SIMPLE
        )
        accepted: list[BlockObservation] = []
        rejected: list[RejectedBlockCandidate] = []
        for contour in contours:
            area = float(self._cv2.contourArea(contour))
            x, y, box_width, box_height = self._cv2.boundingRect(contour)
            if box_width <= 0 or box_height <= 0:
                continue
            aspect = box_width / box_height
            rectangularity = min(1.0, area / float(box_width * box_height))
            moments = self._cv2.moments(contour)
            gate_center = (
                moments["m10"] / moments["m00"],
                moments["m01"] / moments["m00"],
            ) if moments["m00"] else (x + box_width / 2.0, y + box_height / 2.0)
            center_y = gate_center[1] / height
            bottom_y = (y + box_height) / height
            near_field_fill = 0.0
            metric_values = {
                "area_px": area,
                "aspect_ratio": aspect,
                "rectangularity": rectangularity,
                "height_px": float(box_height),
                "center_y": center_y,
                "bottom_y": bottom_y,
                "near_field_fill": near_field_fill,
            }
            reason = None
            if area < self.profile.area_px.minimum:
                reason = "area_below_min"
            elif area > self.profile.area_px.maximum:
                reason = "area_above_max"
            elif aspect < self.profile.aspect_ratio.minimum:
                reason = "aspect_below_min"
            elif aspect > self.profile.aspect_ratio.maximum:
                reason = "aspect_above_max"
            elif rectangularity < self.profile.min_rectangularity:
                reason = "rectangularity_below_min"
            elif box_height < self.profile.height_px.minimum:
                reason = "height_below_min"
            elif box_height > self.profile.height_px.maximum:
                reason = "height_above_max"
            elif (self.profile.bottom_y is not None
                  and bottom_y < self.profile.bottom_y.minimum):
                reason = "bottom_y_below_min"
            elif (self.profile.bottom_y is not None
                  and bottom_y > self.profile.bottom_y.maximum):
                reason = "bottom_y_above_max"
            elif center_y < self.profile.center_y.minimum:
                reason = "center_y_below_min"
            elif center_y > self.profile.center_y.maximum:
                reason = "center_y_above_max"
            if reason is not None:
                rejected.append(RejectedBlockCandidate(
                    reason=reason,
                    bounding_box=(int(x), int(y), int(box_width), int(box_height)),
                    metrics=MappingProxyType(metric_values),
                    frame_index=frame_index,
                    timestamp_ns=timestamp_ns,
                ))
                continue

            contour_mask = None
            if self.profile.near_field is not None:
                near = self.profile.near_field
                near_x1 = int(round(near.left * width))
                near_x2 = int(round(near.right * width))
                near_y1 = int(round(near.top * height))
                near_y2 = int(round(near.bottom * height))
                contour_mask = np.zeros_like(mask)
                self._cv2.drawContours(contour_mask, [contour], -1, 255, -1)
                near_area = max(1, (near_x2 - near_x1) * (near_y2 - near_y1))
                near_field_fill = (
                    self._cv2.countNonZero(
                        contour_mask[near_y1:near_y2, near_x1:near_x2]
                    ) / near_area
                )
                metric_values["near_field_fill"] = near_field_fill
                if near_field_fill < self.profile.min_near_field_fill:
                    rejected.append(RejectedBlockCandidate(
                        reason="near_field_fill_below_min",
                        bounding_box=(int(x), int(y), int(box_width), int(box_height)),
                        metrics=MappingProxyType(metric_values),
                        frame_index=frame_index,
                        timestamp_ns=timestamp_ns,
                    ))
                    continue

            reported_center = gate_center
            if centroid_mask is not None:
                if contour_mask is None:
                    contour_mask = np.zeros_like(mask)
                    self._cv2.drawContours(contour_mask, [contour], -1, 255, -1)
                candidate_centroid_mask = self._cv2.bitwise_and(
                    centroid_mask, contour_mask
                )
                centroid_contours, _ = self._cv2.findContours(
                    candidate_centroid_mask,
                    self._cv2.RETR_EXTERNAL,
                    self._cv2.CHAIN_APPROX_SIMPLE,
                )
                if centroid_contours:
                    centroid_contour = max(
                        centroid_contours, key=self._cv2.contourArea
                    )
                    centroid_area = float(self._cv2.contourArea(centroid_contour))
                    centroid_moments = self._cv2.moments(centroid_contour)
                    if (centroid_area >= self.profile.centroid_min_area_px
                            and centroid_moments["m00"]):
                        reported_center = (
                            centroid_moments["m10"] / centroid_moments["m00"],
                            centroid_moments["m01"] / centroid_moments["m00"],
                        )
            center_xi = min(width - 1, max(0, int(round(reported_center[0]))))
            center_yi = min(height - 1, max(0, int(round(reported_center[1]))))
            channels = tuple(
                name for name, channel_mask in channel_masks.items()
                if channel_mask[center_yi, center_xi] != 0
            )
            confidence = max(0.0, min(1.0, 0.7 * rectangularity + 0.3 * min(1.0, area / 5000.0)))
            accepted.append(BlockObservation(
                color=self.color,
                bounding_box=(int(x), int(y), int(box_width), int(box_height)),
                center_px=reported_center,
                area=area,
                confidence=confidence,
                frame_index=frame_index,
                timestamp_ns=timestamp_ns,
                color_channels=channels,
            ))
        return BlockDetectionResult(
            accepted=tuple(sorted(accepted, key=lambda item: item.area, reverse=True)),
            rejected=tuple(sorted(rejected, key=lambda item: item.metrics["area_px"], reverse=True)),
            frame_index=frame_index,
            timestamp_ns=timestamp_ns,
        )


class BlockDetector:
    def __init__(
        self,
        *,
        ranges: dict[BlockColor, HSVRange] | None = None,
        min_area: float = 300.0,
        max_area: float | None = None,
        min_aspect: float = 0.2,
        max_aspect: float = 5.0,
        cv2_module=None,
    ) -> None:
        if min_area <= 0:
            raise ValueError("min_area must be positive")
        if cv2_module is None:
            import cv2 as cv2_module
        self._cv2 = cv2_module
        self.ranges = dict(ranges or DEFAULT_RANGES)
        self.min_area = float(min_area)
        self.max_area = max_area
        self.min_aspect = float(min_aspect)
        self.max_aspect = float(max_aspect)

    def detect(self, frame, *, timestamp_ns: int = 0, frame_index: int = 0):
        if frame is None or len(frame.shape) != 3:
            raise ValueError("frame must be a BGR image")
        hsv = self._cv2.cvtColor(frame, self._cv2.COLOR_BGR2HSV)
        results = []
        for color, value_range in self.ranges.items():
            lower = np.array(value_range.lower, dtype=np.uint8)
            upper = np.array(value_range.upper, dtype=np.uint8)
            mask = self._cv2.inRange(hsv, lower, upper)
            kernel = self._cv2.getStructuringElement(self._cv2.MORPH_RECT, (3, 3))
            mask = self._cv2.morphologyEx(mask, self._cv2.MORPH_OPEN, kernel)
            mask = self._cv2.morphologyEx(mask, self._cv2.MORPH_CLOSE, kernel)
            contours, _hierarchy = self._cv2.findContours(
                mask, self._cv2.RETR_EXTERNAL, self._cv2.CHAIN_APPROX_SIMPLE
            )
            for contour in contours:
                area = float(self._cv2.contourArea(contour))
                if area < self.min_area or (self.max_area is not None and area > self.max_area):
                    continue
                x, y, width, height = self._cv2.boundingRect(contour)
                if width <= 0 or height <= 0:
                    continue
                aspect = width / height
                if not self.min_aspect <= aspect <= self.max_aspect:
                    continue
                rectangle_area = float(width * height)
                rectangularity = min(1.0, area / rectangle_area)
                confidence = max(0.0, min(1.0, 0.5 * rectangularity + 0.5 * min(1.0, area / 5000.0)))
                moments = self._cv2.moments(contour)
                if moments["m00"]:
                    center = (
                        moments["m10"] / moments["m00"],
                        moments["m01"] / moments["m00"],
                    )
                else:
                    center = (x + width / 2.0, y + height / 2.0)
                results.append(
                    BlockObservation(
                        color=color,
                        bounding_box=(int(x), int(y), int(width), int(height)),
                        center_px=center,
                        area=area,
                        confidence=confidence,
                        frame_index=frame_index,
                        timestamp_ns=timestamp_ns,
                    )
                )
        return tuple(sorted(results, key=lambda item: item.area, reverse=True))


class StableBlockDetector(BlockDetector):
    def __init__(self, *, confirm_frames: int = 3, **kwargs) -> None:
        if confirm_frames <= 0:
            raise ValueError("confirm_frames must be positive")
        super().__init__(**kwargs)
        self.confirm_frames = int(confirm_frames)
        self._last_signature = None
        self._count = 0

    def update(self, frame, *, timestamp_ns: int = 0, frame_index: int = 0):
        candidates = self.detect(frame, timestamp_ns=timestamp_ns, frame_index=frame_index)
        candidate = candidates[0] if candidates else None
        if candidate is None:
            self._last_signature = None
            self._count = 0
            return None
        x, y, width, height = candidate.bounding_box
        signature = (candidate.color, round(x / 10), round(y / 10), round(width / 10), round(height / 10))
        if signature == self._last_signature:
            self._count += 1
        else:
            self._last_signature = signature
            self._count = 1
        return candidate if self._count >= self.confirm_frames else None
