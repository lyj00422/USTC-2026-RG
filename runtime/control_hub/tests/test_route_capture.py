import json
import zipfile

from control_hub.services.route_capture import RouteCaptureService


def test_capture_point_writes_title_directory_and_segment_distance(tmp_path):
    service = RouteCaptureService(tmp_path)
    result = service.capture_point(
        "Intersection 1",
        image=b"jpeg",
        chassis={"pose": {"forward_cm": 42, "right_cm": 3, "rotate_deg": 0}},
        line={"intersection": "cross", "line_error": 0.1},
        apriltags=[{"id": 7}],
    )
    assert result["title"] == "Intersection 1"
    assert result["segment_distance"] == {"forward_cm": 42, "right_cm": 3, "rotate_deg": 0}
    folder = tmp_path / "camera" / "intersection_1"
    assert (folder / "image.jpg").read_bytes() == b"jpeg"
    assert json.loads((folder / "metadata.json").read_text()) ["line_status"]["intersection"] == "cross"


def test_capture_point_preserves_velocity_motion_history(tmp_path):
    service = RouteCaptureService(tmp_path)
    service.capture_point(
        "turn",
        image=None,
        chassis={"pose": {"forward_cm": 0, "right_cm": 0, "rotate_deg": 0}, "motion_history": [{"duration_ms": 1200, "velocity": {"vx": 0, "vy": 0, "wz": 30}}]},
        line={},
        apriltags=[],
    )
    metadata = json.loads((tmp_path / "camera" / "turn" / "metadata.json").read_text())
    assert metadata["motion_history"][0]["duration_ms"] == 1200


def test_export_camera_zip_uses_title_directories(tmp_path):
    service = RouteCaptureService(tmp_path)
    service.capture_point("left turn", image=b"x", chassis={}, line={}, apriltags=[])
    archive = service.export("camera", tmp_path / "camera.zip")
    with zipfile.ZipFile(archive) as zf:
        assert "left_turn/image.jpg" in zf.namelist()
        assert "left_turn/metadata.json" in zf.namelist()
