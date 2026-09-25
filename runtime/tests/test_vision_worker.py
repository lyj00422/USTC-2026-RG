import time

import numpy as np

from route_v2.vision_worker import CameraCaptureWorker, LatestFrameBuffer, VisionTask, VisionWorker


class Clock:
    def __init__(self):
        self.now = 0.0

    def __call__(self):
        return self.now


def test_camera_snapshot_becomes_stale_without_blocking_control_loop():
    clock = Clock()
    frames = LatestFrameBuffer(clock=clock)
    frames.publish(np.zeros((2, 2, 3), dtype=np.uint8), captured_at=0.0)

    assert frames.snapshot(max_age_s=0.5).fresh is True
    clock.now = 0.51
    snapshot = frames.snapshot(max_age_s=0.5)
    assert snapshot.fresh is False
    assert snapshot.age_s == 0.51


def test_worker_processes_latest_frame_instead_of_queueing_intermediate_frames():
    frames = LatestFrameBuffer()
    seen = []
    worker = VisionWorker(frames, detectors={VisionTask.TAG3: lambda image, **kw: seen.append(kw["frame_id"]) or "ok"})
    generation = worker.select(VisionTask.TAG3)
    frames.publish(np.full((2, 2, 3), 1, dtype=np.uint8))
    frames.publish(np.full((2, 2, 3), 2, dtype=np.uint8))
    frames.publish(np.full((2, 2, 3), 3, dtype=np.uint8))
    worker.start()
    try:
        deadline = time.monotonic() + 1
        while worker.result() is None and time.monotonic() < deadline:
            time.sleep(0.005)
        result = worker.result()
        assert result is not None
        assert result.generation == generation
        assert result.frame_id == 3
        assert result.processing_s >= 0
        assert seen == [3]
    finally:
        worker.close()


def test_select_increments_generation_and_clears_previous_result():
    frames = LatestFrameBuffer()
    worker = VisionWorker(frames, detectors={VisionTask.TAG3: lambda image, **kw: "tag3"})
    first = worker.select(VisionTask.TAG3)
    frames.publish(np.zeros((2, 2, 3), dtype=np.uint8))
    worker.process_latest()
    assert worker.result().generation == first

    second = worker.select(VisionTask.NONE)

    assert second == first + 1
    assert worker.result() is None


def test_detector_exception_is_exposed_as_status_not_raised_in_control_loop():
    frames = LatestFrameBuffer()

    def broken(_image, **_kwargs):
        raise RuntimeError("detector failed")

    worker = VisionWorker(frames, detectors={VisionTask.TAG4: broken})
    worker.select(VisionTask.TAG4)
    frames.publish(np.zeros((2, 2, 3), dtype=np.uint8))

    worker.process_latest()

    assert worker.status().error == "detector failed"
    assert worker.result() is None


def test_camera_capture_worker_publishes_read_and_exposes_failure():
    image = np.ones((2, 2, 3), dtype=np.uint8)

    class Capture:
        def __init__(self):
            self.answers = [(True, image), (False, None)]

        def read(self):
            return self.answers.pop(0)

    frames = LatestFrameBuffer()
    capture = CameraCaptureWorker(Capture(), frames)
    assert capture.capture_once() is True
    assert frames.snapshot(max_age_s=1).frame_id == 1
    assert capture.capture_once() is False
    assert capture.status().error == "camera read failed"
