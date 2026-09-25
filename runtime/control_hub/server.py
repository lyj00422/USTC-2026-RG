"""Threading HTTP server and production/demo control-hub composition."""

from __future__ import annotations

import argparse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import signal
import threading
import time
from urllib.parse import parse_qs, urlsplit

from rg_runtime.app_support import load_runtime_config
from rg_runtime.config import load_camera_config

from .api import HubApplication
from .demo import DemoArmService, DemoCameraService, DemoChassisService, DemoLineService
from .safety import HubSafety
from .services.arm_service import ArmService
from .services.camera_service import CameraService, CameraSettings
from .services.chassis_service import ChassisService
from .services.line_service import LineSensorService
from .services.event_log import EventLog
from .services.action_recorder import ActionRecorder
from .services.pickup_service import PickupService
from .services.vision_service import VisionService
from .services.calibration_bundle import CalibrationBundleRepository
from .services.route_capture import RouteCaptureService
from .services.alignment_controller import AlignmentController
from .state import ControlLease, HubState


RUNTIME_ROOT = Path(__file__).resolve().parents[1]
STATIC_ROOT = Path(__file__).resolve().parent / "static"


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="RoboGame browser debugging control hub")
    parser.add_argument("--config", default=str(RUNTIME_ROOT / "config/runtime.yaml"))
    parser.add_argument("--host")
    parser.add_argument("--port", type=int)
    parser.add_argument("--demo", action="store_true", help="use simulated arm and camera")
    return parser


def write_mjpeg_part(stream, jpeg: bytes) -> bool:
    part = b"--frame\r\nContent-Type: image/jpeg\r\n" + f"Content-Length: {len(jpeg)}\r\n\r\n".encode("ascii") + jpeg + b"\r\n"
    try:
        stream.write(part)
        stream.flush()
        return True
    except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
        return False


def write_file_body(stream, path, chunk_size: int = 1 << 16) -> bool:
    """Stream a file to the socket instead of handing over one big bytes object.

    Returns False when the client went away, which for a 33 MB recording the
    operator cancelled is normal rather than a fault.  Content-Length has
    already been sent by the caller, and the file was stat()ed to produce it.
    """
    try:
        with open(path, "rb") as handle:
            while True:
                chunk = handle.read(chunk_size)
                if not chunk:
                    return True
                stream.write(chunk)
    except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
        return False


def make_handler(app: HubApplication, camera_service, event_log):
    class ControlHubHandler(BaseHTTPRequestHandler):
        def log_message(self, _format, *_args):
            return

        def _send(self, response):
            self.send_response(response.status)
            self.send_header("Content-Type", response.content_type)
            length = response.file.stat().st_size if response.file is not None else len(response.body)
            self.send_header("Content-Length", str(length))
            self.send_header("Cache-Control", "no-store")
            for key, value in getattr(response, "headers", ()):
                self.send_header(key, value)
            self.end_headers()
            if response.file is not None:
                write_file_body(self.wfile, response.file)
                return
            self.wfile.write(response.body)

        def do_GET(self):
            path = urlsplit(self.path).path
            if path == "/api/camera/stream.mjpg":
                self._stream_camera()
                return
            if path == "/api/events":
                self._stream_events()
                return
            self._send(app.handle("GET", self.path, headers=dict(self.headers.items())))

        def do_POST(self):
            try:
                size = int(self.headers.get("Content-Length", "0"))
            except ValueError:
                size = 0
            if size > 1_000_000:
                self.send_error(413)
                return
            body = self.rfile.read(size) if size else b""
            self._send(app.handle("POST", self.path, body=body, headers=dict(self.headers.items())))

        def _stream_camera(self):
            self.send_response(200)
            self.send_header("Content-Type", "multipart/x-mixed-replace; boundary=frame")
            self.send_header("Cache-Control", "no-store")
            self.send_header("Connection", "close")
            self.end_headers()
            sequence = 0
            try:
                while True:
                    sequence_new, jpeg = camera_service.wait_for_jpeg(sequence, timeout_s=1.0)
                    if jpeg is None:
                        if not camera_service.status()["running"]:
                            return
                        continue
                    if sequence_new == sequence:
                        continue
                    sequence = sequence_new
                    if not write_mjpeg_part(self.wfile, jpeg):
                        return
            except (BrokenPipeError, ConnectionResetError):
                return

        def _stream_events(self):
            query = parse_qs(urlsplit(self.path).query)
            sequence = int(query.get("after", ["0"])[0])
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream; charset=utf-8")
            self.send_header("Cache-Control", "no-store")
            self.send_header("Connection", "keep-alive")
            self.end_headers()
            try:
                while True:
                    events = event_log.wait_after(sequence, timeout_s=10.0)
                    if not events:
                        self.wfile.write(b": keepalive\n\n")
                    for event in events:
                        sequence = event["sequence"]
                        payload = json.dumps(event, ensure_ascii=False).encode("utf-8")
                        self.wfile.write(b"data: " + payload + b"\n\n")
                    self.wfile.flush()
            except (BrokenPipeError, ConnectionResetError):
                return

    return ControlHubHandler


def _resolve_runtime_path(value: str, config_path: Path) -> Path:
    path = Path(value).expanduser()
    return path if path.is_absolute() else config_path.resolve().parent.parent / path


