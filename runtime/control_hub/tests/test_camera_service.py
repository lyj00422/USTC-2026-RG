import os
from pathlib import Path

import numpy as np
import pytest

from control_hub.services.camera_service import CameraService, CameraServiceError, CameraSettings, split_recording_name
from control_hub.services.event_log import EventLog
from control_hub.state import HubState


class FakeCapture:
    def __init__(self, frames):
        self.frames = list(frames)
        self.released = False

    def isOpened(self):
        return True

    def read(self):
        if not self.frames:
            return False, None
        return True, self.frames.pop(0)

    def release(self):
        self.released = True


class FakeWriter:
    def __init__(self):
        self.frames = []
        self.released = False

    def isOpened(self):
        return True

    def write(self, frame):
        self.frames.append(frame.copy())

    def release(self):
        self.released = True


class FakeCv2:
    IMWRITE_JPEG_QUALITY = 1

    def __init__(self):
        self.writers = []

    def imencode(self, _ext, _frame, _options):
        return True, np.frombuffer(b"jpeg-data", dtype=np.uint8)

    def imwrite(self, path, _frame):
        Path(path).write_bytes(b"image")
        return True

    def VideoWriter_fourcc(self, *_args):
        return 1

    def VideoWriter(self, *_args):
        writer = FakeWriter()
        self.writers.append(writer)
        return writer


def make_service(tmp_path, capture, cv2):
    return CameraService(
        CameraSettings(device=0, width=1280, height=720, fps=30.0),
        tmp_path,
        HubState(),
        EventLog(),
        cv2_module=cv2,
        capture_factory=lambda _settings: capture,
        background=False,
    )


def test_camera_service_shares_latest_frame_and_snapshot(tmp_path):
    frame = np.zeros((20, 30, 3), dtype=np.uint8)
    capture = FakeCapture([frame])
    service = make_service(tmp_path, capture, FakeCv2())
    service.start()
    with pytest.raises(CameraServiceError, match="already running"):
        service.start()
    service.process_once()
    sequence, jpeg = service.wait_for_jpeg(0, timeout_s=0)
    assert sequence == 1
    assert jpeg == b"jpeg-data"
    sample_sequence, sample = service.latest_sample()
    assert sample_sequence == 1
    assert np.array_equal(sample, frame)
    sample[0, 0, 0] = 255
    assert service.latest_sample()[1][0, 0, 0] == 0
    snapshot = service.snapshot()
    assert snapshot.is_file()
    assert service.latest_snapshot_path() == snapshot
    service.stop()
    assert capture.released
    sequence, sample = service.latest_sample()
    assert sequence == 1
    assert sample is None


def test_camera_service_records_frames(tmp_path):
    frame = np.zeros((20, 30, 3), dtype=np.uint8)
    capture = FakeCapture([frame, frame])
    cv2 = FakeCv2()
    service = make_service(tmp_path, capture, cv2)
    service.start()
    service.start_recording("arm_pose")
    service.process_once()
    result = service.stop_recording()
    assert result["frames"] == 1
    assert result["path"].endswith("_arm_pose.avi")
    assert cv2.writers[0].released
    service.stop()


def test_recording_names_yield_a_label_and_a_stamp():
    assert split_recording_name("20260916_125545_100000_第一次跑.avi", 0) == ("第一次跑", "2026-09-16T12:55:45")
    # An underscore in the note is part of the label, not a separator.
    assert split_recording_name("20260916_125545_100000_my_run.avi", 0)[0] == "my_run"
    # Not our pattern (hand-copied onto the Pi): the stem is the label and the
    # time comes from the file, rather than a stamp being invented for it.
    label, stamp = split_recording_name("copied_from_laptop.avi", 1_700_000_000)
    assert label == "copied_from_laptop"
    assert stamp.startswith("2023-")


def test_recording_file_refuses_anything_outside_the_recordings_folder(tmp_path):
    service = make_service(tmp_path, FakeCapture([]), FakeCv2())
    folder = tmp_path / "recordings"
    (folder / "nested").mkdir(parents=True)
    (folder / "nested" / "inner.avi").write_bytes(b"x")
    (folder / "notes.txt").write_bytes(b"x")
    (tmp_path / "outside.avi").write_bytes(b"x")
    inside = folder / "20260916_125545_100000_ok.avi"
    inside.write_bytes(b"y")

    # The folder is readable whether or not the camera has ever been started.
    assert [item["name"] for item in service.list_recordings()] == [inside.name]
    assert service.recording_file(inside.name) == inside.resolve()
    for name in ["../outside.avi", r"..\outside.avi", "nested/inner.avi", "notes.txt", "/etc/passwd", "", "missing.avi"]:
        with pytest.raises(FileNotFoundError):
            service.recording_file(name)


def test_the_library_deletes_finished_recordings_and_keeps_the_open_one(tmp_path):
    frame = np.zeros((20, 30, 3), dtype=np.uint8)
    service = make_service(tmp_path, FakeCapture([frame]), FakeCv2())
    folder = tmp_path / "recordings"
    folder.mkdir()
    older = folder / "20260916_125545_100000_第一段.avi"
    older.write_bytes(b"a" * 11)
    newer = folder / "20260916_130000_200000_第二段.avi"
    newer.write_bytes(b"b" * 13)
    os.utime(older, (1000, 1000))
    os.utime(newer, (2000, 2000))
    (folder / "notes.txt").write_bytes(b"not a recording")

    service.start()
    service.start_recording("live")
    # The writer creates the file on its first frame; the library only reports
    # what is on disk, so the open recording is written here to stand in for it.
    live = Path(service.status()["recording_path"])
    live.write_bytes(b"partial")

    listed = service.list_recordings()
    assert [item["label"] for item in listed] == ["live", "第二段", "第一段"]
    assert [item["active"] for item in listed] == [True, False, False]
    assert listed[1]["recorded_at"] == "2026-09-16T13:00:00"

    with pytest.raises(RuntimeError, match="still being written"):
        service.delete_recording(live.name)

    cleared = service.delete_all_recordings()
    assert cleared["count"] == 2
    assert cleared["size_bytes"] == 24
    assert live.is_file()
    assert [item["label"] for item in service.list_recordings()] == ["live"]
    service.stop()
