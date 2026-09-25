import json
import os
from pathlib import Path
from urllib.parse import quote

from control_hub.api import HubApplication
from control_hub.safety import HubSafety
from control_hub.services.event_log import EventLog
from control_hub.services.action_recorder import ActionRecorder
from control_hub.services.camera_service import RecordingLibrary
from control_hub.services.route_capture import RouteCaptureService
from control_hub.state import ControlLease, HubState
from control_hub.services.pickup_service import PickupConfig

class FakeArm:
    def __init__(self):
        self.connected = True
        self.enabled = 0
        self.stops = 0

    def ports(self):
        return [{"device": "/dev/ttyUSB0", "is_ch340": True}]

    def status(self):
        return {"connected": self.connected, "mode": "LOCKED", "calibrated": True}

    def connect(self, device, baudrate):
        self.connected = True
        return self.status()

    def disconnect(self):
        self.connected = False
        return self.status()

    def probe(self):
        return self.status()

    def enable(self):
        self.enabled += 1
        return self.status()

    def run(self, routine):
        return {**self.status(), "routine": routine}

    def suction(self, enabled):
        return {**self.status(), "suction_commanded": enabled}

    def servo(self, servo_id, position, time_ms):
        return {**self.status(), "servo": {"id": servo_id, "position": position, "time_ms": time_ms}}

    def move(self, positions, time_ms):
        return {**self.status(), "move": {"positions": positions, "time_ms": time_ms}}

    def stop(self):
        self.stops += 1
        return self.status()

class FakeChassis:
    def __init__(self):
        self.connected = True
        self.stops = 0
        self.velocities = []
        self.distances = []
        self.reset_distance_calls = 0
        self.reset_encoder_calls = 0
        self.motion_history = []

    def ports(self):
        return [{"device": "/dev/rfcomm0", "description": "JDY-31", "is_chassis": True}]

    def status(self):
        return {
            "connected": self.connected,
            "device": "/dev/rfcomm0" if self.connected else None,
            "baudrate": 9600 if self.connected else None,
            "state": "CONNECTED" if self.connected else "DISCONNECTED",
            "velocity": {"vx": 0, "vy": 0, "wz": 0},
            "distance": {"forward_cm": 0, "right_cm": 0},
            "pose": {"forward_cm": 0, "right_cm": 0, "rotate_deg": 0},
            "motion_history": list(self.motion_history),
            "last_reply": None,
            "error": None,
        }

    def connect(self, device, baudrate):
        self.connected = True
        return self.status()

    def disconnect(self):
        self.connected = False
        return self.status()

    def set_velocity(self, vx, vy, wz):
        self.velocities.append((vx, vy, wz))
        return self.status()

    def run_distance(self, forward_cm, right_cm, rotate_deg, speed):
        self.distances.append((forward_cm, right_cm, rotate_deg, speed))
        return {**self.status(), "command": {"forward_cm": forward_cm, "right_cm": right_cm, "rotate_deg": rotate_deg, "speed": speed}}

    def stop(self):
        self.stops += 1
        return self.status()

    def run_sequence(self):
        return self.status()

    def request_encoder(self):
        return self.status()

    def request_speed(self):
        return self.status()

    def motor_test(self, wheel, speed):
        return self.status()

    def reset_encoder(self):
        self.reset_encoder_calls += 1
        return self.status()

    def reset_distance(self):
        self.reset_distance_calls += 1
        self.motion_history = []
        return self.status()


class FakeCamera(RecordingLibrary):
    def __init__(self, running=True, output_dir=".", active_path=None):
        self.running = running
        self.recording = False
        # The recordings library is real even here: the listing, the filename
        # guard and the cleanup are the code under test, not a stub of it.
        self.output_dir = Path(output_dir)
        self.event_log = EventLog()
        self.active_path = Path(active_path).resolve() if active_path else None

    def _active_recording_path(self):
        return self.active_path

    def status(self):
        return {"running": self.running, "frame_available": self.running, "recording": self.recording}

    def start(self):
        self.running = True
        return self.status()

    def stop(self):
        self.running = False
        return self.status()

    def snapshot(self):
        return Path("snapshot.jpg")

    def start_recording(self, label):
        self.recording = True
        return {**self.status(), "recording": True, "label": label}

    def stop_recording(self):
        self.recording = False
        return {"path": "recording.avi", "frames": 3}


