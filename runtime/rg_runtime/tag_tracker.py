"""Stable, target-specific AprilTag observation tracking."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable

from .models import TagObservation
from route_v2.vision_config import TagVisionGate


@dataclass(frozen=True)
class TagTrackingResult:
    """The current target-tag state after one camera frame."""

    stable: bool
    observation: TagObservation | None
    consecutive_frames: int
    missed_frames: int


class TagTracker:
    """Confirm one tag only after consecutive detections.

    A short camera miss does not immediately invalidate a confirmed target;
    this is useful while the robot is moving.  A longer miss resets the
    confirmation so an old tag cannot drive a later route decision.
    """

    def __init__(self, *, target_id: int, confirm_frames: int | None = None,
                 max_missed_frames: int = 2, gate: TagVisionGate | None = None,
                 frame_size: tuple[int, int] | None = None):
        if target_id < 0:
            raise ValueError("target_id must be non-negative")
        if confirm_frames is None:
            confirm_frames = gate.confirm_frames if gate is not None else 3
        if confirm_frames < 1:
            raise ValueError("confirm_frames must be positive")
        if max_missed_frames < 0:
            raise ValueError("max_missed_frames must be non-negative")
        self.target_id = int(target_id)
        self.confirm_frames = int(confirm_frames)
        self.max_missed_frames = int(max_missed_frames)
        self.gate = gate
        self.frame_size = frame_size
        if gate is not None:
            if gate.target_id != self.target_id:
                raise ValueError("gate target_id must match tracker target_id")
            if frame_size is None or frame_size[0] <= 0 or frame_size[1] <= 0:
                raise ValueError("frame_size is required for a normalized Tag gate")
        self.reset()

    def reset(self) -> None:
        self._observation: TagObservation | None = None
        self._consecutive_frames = 0
        self._missed_frames = 0
        self._stable = False
        self._last_frame_index: int | None = None

    def _passes_gate(self, target: TagObservation) -> bool:
        if self.gate is None:
            return True
        width, height = self.frame_size
        x = target.center_px[0] / width
        y = target.center_px[1] / height
        window = self.gate.center_window
        if not (window.left <= x <= window.right and window.top <= y <= window.bottom):
            return False
        corners = target.corners_px
        edges = [
            ((corners[(index + 1) % 4][0] - corners[index][0]) ** 2
             + (corners[(index + 1) % 4][1] - corners[index][1]) ** 2) ** 0.5
            for index in range(4)
        ]
        average_edge = sum(edges) / len(edges)
        return self.gate.edge_px.minimum <= average_edge <= self.gate.edge_px.maximum

    def update(self, observations: Iterable[TagObservation]) -> TagTrackingResult:
        target = next((item for item in observations
                       if item.id == self.target_id and self._passes_gate(item)), None)
        if target is not None:
            if self._last_frame_index is not None and target.frame_index <= self._last_frame_index:
                return TagTrackingResult(
                    stable=self._stable,
                    observation=self._observation,
                    consecutive_frames=self._consecutive_frames,
                    missed_frames=self._missed_frames,
                )
            self._last_frame_index = target.frame_index
            self._observation = target
            self._consecutive_frames += 1
            self._missed_frames = 0
            self._stable = self._consecutive_frames >= self.confirm_frames
        else:
            # A miss breaks a new confirmation run even if a previously
            # confirmed tag is being held through the short gap.
            self._consecutive_frames = 0
            self._missed_frames += 1
            if self._missed_frames > self.max_missed_frames:
                self.reset()

        return TagTrackingResult(
            stable=self._stable,
            observation=self._observation,
            consecutive_frames=self._consecutive_frames,
            missed_frames=self._missed_frames,
        )
