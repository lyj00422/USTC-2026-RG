from control_hub.services.vision_service import VisionService


class FakeCamera:
    def __init__(self):
        self.sequence = 1
        self.sample_calls = 0

    def latest_sequence(self):
        return self.sequence

    def latest_sample(self):
        self.sample_calls += 1
        return self.sequence, object()


class CountingDetector:
    def __init__(self):
        self.calls = 0

    def detect(self, _frame, *, frame_index):
        self.calls += 1
        return []


def test_snapshot_is_detected_once_per_camera_sequence():
    camera = FakeCamera()
    tags = CountingDetector()
    blocks = CountingDetector()
    vision = VisionService(camera, tag_detector=tags, block_detector=blocks)

    assert vision.status() == vision.status()
    assert camera.sample_calls == 1
    assert tags.calls == 1
    assert blocks.calls == 1

    camera.sequence += 1
    vision.status()
    assert camera.sample_calls == 2
    assert tags.calls == 2
    assert blocks.calls == 2