def build_components(config_path: str | Path, *, demo: bool):
    config_path = Path(config_path)
    config = load_runtime_config(config_path)
    state = HubState()
    log_path = _resolve_runtime_path(config.hub_log_path, config_path)
    event_log = EventLog(log_path)
    bundle = CalibrationBundleRepository(_resolve_runtime_path("data/calibration_bundle", config_path))
    capture = RouteCaptureService(_resolve_runtime_path("data/route_capture", config_path))
    recorder = ActionRecorder(_resolve_runtime_path("data/actions.json", config_path), repository=bundle, capture_service=capture)
    lease = ControlLease(config.control_lease_ms)
    output_dir = _resolve_runtime_path(config.camera_output_dir, config_path)
    line = LineSensorService(
        transport=config.line_transport,
        device=config.line_device,
        rx_gpio=config.line_rx_gpio,
        tx_gpio=config.line_tx_gpio,
        baudrate=config.line_baudrate,
        mode=config.line_frame_mode,
        active_level=config.line_active_level,
        reverse_order=config.line_reverse_order,
        enabled=config.line_enabled,
        request_command=config.line_request_command,
        startup_delay_s=config.line_startup_delay_s,
        request_retry_s=config.line_request_retry_s,
    )
    if demo:
        arm = DemoArmService(state, event_log)
        chassis = DemoChassisService(state, event_log)
        camera = DemoCameraService(state, event_log, output_dir)
        line = DemoLineService(state, event_log)
    else:
        arm = ArmService(
            state,
            event_log,
            probe_timeout_s=config.arm_probe_timeout_ms / 1000,
            action_timeout_s=config.arm_action_timeout_ms / 1000,
        )
        chassis = ChassisService(
            state,
            event_log,
            keepalive_enabled=config.chassis_keepalive_enabled,
            keepalive_s=config.chassis_keepalive_s,
            keepalive_quiet_s=config.chassis_keepalive_quiet_s,
            keepalive_log_every_s=config.chassis_keepalive_log_every_s,
        )
        camera_config = load_camera_config(_resolve_runtime_path(config.camera_config_path, config_path))
        settings = CameraSettings(camera_config.camera, camera_config.width, camera_config.height, camera_config.fps, camera_config.pixel_format)
        camera = CameraService(settings, output_dir, state, event_log)
    recorder.camera_service = camera
    recorder.chassis_service = chassis
    # A package records the whole robot's state at save time, not just the arm.
    recorder.arm_service = arm
    recorder.line_service = line
    # One package session per console run (see RouteCaptureService).
    capture.begin_package_session()
    vision = VisionService(camera)
    pickup = PickupService(camera, arm, event_log=event_log, background=True, vision_service=vision)
    alignment = AlignmentController(chassis, arm)
    safety = HubSafety(arm, lease, event_log, chassis_service=chassis)
    # The arm goes through its udev alias, exactly like the chassis below, and
    # for the same reason.  2026-09-18: this was hardcoded to "/dev/ttyUSB0",
    # the raw node, and the arm's USB re-enumerated ttyUSB0 -> ttyUSB1 at 14:20
    # while the console was running.  The alias followed (99-robogame-arm.rules
    # re-pointed /dev/robogame-arm at ttyUSB1) but the console kept looking for
    # ttyUSB0, found nothing, and could never connect the arm again -- while the
    # chassis, reading config.chassis_device, rode the same event out untouched.
    fixed_arm_device = "demo-arm" if demo else config.arm_device
    fixed_chassis_device = "demo-chassis" if demo else config.chassis_device
    app = HubApplication(state, lease, safety, arm, camera, event_log, pickup_service=pickup, vision_service=vision, chassis_service=chassis, line_service=line, recorder=recorder, bundle=bundle, route_capture=capture, alignment=alignment, fixed_arm_device=fixed_arm_device, fixed_chassis_device=fixed_chassis_device, static_root=STATIC_ROOT, clock_ms=lambda: time.monotonic_ns() // 1_000_000)
    # Everything logged from here on belongs to this console session; the
    # package export slices the JSONL at this sequence.
    app.session_log_sequence = event_log.sequence()
    return config, app, arm, chassis, line, camera, safety, event_log, pickup, alignment


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)
    config, app, arm, chassis, line, camera, safety, event_log, pickup, alignment = build_components(args.config, demo=args.demo)
    host = args.host or config.hub_host
    port = args.port or config.hub_port
    if not args.demo:
        # Camera capture is operator-controlled.  Do not open /dev/video0 at boot.
        event_log.append("ready", "camera", {"message": "camera is off until explicitly started"})
    line.start()
    server = ThreadingHTTPServer((host, port), make_handler(app, camera, event_log))
    watchdog_stop = threading.Event()

    def watchdog():
        while not watchdog_stop.wait(.2):
            safety.check_lease(now_ms=time.monotonic_ns() // 1_000_000)
            try:
                chassis.poll_once()
                # Idle keepalive: one read-only SPD every keepalive_s, skipped
                # whenever the operator just sent something.  A dropped JDY-31
                # session makes the maintainer service recreate /dev/rfcomm0,
                # which permanently breaks the tty this process is holding.
                chassis.keepalive_tick()
            except Exception:
                pass
            line.poll_once()

    worker = threading.Thread(target=watchdog, name="hub-safety-watchdog", daemon=True)
    worker.start()

    def _shutdown_on_signal(_signum, _frame):
        # The default SIGTERM action terminates the process without running the
        # finally block below, which leaks the pigpio claim on the line sensor:
        # every later program then fails with "GPIO already in use" until
        # pigpiod is restarted.  Turn it into the same path as Ctrl-C.
        raise KeyboardInterrupt

    signal.signal(signal.SIGTERM, _shutdown_on_signal)
    print(f"Control hub: http://{host}:{server.server_port}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        return 130
    finally:
        watchdog_stop.set()
        worker.join(timeout=1.0)
        safety.global_stop("server_shutdown")
        if chassis.connected:
            chassis.disconnect()
        line.close()
        if getattr(arm, "connected", False):
            arm.disconnect()
        camera.stop()
        server.server_close()
    return 0
