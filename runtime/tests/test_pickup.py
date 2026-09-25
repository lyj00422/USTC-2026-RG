import cv2
import numpy as np
import pytest

from control_hub.services.pickup_service import PickupConfig, PickupService, PickupState
def frame(*, orange=False, purple=False):
    image = np.zeros((240, 320, 3), dtype=np.uint8)
    if orange:
        cv2.rectangle(image, (30, 70), (110, 180), (0, 140, 255), -1)
    if purple:
        cv2.rectangle(image, (190, 70), (270, 180), (255, 0, 255), -1)
    return image


class FakeCamera:
    def __init__(self, image):
        self.image = image
        self.running = True
        self.sequence = 0

    def status(self):
        return {"running": self.running, "frame_available": self.image is not None, "sequence": self.sequence}

    def latest_frame(self):
        return self.image

    def latest_sample(self):
        return self.sequence, self.image

    def advance(self):
        self.sequence += 1


class FakeArm:
    def __init__(self):
        self.connected = True
        self.calls = []
        self._status = {"mode": "READY", "calibrated": True, "routine": None}

    def status(self):
        return {"connected": self.connected, **self._status}

    def run(self, routine):
        self.calls.append(("run", routine))
        self._status.update(mode="BUSY", routine=routine)
        return self.status()

    def stop(self):
        self.calls.append(("stop",))
        self._status.update(mode="LOCKED", routine=None)
        return self.status()


def make_service(image, **kwargs):
    return PickupService(FakeCamera(image), FakeArm(), **kwargs)


def test_purple_is_selected_before_orange_and_action_three_runs_once():
    service = make_service(frame(orange=True, purple=True), config=PickupConfig(confirm_frames=2, min_area=500))
    service.start()

    service.camera.advance()
    assert service.update() is None
    service.camera.advance()
    result = service.update()
    assert result is not None
    assert result["color"] == "purple"
    assert result["routine"] == 3
    assert service.arm.calls == [("run", 3)]
    assert service.update() is None
    assert service.arm.calls == [("run", 3)]
    assert service.state is PickupState.RUNNING


def test_action_done_returns_to_idle_and_no_target_rejects_start():
    service = make_service(frame(orange=True), config=PickupConfig(confirm_frames=1, min_area=500))
    service.start()
    service.camera.advance()
    service.update()
    assert service.state is PickupState.RUNNING
    service.arm._status.update(mode="READY", routine=None)
    service.update()
    assert service.state is PickupState.IDLE

    empty = make_service(None)
    with pytest.raises(RuntimeError, match="camera"):
        empty.start()


def test_action_timeout_stops_and_faults():
    now = [0.0]
    service = make_service(
        frame(orange=True),
        config=PickupConfig(confirm_frames=1, min_area=500, action_timeout_s=2.0),
        clock=lambda: now[0],
    )
    service.start()
    service.camera.advance()
    service.update()
    now[0] = 2.1
    service.update()
    assert service.state is PickupState.FAULT
    assert service.arm.calls == [("run", 3), ("stop",)]


def test_start_requires_ready_calibrated_arm():
    service = make_service(frame(orange=True))
    service.arm._status["mode"] = "LOCKED"
    with pytest.raises(RuntimeError, match="READY"):
        service.start()


def test_same_camera_frame_is_not_counted_as_multiple_confirmations():
    camera = FakeCamera(frame(purple=True))
    service = PickupService(camera, FakeArm(), config=PickupConfig(confirm_frames=2, min_area=500))
    service.start()
    assert service.update() is None
    assert service.update() is None
    assert service.state is PickupState.SEARCHING
    camera.advance()
    result = service.update()
    assert result is None
    assert service.state is PickupState.SEARCHING
    camera.advance()
    result = service.update()
    assert result is not None
    assert result["routine"] == 3