class FakePickup:
    def __init__(self):
        self.config = PickupConfig()
        self.started = []
        self.stopped = 0
        self.active = False

    def status(self):
        return {"state": "SEARCHING" if self.active else "IDLE", "target": None, "error": None, "config": {"preferred_color": "purple"}}

    def start(self, config):
        self.started.append(config)
        self.active = True
        return {"state": "SEARCHING", "target": None, "error": None}

    def stop(self):
        self.stopped += 1
        self.active = False
        return {"state": "IDLE", "target": None, "error": None}


def make_app(static_root, pickup=None):
    state = HubState()
    lease = ControlLease(2000, token_factory=lambda: "token")
    log = EventLog()
    arm = FakeArm()
    chassis = FakeChassis()
    camera = FakeCamera()
    safety = HubSafety(arm, lease, log, chassis_service=chassis)
    return HubApplication(state, lease, safety, arm, camera, log, pickup_service=pickup, chassis_service=chassis, static_root=static_root, clock_ms=lambda: 100), arm, chassis


def make_app_with_camera(static_root, camera, pickup=None):
    state = HubState()
    lease = ControlLease(2000, token_factory=lambda: "token")
    log = EventLog()
    arm = FakeArm()
    chassis = FakeChassis()
    safety = HubSafety(arm, lease, log, chassis_service=chassis)
    return HubApplication(state, lease, safety, arm, camera, log, pickup_service=pickup, chassis_service=chassis, static_root=static_root, clock_ms=lambda: 100)


def body(response):
    return json.loads(response.body.decode("utf-8"))


def test_modules_and_arm_status_are_readable_without_lease(tmp_path):
    app, _, _ = make_app(tmp_path)
    response = app.handle("GET", "/api/system/modules")
    assert response.status == 200
    assert any(item["key"] == "arm" for item in body(response)["modules"])
    assert body(app.handle("GET", "/api/arm/status"))["mode"] == "LOCKED"


def test_auto_connect_rejects_arm_when_fixed_ch340_port_is_missing(tmp_path):
    app, arm, _ = make_app(tmp_path)
    arm.connected = False
    arm.ports = lambda: [{"device": "/dev/ttyUSB0", "is_ch340": False}]
    token = body(app.handle("POST", "/api/control/acquire", body=b'{"owner":"test"}'))["token"]

    response = app.handle("POST", "/api/devices/auto-connect", body=b"{}", headers={"X-Control-Token": token})

    assert response.status == 409
    assert arm.connected is False


def test_system_status_aggregates_all_operator_modules(tmp_path):
    app, _, _ = make_app(tmp_path)
    payload = body(app.handle("GET", "/api/system/status"))
    assert payload["ok"] is True
    assert set(("arm", "camera", "chassis", "line", "safety", "lease")).issubset(payload)
    assert payload["line"]["state"] == "UNAVAILABLE" or "connected" in payload["line"]


def test_arm_mutation_requires_control_lease(tmp_path):
    app, arm, _ = make_app(tmp_path)
    denied = app.handle("POST", "/api/arm/enable", body=b"{}")
    assert denied.status == 409
    acquired = body(app.handle("POST", "/api/control/acquire", body=b'{"owner":"test"}'))
    allowed = app.handle("POST", "/api/arm/enable", body=b"{}", headers={"X-Control-Token": acquired["token"]})
    assert allowed.status == 200
    assert arm.enabled == 1


def test_arm_manual_pose_and_action_library_are_available_with_lease(tmp_path):
    app, _, _ = make_app(tmp_path)
    acquired = body(app.handle("POST", "/api/control/acquire", body=b'{"owner":"test"}'))
    headers = {"X-Control-Token": acquired["token"]}
    app.handle("POST", "/api/arm/enable", body=b"{}", headers=headers)
    assert app.handle("POST", "/api/arm/servo", body=b'{"id":2,"position":1650,"time_ms":3000}', headers=headers).status == 200
    assert app.handle("POST", "/api/arm/move", body=b'{"positions":[1500,1500,1500,1500,1500]}', headers=headers).status == 200


