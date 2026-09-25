import json
from control_hub.services.action_recorder import ActionRecorder


class FakeCamera:
    def __init__(self):
        self.snapshots = 0

    def latest_snapshot(self):
        return {"jpeg": b"jpeg-bytes", "path": "/tmp/latest.jpg"}

    def snapshot(self):
        self.snapshots += 1
        return "/tmp/latest.jpg"


class FakeChassis:
    def status(self):
        return {"connected": True, "distance_cm": 12.5, "motion_history": [{"duration_ms": 900, "velocity": {"vx": 20, "vy": 0, "wz": 0}}]}


class FakeArm:
    def status(self):
        return {"connected": True, "mode": "READY", "servo_targets": {"2": {"position": 1600}}}


class FakeLine:
    def status(self):
        return {"connected": True, "state": "FOLLOWING", "line_error": 0.1}


class FakeCapture:
    def __init__(self, root):
        from control_hub.services.route_capture import RouteCaptureService
        self.service = RouteCaptureService(root)

    def save_arm(self, action, **kwargs):
        return self.service.save_arm(action, **kwargs)


def make_recorder(tmp_path, capture=None, camera=None):
    from control_hub.services.route_capture import RouteCaptureService
    capture = capture or FakeCapture(tmp_path / "capture")
    return ActionRecorder(
        tmp_path / "actions.json",
        capture_service=capture,
        camera_service=camera or FakeCamera(),
        chassis_service=FakeChassis(),
        arm_service=FakeArm(),
        line_service=FakeLine(),
    ), capture


def test_finish_automatically_saves_action_photo_zones_and_chassis_as_one_named_package(tmp_path):
    recorder, capture = make_recorder(tmp_path)
    recorder.start("抓取1")
    recorder.append("SERVO", {"id": 2, "position": 1600, "time_ms": 500})
    recorder.save_zone("purple", {"x": 10, "y": 20, "width": 100, "height": 80}, "snap.jpg")
    result = recorder.finish()
    assert result["saved"]["name"] == "抓取1"
    assert result["draft"] is None

    session_id = result["saved"]["session_id"]
    package = tmp_path / "capture" / "arm" / session_id / "0001_抓取1"
    assert (package / "action.json").is_file()
    assert (package / "camera.jpg").read_bytes() == b"jpeg-bytes"
    assert json.loads((package / "chassis.json").read_text(encoding="utf-8"))["distance_cm"] == 12.5
    assert json.loads((package / "zones.json").read_text(encoding="utf-8"))["purple"]["width"] == 100
    assert json.loads((package / "arm.json").read_text(encoding="utf-8"))["mode"] == "READY"
    metadata = json.loads((package / "metadata.json").read_text(encoding="utf-8"))
    assert metadata["session_id"] == session_id
    assert metadata["index"] == 1
    assert metadata["line_status"]["state"] == "FOLLOWING"
    assert recorder.export()["actions"][0]["name"] == "抓取1"


def test_the_package_also_leaves_a_photo_in_the_snapshot_folder(tmp_path):
    camera = FakeCamera()
    recorder, _ = make_recorder(tmp_path, camera=camera)
    recorder.start("拍照")
    recorder.finish()
    assert camera.snapshots == 1


def test_unnamed_finish_holds_a_draft_until_the_operator_names_it(tmp_path):
    recorder, capture = make_recorder(tmp_path)
    recorder.start()
    recorder.append("SERVO", {"id": 0, "position": 2330, "time_ms": 500})
    result = recorder.finish()

    assert "saved" not in result
    assert result["draft"]["name"].startswith("动作包_")
    # Nothing may reach the package folder before the operator answers.
    assert recorder.actions() == []
    assert capture.service.package_folders() == []

    recorder.set_draft_name("放块")
    saved = recorder.confirm_draft()["saved"]
    assert saved["name"] == "放块"
    assert saved["package_index"] == 1
    assert (tmp_path / "actions.json").is_file()


def test_a_pending_draft_must_be_named_or_discarded_before_recording_again(tmp_path):
    import pytest
    recorder, _ = make_recorder(tmp_path)
    recorder.start()
    recorder.finish()
    with pytest.raises(RuntimeError):
        recorder.start()
    recorder.discard_draft()
    assert recorder.status()["draft"] is None
    assert recorder.start()["recording"] is True


def test_repeated_package_names_do_not_overwrite_each_other(tmp_path):
    recorder, capture = make_recorder(tmp_path)
    for _ in range(2):
        recorder.start("抓取")
        recorder.finish()

    folders = sorted(folder.name for folder in capture.service.package_folders())
    assert folders == ["0001_抓取", "0002_抓取"]


def test_finish_keeps_draft_when_automatic_package_save_fails(tmp_path):
    class FailingCapture:
        def save_arm(self, *_args, **_kwargs):
            raise OSError("storage unavailable")

    recorder = ActionRecorder(tmp_path / "actions.json", capture_service=FailingCapture())
    recorder.start("retry-me")

    try:
        recorder.finish()
    except OSError as error:
        assert str(error) == "storage unavailable"
    else:
        raise AssertionError("automatic package save must report its write failure")

    assert recorder.status()["draft"]["name"] == "retry-me"
    assert recorder.actions() == []


def test_draft_can_be_discarded(tmp_path):
    recorder = ActionRecorder(tmp_path / "actions.json")
    recorder.start("discard")
    recorder.finish()
    assert recorder.discard_draft()["draft"] is None
    assert recorder.actions()[0]["name"] == "discard"
