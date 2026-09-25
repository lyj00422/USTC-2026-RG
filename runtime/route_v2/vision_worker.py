from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
import threading
import time
from typing import Any, Callable, Mapping


class VisionTask(str, Enum):
    NONE = "NONE"
    TAG2 = "TAG2"
    TAG3 = "TAG3"
    TAG4 = "TAG4"
    PURPLE_PRESCAN = "PURPLE_PRESCAN"
    PURPLE_CLOSE = "PURPLE_CLOSE"
    ORANGE_CLOSE = "ORANGE_CLOSE"
    BUILD_OCCUPANCY = "BUILD_OCCUPANCY"


@dataclass(frozen=True)
class FrameSnapshot:
    image: Any | None
    frame_id: int
    captured_at: float | None
    age_s: float
    fresh: bool


class LatestFrameBuffer:
    def __init__(self, *, clock: Callable[[], float] = time.monotonic):
        self._clock = clock
        self._lock = threading.Lock()
        self._image = None
        self._frame_id = 0
        self._captured_at: float | None = None

    def publish(self, image, *, captured_at: float | None = None) -> int:
        when = self._clock() if captured_at is None else float(captured_at)
        with self._lock:
            self._frame_id += 1
            self._image = image
            self._captured_at = when
            return self._frame_id

    def snapshot(self, *, max_age_s: float) -> FrameSnapshot:
        if max_age_s <= 0:
            raise ValueError("max_age_s must be positive")
        with self._lock:
            image, frame_id, captured_at = self._image, self._frame_id, self._captured_at
        age = float("inf") if captured_at is None else max(0.0, self._clock() - captured_at)
        return FrameSnapshot(image, frame_id, captured_at, age, image is not None and age <= max_age_s)


@dataclass(frozen=True)
class WorkerStatus:
    running: bool
    error: str | None
    frame_id: int
    generation: int = 0
    task: VisionTask = VisionTask.NONE


class CameraCaptureWorker:
    def __init__(self, capture, frames: LatestFrameBuffer, *, clock=time.monotonic):
        self._capture = capture
        self._frames = frames
        self._clock = clock
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._lock = threading.Lock()
        self._error: str | None = None

    def capture_once(self) -> bool:
        try:
            ok, image = self._capture.read()
        except Exception as exc:
            ok, image = False, None
            message = str(exc)
        else:
            message = "camera read failed"
        if not ok or image is None:
            with self._lock:
                self._error = message
            return False
        self._frames.publish(image, captured_at=self._clock())
        with self._lock:
            self._error = None
        return True

    def start(self) -> None:
        if self._thread is not None and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._loop, name="route-camera", daemon=True)
        self._thread.start()

    def _loop(self) -> None:
        while not self._stop.is_set():
            if not self.capture_once():
                self._stop.wait(0.01)

    def close(self) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=1.0)

    def status(self) -> WorkerStatus:
        with self._lock:
            error = self._error
        snapshot = self._frames.snapshot(max_age_s=365 * 24 * 3600)
        return WorkerStatus(
            running=self._thread is not None and self._thread.is_alive(),
            error=error,
            frame_id=snapshot.frame_id,
        )


@dataclass(frozen=True)
class VisionResult:
    task: VisionTask
    generation: int
    frame_id: int
    captured_at: float
    completed_at: float
    processing_s: float
    value: Any


class VisionWorker:
    def __init__(self, frames: LatestFrameBuffer, *, detectors: Mapping[VisionTask, Callable],
                 clock=time.monotonic, poll_s: float = 0.002):
        self._frames = frames
        self._detectors = dict(detectors)
        self._clock = clock
        self._poll_s = poll_s
        self._lock = threading.Lock()
        self._task = VisionTask.NONE
        self._generation = 0
        self._last_processed_frame = 0
        self._result: VisionResult | None = None
        self._error: str | None = None
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def select(self, task: VisionTask) -> int:
        task = VisionTask(task)
        with self._lock:
            self._task = task
            self._generation += 1
            self._last_processed_frame = 0
            self._result = None
            self._error = None
            return self._generation

    def start(self) -> None:
        if self._thread is not None and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._loop, name="route-vision", daemon=True)
        self._thread.start()

    def _loop(self) -> None:
        while not self._stop.wait(self._poll_s):
            self.process_latest()

    def process_latest(self) -> None:
        with self._lock:
            task, generation, last_frame = self._task, self._generation, self._last_processed_frame
        if task is VisionTask.NONE:
            return
        snapshot = self._frames.snapshot(max_age_s=365 * 24 * 3600)
        if snapshot.image is None or snapshot.frame_id <= last_frame:
            return
        detector = self._detectors.get(task)
        if detector is None:
            with self._lock:
                self._error = f"no detector configured for {task.value}"
            return
        started_at = self._clock()
        try:
            value = detector(
                snapshot.image,
                frame_id=snapshot.frame_id,
                captured_at=snapshot.captured_at,
                generation=generation,
            )
        except Exception as exc:
            with self._lock:
                if generation == self._generation and task is self._task:
                    self._error = str(exc)
                    self._last_processed_frame = snapshot.frame_id
            return
        completed_at = self._clock()
        with self._lock:
            if generation != self._generation or task is not self._task:
                return
            self._last_processed_frame = snapshot.frame_id
            self._error = None
            self._result = VisionResult(
                task, generation, snapshot.frame_id, snapshot.captured_at,
                completed_at, max(0.0, completed_at - started_at), value,
            )

    def result(self) -> VisionResult | None:
        with self._lock:
            return self._result

    def status(self) -> WorkerStatus:
        with self._lock:
            task, generation, error, last_frame = (
                self._task, self._generation, self._error, self._last_processed_frame
            )
        return WorkerStatus(
            running=self._thread is not None and self._thread.is_alive(),
            error=error,
            frame_id=last_frame,
            generation=generation,
            task=task,
        )

    def close(self) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=1.0)