def test_arm_api_rejects_coerced_suction_command(tmp_path):
    app, _, _ = make_app(tmp_path)
    token = body(app.handle("POST", "/api/control/acquire", body=b'{"owner":"test"}'))["token"]
    headers = {"X-Control-Token": token}
    assert app.handle("POST", "/api/arm/suction", body=b'{"enabled":"false"}', headers=headers).status == 400


def test_global_stop_is_always_available(tmp_path):
    app, arm, chassis = make_app(tmp_path)
    response = app.handle("POST", "/api/safety/stop", body=b'{"reason":"operator"}')
    assert response.status == 200
    assert arm.stops == 1
    assert chassis.stops == 1


def test_latched_global_stop_blocks_motion_until_fixed_auto_connect(tmp_path):
    app, _, chassis = make_app(tmp_path)
    token = body(app.handle("POST", "/api/control/acquire", body=b'{"owner":"test"}'))["token"]
    headers = {"X-Control-Token": token}

    assert app.handle("POST", "/api/safety/stop", body=b'{"reason":"operator"}').status == 200
    blocked = app.handle("POST", "/api/chassis/velocity", body=b'{"vx":20,"vy":0,"wz":0}', headers=headers)

    assert blocked.status == 409
    assert chassis.velocities == []


def test_manual_connect_cannot_bypass_fixed_device_contract(tmp_path):
    app, arm, chassis = make_app(tmp_path)
    token = body(app.handle("POST", "/api/control/acquire", body=b'{"owner":"test"}'))["token"]
    headers = {"X-Control-Token": token}
    arm.connected = False
    chassis.connected = False

    arm_result = app.handle("POST", "/api/arm/connect", body=b'{"device":"/dev/ttyUSB1"}', headers=headers)
    chassis_result = app.handle("POST", "/api/chassis/connect", body=b'{"device":"/dev/rfcomm0"}', headers=headers)

    assert arm_result.status == 409
    assert chassis_result.status == 409
    assert arm.connected is False
    assert chassis.connected is False


def test_fixed_auto_connect_clears_stop_latch_without_unlock_endpoint(tmp_path):
    app, arm, chassis = make_app(tmp_path)
    chassis.ports = lambda: [{"device": "/dev/robogame-chassis", "is_chassis": True}]
    token = body(app.handle("POST", "/api/control/acquire", body=b'{"owner":"test"}'))["token"]
    headers = {"X-Control-Token": token}
    app.handle("POST", "/api/safety/stop", body=b'{"reason":"operator"}')

    response = app.handle("POST", "/api/devices/auto-connect", body=b"{}", headers=headers)

    assert response.status == 200
    assert app.safety.latched is False


def test_chassis_mutation_requires_control_lease_but_stop_does_not(tmp_path):
    app, _, chassis = make_app(tmp_path)
    denied = app.handle("POST", "/api/chassis/velocity", body=b'{"vx":20,"vy":0,"wz":0}')
    assert denied.status == 409
    stopped = app.handle("POST", "/api/chassis/stop", body=b"{}")
    assert stopped.status == 200
    assert chassis.stops == 1
    token = body(app.handle("POST", "/api/control/acquire", body=b'{"owner":"test"}'))["token"]
    headers = {"X-Control-Token": token}
    moved = app.handle("POST", "/api/chassis/velocity", body=b'{"vx":20,"vy":0,"wz":0}', headers=headers)
    assert moved.status == 200
    assert chassis.velocities == [(20, 0, 0)]


def test_chassis_status_and_ports_are_readable_without_lease(tmp_path):
    app, _, _ = make_app(tmp_path)
    assert body(app.handle("GET", "/api/chassis/status"))["connected"] is True
    assert body(app.handle("GET", "/api/chassis/ports"))["ports"][0]["is_chassis"] is True


def test_route_capture_persists_motion_history_and_uses_software_reset(tmp_path):
    app, _, chassis = make_app(tmp_path)
    app.route_capture = RouteCaptureService(tmp_path / "route_capture")
    chassis.motion_history = [{
        "velocity": {"vx": 20, "vy": 0, "wz": 0},
        "duration_ms": 2500,
        "command_integral": {"vx_ms": 50000, "vy_ms": 0, "wz_ms": 0},
    }]
    token = body(app.handle("POST", "/api/control/acquire", body=b'{"owner":"test"}'))["token"]
    response = app.handle(
        "POST",
        "/api/route-capture/capture",
        body=b'{"title":"point-1"}',
        headers={"X-Control-Token": token},
    )

    assert response.status == 200
    payload = body(response)
    assert payload["point"]["motion_history"][0]["duration_ms"] == 2500
    assert chassis.reset_distance_calls == 1
    assert chassis.reset_encoder_calls == 0
    metadata = json.loads((tmp_path / "route_capture" / "camera" / "point-1" / "metadata.json").read_text())
    assert metadata["motion_history"][0]["command_integral"]["vx_ms"] == 50000


