"""Parking-position block detection and one-shot firmware routine 3 orchestration."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
import threading
import time

from rg_runtime.blocks import BlockDetector
from rg_runtime.models import BlockColor


class PickupState(str, Enum):
    IDLE = "IDLE"
    SEARCHING = "SEARCHING"
    RUNNING = "RUNNING"
    FAULT = "FAULT"


@dataclass(frozen=True)
class PickupConfig:
    preferred_color: BlockColor = BlockColor.PURPLE
    min_confidence: float = 0.45
    confirm_frames: int = 3
    min_area: float = 300.0
    action_timeout_s: float = 45.0

    def __post_init__(self) -> None:
        if self.preferred_color not in (BlockColor.ORANGE, BlockColor.PURPLE):
            raise ValueError("preferred_color must be orange or purple")
        if not 0.0 <= self.min_confidence <= 1.0:
            raise ValueError("min_confidence must be between 0 and 1")
        if self.confirm_frames <= 0:
            raise ValueError("confirm_frames must be positive")
        if self.min_area <= 0:
            raise ValueError("min_area must be positive")
        if self.action_timeout_s <= 0:
            raise ValueError("action_timeout_s must be positive")


class PickupService:
    ROUTINE = 3

    def __init__(self, camera, arm, *, config: PickupConfig | None = None, event_log=None, background: bool = False, clock=time.monotonic, vision_service=None):
        self.camera = camera
        self.arm = arm
        self.vision_service = vision_service
        self.config = config or PickupConfig()
        self.event_log = event_log
        self.background = background
        self.clock = clock
        self.state = PickupState.IDLE
        self.target: dict | None = None
        self.error: str | None = None
        self._started_at: float | None = None
        self._last_signature = None
        self._last_frame_sequence: int | None = None
        self._stable_count = 0
        self._lock = threading.RLock()
        self._stop_event = threading.Event()
        self._worker: threading.Thread | None = None

    def start(self, config: PickupConfig | None = None) -> dict:
        with self._lock:
            if self.state in {PickupState.SEARCHING, PickupState.RUNNING}:
                raise RuntimeError("pickup is already running")
            if config is not None:
                self.config = config
            camera_status = self.camera.status()
            if not camera_status.get("running") or not camera_status.get("frame_available"):
                raise RuntimeError("camera must be running with an available frame")
            arm_status = self.arm.status()
            if not arm_status.get("connected"):
                raise RuntimeError("arm must be connected before pickup")
            if arm_status.get("mode") != "READY":
                raise RuntimeError("arm must report READY before pickup")
            if arm_status.get("calibrated") is not True:
                raise RuntimeError("arm must report CAL=1 before pickup")
            self.state = PickupState.SEARCHING
            self.target = None
            self.error = None
            self._started_at = self.clock()
            self._last_signature = None
            self._last_frame_sequence = camera_status.get("sequence")
            self._stable_count = 0
            self._stop_event.clear()
            self._log("started", {"preferred_color": self.config.preferred_color.value, "routine": self.ROUTINE})
            if self.background:
                self._start_worker()
            return self.status()

    def stop(self) -> dict:
        with self._lock:
            if self.state is PickupState.RUNNING:
                self.arm.stop()
            self._stop_event.set()
            self.state = PickupState.IDLE
            self.target = None
            self.error = None
            self._started_at = None
            self._last_signature = None
            self._last_frame_sequence = None
            self._stable_count = 0
            self._log("stopped", {})
            return self.status()

    def update(self, frame=None) -> dict | None:
        with self._lock:
            if self.state is PickupState.IDLE:
                return None
            if self.state is PickupState.RUNNING:
                return self._update_running()
            if self.state is PickupState.FAULT:
                return None
            if frame is None:
                sequence, frame = self.camera.latest_sample()
                if sequence == self._last_frame_sequence:
                    return None
                self._last_frame_sequence = sequence
            if frame is None:
                return None
            if self.vision_service is not None:
                candidates = [item for item in self.vision_service.blocks() if item.area >= self.config.min_area]
            else:
                detector = BlockDetector(min_area=self.config.min_area)
                candidates = detector.detect(frame)
            candidates = [item for item in candidates if item.confidence >= self.config.min_confidence]
            candidate = self._select(candidates)
            if candidate is None:
                self._last_signature = None
                self._stable_count = 0
                self.target = None
                return None
            signature = self._signature(candidate)
            self._stable_count = self._stable_count + 1 if signature == self._last_signature else 1
            self._last_signature = signature
            self.target = self._target_dict(candidate, frame)
            if self._stable_count < self.config.confirm_frames:
                return None
            self.arm.run(self.ROUTINE)
            self.state = PickupState.RUNNING
            self._started_at = self.clock()
            self.target["routine"] = self.ROUTINE
            self._log("target_confirmed", self.target)
            return dict(self.target)

    def status(self) -> dict:
        with self._lock:
            return {
                "state": self.state.value,
                "target": None if self.target is None else dict(self.target),
                "error": self.error,
                "routine": self.ROUTINE if self.state is PickupState.RUNNING else None,
                "stable_count": self._stable_count,
                "config": {
                    "preferred_color": self.config.preferred_color.value,
                    "min_confidence": self.config.min_confidence,
                    "confirm_frames": self.config.confirm_frames,
                    "min_area": self.config.min_area,
                    "action_timeout_s": self.config.action_timeout_s,
                },
            }

    def _update_running(self) -> dict | None:
        status = self.arm.status()
        if status.get("mode") == "READY" and status.get("routine") is None:
            self.state = PickupState.IDLE
            self._log("done", {"routine": self.ROUTINE, "target": self.target})
            return None
        if status.get("mode") == "FAULT":
            return self._fault("arm entered FAULT")
        if self._started_at is not None and self.clock() - self._started_at > self.config.action_timeout_s:
            return self._fault("pickup action timed out")
        return None

    def _fault(self, message: str) -> None:
        try:
            self.arm.stop()
        finally:
            self.state = PickupState.FAULT
            self.error = message
            self._log("fault", {"message": message, "routine": self.ROUTINE})
        return None

    def _start_worker(self) -> None:
        if self._worker is not None and self._worker.is_alive():
            return
        self._worker = threading.Thread(target=self._loop, name="pickup-vision", daemon=True)
        self._worker.start()

    def _loop(self) -> None:  # pragma: no cover - timing depends on camera hardware
        while not self._stop_event.wait(0.03):
            try:
                self.update()
            except Exception as exc:
                with self._lock:
                    self._fault(str(exc))

    def _select(self, candidates):
        preferred = [item for item in candidates if item.color is self.config.preferred_color]
        pool = preferred or [item for item in candidates if item.color is not self.config.preferred_color]
        return max(pool, key=lambda item: (item.confidence, item.area), default=None)

    @staticmethod
    def _signature(candidate):
        x, y, width, height = candidate.bounding_box
        return candidate.color, round(x / 10), round(y / 10), round(width / 10), round(height / 10)

    @staticmethod
    def _target_dict(candidate, frame) -> dict:
        height, width = frame.shape[:2]
        offset_x = candidate.center_px[0] - width / 2.0
        offset_y = candidate.center_px[1] - height / 2.0
        result = candidate.to_dict()
        result.update({"offset_px": [offset_x, offset_y], "routine": None})
        return result

    def _log(self, event_type: str, payload: dict) -> None:
        if self.event_log is not None:
            self.event_log.append(event_type, "vision_pickup", payload)
