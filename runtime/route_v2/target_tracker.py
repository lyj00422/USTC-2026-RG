from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
import math
from typing import Iterable

from rg_runtime.models import BlockObservation


class BlockTargetState(str, Enum):
    SEARCHING = "SEARCHING"
    CANDIDATE = "CANDIDATE"
    LOCKED = "LOCKED"
    LOST = "LOST"


@dataclass(frozen=True)
class BlockTargetResult:
    state: BlockTargetState
    target: BlockObservation | None
    consecutive_frames: int
    missed_frames: int


def _iou(left: BlockObservation, right: BlockObservation) -> float:
    lx, ly, lw, lh = left.bounding_box
    rx, ry, rw, rh = right.bounding_box
    x1, y1 = max(lx, rx), max(ly, ry)
    x2, y2 = min(lx + lw, rx + rw), min(ly + lh, ry + rh)
    intersection = max(0, x2 - x1) * max(0, y2 - y1)
    union = lw * lh + rw * rh - intersection
    return intersection / union if union else 0.0


class BlockTargetTracker:
    """Lock one visual block and refuse identity switches after lock."""

    def __init__(self, *, confirm_frames: int = 3,
                 capture_center_px: tuple[float, float],
                 max_center_distance_px: float = 80.0,
                 min_iou: float = 0.1,
                 max_missed_frames: int = 2):
        if confirm_frames < 1 or max_center_distance_px <= 0 or not 0 <= min_iou <= 1:
            raise ValueError("invalid block tracker limits")
        if max_missed_frames < 0:
            raise ValueError("max_missed_frames must be non-negative")
        self.confirm_frames = int(confirm_frames)
        self.capture_center_px = capture_center_px
        self.max_center_distance_px = float(max_center_distance_px)
        self.min_iou = float(min_iou)
        self.max_missed_frames = int(max_missed_frames)
        self.reset()

    def reset(self) -> None:
        self._state = BlockTargetState.SEARCHING
        self._target: BlockObservation | None = None
        self._consecutive = 0
        self._missed = 0
        self._last_frame_index: int | None = None

    def _result(self) -> BlockTargetResult:
        return BlockTargetResult(self._state, self._target, self._consecutive, self._missed)

    def _distance(self, item: BlockObservation, point: tuple[float, float]) -> float:
        return math.hypot(item.center_px[0] - point[0], item.center_px[1] - point[1])

    def update(self, candidates: Iterable[BlockObservation]) -> BlockTargetResult:
        items = tuple(candidates)
        newest = max((item.frame_index for item in items), default=None)
        if newest is not None and self._last_frame_index is not None and newest <= self._last_frame_index:
            return self._result()
        if newest is not None:
            self._last_frame_index = newest

        selected = None
        if self._target is None:
            selected = min(items, key=lambda item: self._distance(item, self.capture_center_px), default=None)
        else:
            matches = [
                item for item in items
                if _iou(self._target, item) >= self.min_iou
                or self._distance(item, self._target.center_px) <= self.max_center_distance_px
            ]
            selected = min(matches, key=lambda item: self._distance(item, self._target.center_px), default=None)

        if selected is None:
            self._missed += 1
            if self._state is BlockTargetState.LOCKED:
                if self._missed > self.max_missed_frames:
                    self._state = BlockTargetState.LOST
                    self._target = None
            else:
                self._state = BlockTargetState.SEARCHING
                self._target = None
                self._consecutive = 0
            return self._result()

        self._target = selected
        self._missed = 0
        self._consecutive += 1
        self._state = (
            BlockTargetState.LOCKED
            if self._consecutive >= self.confirm_frames
            else BlockTargetState.CANDIDATE
        )
        return self._result()