def test_chassis_distance_command_requires_lease_and_formats_values(tmp_path):
    app, _, chassis = make_app(tmp_path)
    denied = app.handle("POST", "/api/chassis/distance", body=b'{"forward_cm":10,"right_cm":0,"rotate_deg":0,"speed":80}')
    assert denied.status == 409
    token = body(app.handle("POST", "/api/control/acquire", body=b'{"owner":"test"}'))["token"]
    response = app.handle(
        "POST",
        "/api/chassis/distance",
        body=b'{"forward_cm":10,"right_cm":0,"rotate_deg":0,"speed":80}',
        headers={"X-Control-Token": token},
    )
    assert response.status == 200
    assert chassis.distances == [(10, 0, 0, 80)]


def test_chassis_diagnostics_and_sequence_require_lease(tmp_path):
    app, _, chassis = make_app(tmp_path)
    assert app.handle("POST", "/api/chassis/sequence", body=b"{}").status == 409
    token = body(app.handle("POST", "/api/control/acquire", body=b'{"owner":"test"}'))["token"]
    headers = {"X-Control-Token": token}
    assert app.handle("POST", "/api/chassis/sequence", body=b"{}", headers=headers).status == 200
    assert app.handle("POST", "/api/chassis/encoder", body=b"{}", headers=headers).status == 200
    assert app.handle("POST", "/api/chassis/speed", body=b"{}", headers=headers).status == 200


def test_chassis_motor_and_encoder_reset_api_require_lease(tmp_path):
    app, _, _ = make_app(tmp_path)
    token = body(app.handle("POST", "/api/control/acquire", body=b'{"owner":"test"}'))["token"]
    headers = {"X-Control-Token": token}
    assert app.handle("POST", "/api/chassis/motor", body=b'{"wheel":"LF","speed":-20}', headers=headers).status == 200
    assert app.handle("POST", "/api/chassis/encoder/reset", body=b"{}", headers=headers).status == 200


def test_static_path_cannot_escape_root(tmp_path):
    (tmp_path / "index.html").write_text("hub", encoding="utf-8")
    app, _, _ = make_app(tmp_path)
    assert app.handle("GET", "/").status == 200
    assert app.handle("GET", "/../../config/runtime.yaml").status == 404


def test_pickup_api_requires_lease_and_accepts_tunable_preference(tmp_path):
    pickup = FakePickup()
    app, _, _ = make_app(tmp_path, pickup)
    assert body(app.handle("GET", "/api/vision/pickup/status"))["state"] == "IDLE"
    denied = app.handle("POST", "/api/vision/pickup/start", body=b'{"preferred_color":"purple"}')
    assert denied.status == 409
    token = body(app.handle("POST", "/api/control/acquire", body=b'{"owner":"test"}'))["token"]
    headers = {"X-Control-Token": token}
    started = app.handle(
        "POST",
        "/api/vision/pickup/start",
        body=b'{"preferred_color":"orange","min_confidence":0.7,"confirm_frames":4}',
        headers=headers,
    )
    assert started.status == 200
    assert pickup.started[-1].preferred_color.value == "orange"
    assert pickup.started[-1].min_confidence == 0.7
    assert pickup.started[-1].confirm_frames == 4
    assert app.handle("POST", "/api/vision/pickup/stop", body=b"{}", headers=headers).status == 200
    assert pickup.stopped == 1


def test_camera_dependent_operations_require_explicit_camera_start(tmp_path):
    camera = FakeCamera(running=False)
    pickup = FakePickup()
    app = make_app_with_camera(tmp_path, camera, pickup)
    token = body(app.handle("POST", "/api/control/acquire", body=b'{"owner":"test"}'))["token"]
    headers = {"X-Control-Token": token}

    assert app.handle("POST", "/api/camera/snapshot", body=b"{}", headers=headers).status == 409
    assert app.handle("POST", "/api/camera/record/start", body=b'{"label":"test"}', headers=headers).status == 409
    assert app.handle("POST", "/api/vision/pickup/start", body=b"{}", headers=headers).status == 409
    assert pickup.started == []


