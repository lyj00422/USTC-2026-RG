import io
import threading
from urllib.parse import quote
from urllib.request import urlopen

from control_hub.api import HubApplication
from control_hub.demo import DemoArmService, DemoCameraService
from control_hub.safety import HubSafety
from control_hub.server import build_components, build_parser, make_handler, write_file_body, write_mjpeg_part
from control_hub.services.event_log import EventLog
from control_hub.state import ControlLease, HubState
from http.server import ThreadingHTTPServer


def test_server_parser_supports_demo_and_port():
    args = build_parser().parse_args(["--demo", "--port", "9090"])
    assert args.demo
    assert args.port == 9090


def test_http_server_serves_home_and_status(tmp_path):
    static = tmp_path / "static"
    static.mkdir()
    (static / "index.html").write_text("control hub", encoding="utf-8")
    state = HubState()
    log = EventLog()
    lease = ControlLease(2000)
    arm = DemoArmService(state, log)
    camera = DemoCameraService(state, log, tmp_path)
    safety = HubSafety(arm, lease, log)
    app = HubApplication(state, lease, safety, arm, camera, log, static_root=static, clock_ms=lambda: 0)
    server = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(app, camera, log))
    worker = threading.Thread(target=server.serve_forever, daemon=True)
    worker.start()
    try:
        base = f"http://127.0.0.1:{server.server_port}"
        assert urlopen(base + "/", timeout=2).read() == b"control hub"
        assert b'"arm"' in urlopen(base + "/api/system/status", timeout=2).read()
    finally:
        server.shutdown()
        server.server_close()
        worker.join(timeout=2)


def test_mjpeg_writer_treats_client_abort_as_normal_disconnect():
    class AbortedStream:
        def write(self, _data):
            raise ConnectionAbortedError("browser closed stream")

        def flush(self):
            raise AssertionError("flush should not run after failed write")

    assert write_mjpeg_part(AbortedStream(), b"jpeg") is False


def test_file_downloads_stream_from_disk_and_survive_a_client_abort(tmp_path):
    path = tmp_path / "recording.avi"
    payload = b"x" * (1 << 17) + b"tail"
    path.write_bytes(payload)
    sink = io.BytesIO()
    assert write_file_body(sink, path) is True
    assert sink.getvalue() == payload

    class AbortedStream:
        def write(self, _data):
            raise BrokenPipeError("browser cancelled the download")

    assert write_file_body(AbortedStream(), path) is False


def test_http_server_serves_a_recording_as_a_streamed_attachment(tmp_path):
    static = tmp_path / "static"
    static.mkdir()
    state = HubState()
    log = EventLog()
    lease = ControlLease(2000)
    arm = DemoArmService(state, log)
    camera = DemoCameraService(state, log, tmp_path)
    name = "20260916_125545_100000_演示录像.avi"
    payload = b"RIFF" + b"x" * 200_000
    recordings = tmp_path / "recordings"
    recordings.mkdir()
    (recordings / name).write_bytes(payload)
    safety = HubSafety(arm, lease, log)
    app = HubApplication(state, lease, safety, arm, camera, log, static_root=static, clock_ms=lambda: 0)
    server = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(app, camera, log))
    worker = threading.Thread(target=server.serve_forever, daemon=True)
    worker.start()
    try:
        base = f"http://127.0.0.1:{server.server_port}"
        listed = urlopen(base + "/api/camera/recordings", timeout=5).read()
        assert name.encode() in listed
        response = urlopen(f"{base}/api/camera/recordings/file?name={quote(name)}", timeout=5)
        assert response.status == 200
        # Content-Length comes from the file on disk, and the body is the file.
        assert response.headers["Content-Length"] == str(len(payload))
        assert quote(name) in response.headers["Content-Disposition"]
        assert response.read() == payload
    finally:
        server.shutdown()
        server.server_close()
        worker.join(timeout=2)


def test_build_components_does_not_open_real_camera_until_start(monkeypatch):
    def unexpected_open(_service, _settings):
        raise AssertionError("camera device must not be opened during component construction")

    monkeypatch.setattr("control_hub.services.camera_service.CameraService._open_capture", unexpected_open)
    _config, _app, _arm, _chassis, line, camera, _safety, _log, _pickup, _alignment = build_components(
        "config/runtime.yaml", demo=False
    )

    assert camera.status()["running"] is False
    assert line.snapshot.connected is False
