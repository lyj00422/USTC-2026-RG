"""Cached camera-frame detection shared by the operator APIs."""

from __future__ import annotations

import threading

from rg_runtime.apriltag import AprilTagDetector
from rg_runtime.blocks import BlockDetector


class VisionService:
    """Detect tags and blocks at most once for each captured frame."""

    def __init__(self, camera, *, tag_detector=None, block_detector=None) -> None:
        self.camera = camera
        self.tag_detector = tag_detector or AprilTagDetector()
        self.block_detector = block_detector or BlockDetector(min_area=100)
        self._lock = threading.RLock()
        self._cached_sequence: int | None = None
        self._cached_status: dict | None = None
        self._cached_blocks: tuple = ()

    def status(self) -> dict:
        with self._lock:
            sequence = self.camera.latest_sequence()
            if sequence == self._cached_sequence and self._cached_status is not None:
                return dict(self._cached_status)

            sequence, frame = self.camera.latest_sample()
            status = {
                "sequence": sequence,
                "frame_available": frame is not None,
                "apriltags": [],
                "blocks": [],
                "error": None,
            }
            if frame is not None:
                try:
                    status["apriltags"] = [item.to_dict() for item in self.tag_detector.detect(frame, frame_index=sequence)]
                    detected_blocks = list(self.block_detector.detect(frame, frame_index=sequence))
                    self._cached_blocks = tuple(detected_blocks)
                    status["blocks"] = [item.to_dict() for item in detected_blocks]
                except Exception as exc:
                    self._cached_blocks = ()
                    status["error"] = str(exc)
            self._cached_sequence = sequence
            self._cached_status = status
            return dict(status)

    def blocks(self) -> tuple:
        """Return raw block objects from the cached frame detection."""
        self.status()
        with self._lock:
            return self._cached_blocks