def test_arm_recording_stop_writes_one_named_capture_package(tmp_path):
    app, _, _ = make_app(tmp_path)
    app.camera.latest_snapshot = lambda: {"jpeg": b"final-frame"}
    snapshot_calls = []
    app.camera.snapshot = lambda: snapshot_calls.append(1)
    app.recorder = ActionRecorder(
        tmp_path / "actions.json",
        capture_service=RouteCaptureService(tmp_path / "route_capture"),
        camera_service=app.camera,
        chassis_service=app.chassis,
    )
    token = body(app.handle("POST", "/api/control/acquire", body=b'{"owner":"test"}'))["token"]
    headers = {"X-Control-Token": token}

    assert app.handle("POST", "/api/arm/actions/start", body=b'{"name":"arm-pickup"}', headers=headers).status == 200
    assert app.handle(
        "POST",
        "/api/arm/actions/zone",
        body=b'{"name":"capture","rect":{"x":10,"y":20,"width":100,"height":80}}',
        headers=headers,
    ).status == 200
    # Dragging the grab window only records the rectangle; the automatic photo
    # belongs to saving the package.
    assert snapshot_calls == []
    finished = body(app.handle("POST", "/api/arm/actions/stop", body=b"{}", headers=headers))

    assert finished["saved"]["name"] == "arm-pickup"
    assert snapshot_calls == [1]
    package = tmp_path / "route_capture" / "arm" / finished["saved"]["session_id"] / "0001_arm-pickup"
    assert (package / "camera.jpg").read_bytes() == b"final-frame"
    assert (package / "zones.json").is_file()
    assert (package / "chassis.json").is_file()


def test_console_save_flow_records_first_and_asks_for_the_name_second(tmp_path):
    app, _, _ = make_app(tmp_path)
    app.camera.latest_snapshot = lambda: {"jpeg": b"frame"}
    app.camera.snapshot = lambda: None
    app.recorder = ActionRecorder(
        tmp_path / "actions.json",
        capture_service=RouteCaptureService(tmp_path / "route_capture"),
        camera_service=app.camera,
    )
    token = body(app.handle("POST", "/api/control/acquire", body=b'{"owner":"test"}'))["token"]
    headers = {"X-Control-Token": token}

    started = body(app.handle("POST", "/api/arm/recording", body=b'{"enabled":true}', headers=headers))
    assert started["recording"] is True
    assert app.handle("POST", "/api/arm/run", body=b'{"routine":3}', headers=headers).status == 200

    stopped = body(app.handle("POST", "/api/arm/recording", body=b'{"enabled":false}', headers=headers))
    assert "saved" not in stopped
    assert stopped["draft"]["name"].startswith("动作包_")
    assert stopped["draft"]["steps"][0]["kind"] == "RUN"

    named = body(app.handle("POST", "/api/arm/actions/confirm", body='{"title":"放块"}'.encode(), headers=headers))
    assert named["saved"]["name"] == "放块"
    assert named["draft"] is None


def test_action_packages_are_listed_and_exported_as_one_zip(tmp_path):
    import io
    import zipfile

    app, _, _ = make_app(tmp_path)
    app.camera.latest_snapshot = lambda: {"jpeg": b"frame"}
    app.camera.snapshot = lambda: None
    capture = RouteCaptureService(tmp_path / "route_capture")
    app.route_capture = capture
    app.recorder = ActionRecorder(tmp_path / "actions.json", capture_service=capture, camera_service=app.camera)
    token = body(app.handle("POST", "/api/control/acquire", body=b'{"owner":"test"}'))["token"]
    headers = {"X-Control-Token": token}
    app.handle("POST", "/api/arm/actions/start", body='{"name":"甲"}'.encode(), headers=headers)
    app.handle("POST", "/api/arm/actions/stop", body=b"{}", headers=headers)

    listed = body(app.handle("GET", "/api/arm/packages"))
    assert listed["count"] == 1
    assert listed["history_count"] == 1
    assert listed["packages"][0]["name"] == "甲"

    response = app.handle("GET", "/api/arm/packages/export?scope=session")
    assert response.status == 200
    assert response.content_type == "application/zip"
    # The archive is handed over as a file for the handler to stream, so the
    # bytes come from payload() rather than from a body held in memory.
    assert response.body == b""
    with zipfile.ZipFile(io.BytesIO(response.payload())) as archive:
        names = archive.namelist()
    assert "manifest.json" in names
    assert any(name.endswith("0001_甲/action.json") for name in names)
    assert "data/actions.json" in names


def test_camera_stop_stops_active_pickup_before_releasing_camera(tmp_path):
    camera = FakeCamera(running=True)
    pickup = FakePickup()
    app = make_app_with_camera(tmp_path, camera, pickup)
    token = body(app.handle("POST", "/api/control/acquire", body=b'{"owner":"test"}'))["token"]
    headers = {"X-Control-Token": token}
    app.handle("POST", "/api/vision/pickup/start", body=b"{}", headers=headers)

    assert app.handle("POST", "/api/camera/stop", body=b"{}", headers=headers).status == 200
    assert pickup.stopped == 1
    assert camera.running is False


def make_recording(directory, name, payload=b"avi-bytes", mtime=None):
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / name
    path.write_bytes(payload)
    # Explicit times, so "newest first" is decided by the fixture and not by how
    # fast two writes happened to land.
    if mtime is not None:
        os.utime(path, (mtime, mtime))
    return path


def test_recordings_are_listed_newest_first_with_their_label_and_size(tmp_path):
    app = make_app_with_camera(tmp_path, FakeCamera(output_dir=tmp_path))
    folder = tmp_path / "recordings"
    make_recording(folder, "20260916_125545_100000_第一次跑.avi", b"a" * 10, mtime=1000)
    make_recording(folder, "20260916_130000_200000_第二次跑.avi", b"b" * 20, mtime=2000)
    make_recording(folder, "notes.txt", b"not a recording", mtime=3000)

    payload = body(app.handle("GET", "/api/camera/recordings"))
    assert payload["count"] == 2
    assert payload["total_bytes"] == 30
    assert [item["label"] for item in payload["recordings"]] == ["第二次跑", "第一次跑"]
    assert payload["recordings"][0]["recorded_at"] == "2026-09-16T13:00:00"
    assert payload["recordings"][0]["size_bytes"] == 20
    assert payload["recordings"][0]["active"] is False


def test_a_recording_downloads_as_an_attachment_named_after_it(tmp_path):
    app = make_app_with_camera(tmp_path, FakeCamera(output_dir=tmp_path))
    name = "20260916_125545_100000_第一次跑.avi"
    path = make_recording(tmp_path / "recordings", name)

    response = app.handle("GET", f"/api/camera/recordings/file?name={quote(name)}")
    assert response.status == 200
    # The exact alias for .avi differs by platform (video/x-msvideo on the Pi,
    # video/avi on Windows); what matters is that it is served as video, and
    # that the download is an attachment either way.
    assert response.content_type.startswith("video/")
    # Streamed from disk: the recording is never held in memory as one buffer.
    assert response.body == b""
    assert response.file == path
    assert response.file.read_bytes() == b"avi-bytes"
    disposition = dict(response.headers)["Content-Disposition"]
    assert disposition.startswith("attachment")
    assert quote(name) in disposition


def test_a_recording_name_cannot_reach_outside_the_recordings_folder(tmp_path):
    app = make_app_with_camera(tmp_path, FakeCamera(output_dir=tmp_path))
    folder = tmp_path / "recordings"
    make_recording(folder, "20260916_125545_100000_run.avi")
    make_recording(folder / "nested", "inner.avi")
    make_recording(folder, "notes.txt", b"not a recording")
    make_recording(tmp_path, "outside.avi", b"not yours")

    for name in ["../outside.avi", "nested/inner.avi", r"..\outside.avi", "notes.txt", "missing.avi", ""]:
        response = app.handle("GET", f"/api/camera/recordings/file?name={quote(name)}")
        assert response.status == 404, name


def test_the_recording_being_written_is_neither_downloadable_nor_deletable(tmp_path):
    live = make_recording(tmp_path / "recordings", "20260916_125545_100000_进行中.avi", b"partial")
    app = make_app_with_camera(tmp_path, FakeCamera(output_dir=tmp_path, active_path=live))
    token = body(app.handle("POST", "/api/control/acquire", body=b'{"owner":"test"}'))["token"]
    headers = {"X-Control-Token": token}

    listed = body(app.handle("GET", "/api/camera/recordings"))
    assert [(item["label"], item["active"]) for item in listed["recordings"]] == [("进行中", True)]

    rejected = app.handle("POST", "/api/camera/recordings/delete", body=json.dumps({"name": live.name}).encode(), headers=headers)
    assert rejected.status == 409
    assert live.is_file()


def test_deleting_a_recording_needs_the_lease_and_then_removes_it(tmp_path):
    path = make_recording(tmp_path / "recordings", "20260916_125545_100000_旧.avi", b"a" * 9)
    app = make_app_with_camera(tmp_path, FakeCamera(output_dir=tmp_path))
    payload = json.dumps({"name": path.name}).encode()

    assert app.handle("POST", "/api/camera/recordings/delete", body=payload).status == 409
    assert path.is_file()

    token = body(app.handle("POST", "/api/control/acquire", body=b'{"owner":"test"}'))["token"]
    response = app.handle("POST", "/api/camera/recordings/delete", body=payload, headers={"X-Control-Token": token})
    assert response.status == 200
    assert body(response)["size_bytes"] == 9
    assert not path.is_file()
    assert body(app.handle("GET", "/api/camera/recordings"))["count"] == 0


def test_clearing_every_recording_keeps_the_one_being_written(tmp_path):
    folder = tmp_path / "recordings"
    finished = make_recording(folder, "20260916_125545_100000_旧.avi", b"a" * 5)
    live = make_recording(folder, "20260916_130000_200000_录制中.avi", b"b" * 7)
    app = make_app_with_camera(tmp_path, FakeCamera(output_dir=tmp_path, active_path=live))
    token = body(app.handle("POST", "/api/control/acquire", body=b'{"owner":"test"}'))["token"]
    headers = {"X-Control-Token": token}

    # Without the explicit flag nothing is removed, so a stray POST is harmless.
    assert app.handle("POST", "/api/camera/recordings/delete-all", body=b"{}", headers=headers).status == 400
    assert finished.is_file()

    cleared = body(app.handle("POST", "/api/camera/recordings/delete-all", body=b'{"confirm":true}', headers=headers))
    assert cleared["count"] == 1
    assert cleared["size_bytes"] == 5
    assert not finished.is_file()
    assert live.is_file()


def test_every_recording_packages_into_one_stored_zip(tmp_path):
    import zipfile

    folder = tmp_path / "recordings"
    first = "20260916_125545_100000_第一段.avi"
    second = "20260916_130000_200000_第二段.avi"
    make_recording(folder, first, b"a" * 11, mtime=1000)
    make_recording(folder, second, b"b" * 13, mtime=2000)
    live = make_recording(folder, "20260916_131500_300000_录制中.avi", b"c", mtime=3000)
    app = make_app_with_camera(tmp_path, FakeCamera(output_dir=tmp_path, active_path=live))

    response = app.handle("GET", "/api/camera/recordings/export")
    assert response.status == 200
    assert response.content_type == "application/zip"
    assert response.body == b""
    with zipfile.ZipFile(response.file) as archive:
        assert sorted(archive.namelist()) == sorted([first, second])
        # Stored, not deflated: the frames are already JPEG-compressed.
        info = archive.getinfo(first)
        assert info.compress_type == zipfile.ZIP_STORED
        assert info.file_size == 11
        # These fixtures are stamped before 1980 on purpose -- that is a Pi that
        # booted with no clock -- and zipfile would refuse them unclamped.
        assert info.date_time[0] == 1980
    # The archive is built outside the recordings folder, so it never appears in
    # the listing it was made from.
    assert body(app.handle("GET", "/api/camera/recordings"))["count"] == 3


def test_packaging_nothing_is_not_an_empty_download(tmp_path):
    app = make_app_with_camera(tmp_path, FakeCamera(output_dir=tmp_path))
    (tmp_path / "recordings").mkdir()
    assert app.handle("GET", "/api/camera/recordings/export").status == 404
