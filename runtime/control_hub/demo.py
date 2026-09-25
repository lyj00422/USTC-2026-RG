"""Non-hardware demo services for browser layout and API verification."""

from __future__ import annotations

from pathlib import Path
import threading
import time

import cv2
import numpy as np

from .services.camera_service import RecordingLibrary


class DemoChassisService:
    """Non-hardware chassis service used by the browser demo."""

    def __init__(self, hub_state, event_log) -> None:
        self.hub_state = hub_state
        self.event_log = event_log
        self.connected = False
        self.device = None
        self.baudrate = None
        self.state = "DISCONNECTED"
        self.velocity = {"vx": 0, "vy": 0, "wz": 0}
        self.command = None
        self.error = None
        self.last_reply = None
        self.distance = {"forward_cm": 0, "right_cm": 0}
        self.rotate_deg = 0
        self._sync()

    def ports(self):
        return [{"device": "demo-chassis", "description": "Simulated JDY-31", "is_chassis": True}]

    def connect(self, device, baudrate=9600):
        self.connected = True
        self.device = device
        self.baudrate = baudrate
        self.state = "CONNECTED"
        self.error = None
        self._sync()
        self.event_log.append("connected", "chassis", {"device": device, "baudrate": baudrate, "demo": True})
        return self.status()

    def disconnect(self):
        self.stop()
        self.connected = False
        self.device = None
        self.baudrate = None
        self.state = "DISCONNECTED"
        self._sync()
        return self.status()

    def set_velocity(self, vx, vy, wz):
        if not self.connected:
            raise RuntimeError("chassis is not connected")
        self.velocity = {"vx": vx, "vy": vy, "wz": wz}
        self.command = None
        self.state = "RUNNING" if any(self.velocity.values()) else "CONNECTED"
        self.event_log.append("command", "chassis", {"command": "V", **self.velocity, "demo": True})
        self._sync()
        return self.status()

    def run_distance(self, forward_cm, right_cm, rotate_deg, speed):
        if not self.connected:
            raise RuntimeError("chassis is not connected")
        self.command = {
            "forward_cm": forward_cm,
            "right_cm": right_cm,
            "rotate_deg": rotate_deg,
            "speed": speed,
        }
        self.distance["forward_cm"] += forward_cm
        self.distance["right_cm"] += right_cm
        self.rotate_deg += rotate_deg
        self.velocity = {"vx": 0, "vy": 0, "wz": 0}
        self.state = "RUNNING"
        self.event_log.append("command", "chassis", {"command": "D", **self.command, "demo": True})
        self._sync()
        return {**self.status(), "command": dict(self.command)}

    def stop(self):
        self.velocity = {"vx": 0, "vy": 0, "wz": 0}
        self.command = None
        if self.connected:
            self.state = "CONNECTED"
            self.event_log.append("command", "chassis", {"command": "STOP", "demo": True})
        self._sync()
        return self.status()

    def run_sequence(self):
        if not self.connected:
            raise RuntimeError("chassis is not connected")
        self.command = {"kind": "SEQ"}
        self.state = "RUNNING"
        self.event_log.append("command", "chassis", {"command": "SEQ", "demo": True})
        self._sync()
        return self.status()

    def request_encoder(self):
        if not self.connected:
            raise RuntimeError("chassis is not connected")
        self.last_reply = {"kind": "encoder", "value": "LF 0 RF 0 LR 0 RR 0"}
        return self.status()

    def request_speed(self):
        if not self.connected:
            raise RuntimeError("chassis is not connected")
        self.last_reply = {"kind": "speed", "value": "LF 0 RF 0 LR 0 RR 0 OUT 0 0 0 0"}
        return self.status()

    def motor_test(self, wheel, speed):
        if not self.connected:
            raise RuntimeError("chassis is not connected")
        self.command = {"kind": "M", "wheel": wheel, "speed": speed}
        self.state = "RUNNING"
        self._sync()
        return self.status()

    def reset_encoder(self):
        if not self.connected:
            raise RuntimeError("chassis is not connected")
        self.last_reply = {"kind": "ok", "value": "ENC RESET"}
        return self.status()

    def reset_distance(self):
        self.distance = {"forward_cm": 0, "right_cm": 0}
        self.rotate_deg = 0
        return self.status()

    def poll_once(self):
        return []

    def keepalive_tick(self, now=None):
        """The demo has no Bluetooth session to keep alive."""
        return False

    def status(self):
        return {"connected": self.connected, "device": self.device, "baudrate": self.baudrate, "state": self.state, "velocity": dict(self.velocity), "command": dict(self.command) if self.command else None, "last_reply": self.last_reply, "error": self.error, "distance": dict(self.distance), "pose": {**self.distance, "rotate_deg": self.rotate_deg}, "keepalive": {"enabled": False, "period_s": 0, "sends": 0, "last_send_s_ago": None, "next_in_s": None}}

    def _sync(self):
        self.hub_state.update_module("chassis", state=self.state, detail=f"演示底盘 / {self.state}")


class DemoArmService:
    def __init__(self, hub_state, event_log) -> None:
        self.hub_state = hub_state
        self.event_log = event_log
        self.connected = False
        self.mode = "DISCONNECTED"
        self.routine = None
        self.step = None
        self.suction_commanded = False
        self.calibrated = True

    def ports(self):
        return [{"device": "demo-arm", "description": "Simulated CH340", "is_ch340": True, "vid": 6790, "pid": 29987}]

    def connect(self, device, baudrate):
        self.connected = True
        self.mode = "CONNECTED"
        self.hub_state.update_module("arm", state=self.mode, detail=f"{device} / 演示")
        self.event_log.append("connected", "arm", {"device": device, "baudrate": baudrate, "demo": True})
        return self.status()

    def disconnect(self):
        self.stop()
        self.connected = False
        self.mode = "DISCONNECTED"
        self.hub_state.update_module("arm", state=self.mode, detail="演示机械臂未连接")
        return self.status()

    def probe(self):
        if not self.connected:
            raise RuntimeError("arm is not connected")
        self.mode = "LOCKED"
        self.hub_state.update_module("arm", state=self.mode, detail="安全探测完成 / 演示")
        self.event_log.append("probe", "arm", {"demo": True})
        return self.status()

    def enable(self):
        if self.mode != "LOCKED" or not self.calibrated:
            raise RuntimeError("arm must report calibrated LOCKED state before enable")
        self.mode = "READY"
        self._sync()
        return self.status()

    def run(self, routine):
        if self.mode != "READY":
            raise RuntimeError("arm must report READY before this command")
        self.mode = "BUSY"
        self.routine = routine
        self.step = 0
        self._sync()
        threading.Timer(2.0, self._finish_action).start()
        return self.status()

    def suction(self, enabled):
        if self.mode != "READY":
            raise RuntimeError("arm must report READY before this command")
        if type(enabled) is not bool:
            raise ValueError("enabled must be a boolean")
        self.suction_commanded = enabled
        self.event_log.append("command", "arm", {"command": "SUCTION", "enabled": enabled, "demo": True})
        return self.status()

    def servo(self, servo_id, position, time_ms):
        if self.mode != "READY":
            raise RuntimeError("arm must report READY before this command")
        self.event_log.append("command", "arm", {"command": "SERVO", "id": servo_id, "position": position, "time_ms": time_ms, "demo": True})
        return self.status()

    def move(self, positions, time_ms):
        if self.mode != "READY":
            raise RuntimeError("arm must report READY before this command")
        self.event_log.append("command", "arm", {"command": "MOVE", "positions": positions, "time_ms": time_ms, "demo": True})
        return self.status()

    def stop(self):
        if self.connected:
            self.mode = "LOCKED"
            self.routine = None
            self.step = None
            self._sync()
        return self.status()

    def status(self):
        return {"connected": self.connected, "device": "demo-arm" if self.connected else None, "baudrate": 115200 if self.connected else None, "mode": self.mode, "routine": self.routine, "step": self.step, "suction_commanded": self.suction_commanded, "calibrated": self.calibrated if self.connected else None}

    def _finish_action(self):
        if self.mode == "BUSY":
            routine = self.routine
            self.mode = "READY"
            self.routine = None
            self.step = None
            self._sync()
            self.event_log.append("reply", "arm", {"kind": "ArmEvent", "name": "DONE", "value": str(routine), "demo": True})

    def _sync(self):
        self.hub_state.update_module("arm", state=self.mode, detail=f"演示机械臂 / {self.mode}")


class DemoCameraService(RecordingLibrary):
    def __init__(self, hub_state, event_log, output_dir) -> None:
        self.hub_state = hub_state
        self.event_log = event_log
        self.output_dir = Path(output_dir)
        self.running = True
        self.recording = False
        self.sequence = 0
        self.recording_path = None
        self._jpeg = self._make_frame()
        self.hub_state.update_module("camera", state="RUNNING", detail="演示画面")

    def _make_frame(self):
        frame = np.full((720, 1280, 3), (238, 243, 245), dtype=np.uint8)
        cv2.rectangle(frame, (70, 70), (1210, 650), (32, 38, 43), 3)
        cv2.line(frame, (640, 120), (640, 600), (8, 126, 139), 2)
        cv2.line(frame, (170, 360), (1110, 360), (8, 126, 139), 2)
        cv2.putText(frame, "ROBOGAME / CAMERA DEMO", (110, 160), cv2.FONT_HERSHEY_SIMPLEX, 1.5, (32, 38, 43), 3, cv2.LINE_AA)
        cv2.putText(frame, "Connect /dev/video0 on Raspberry Pi", (110, 565), cv2.FONT_HERSHEY_SIMPLEX, .9, (22, 122, 85), 2, cv2.LINE_AA)
        ok, encoded = cv2.imencode(".jpg", frame, [cv2.IMWRITE_JPEG_QUALITY, 86])
        if not ok:
            raise RuntimeError("unable to create demo camera frame")
        self._frame = frame
        return encoded.tobytes()

    def latest_frame(self):
        return self._frame.copy()

    def latest_sample(self):
        return self.sequence, self._frame.copy()

    def latest_sequence(self):
        return self.sequence

    def start(self):
        self.running = True
        self.hub_state.update_module("camera", state="RUNNING", detail="演示画面")
        return self.status()

    def stop(self):
        self.running = False
        self.recording = False
        self.recording_path = None
        self.hub_state.update_module("camera", state="STOPPED", detail="演示画面已停止")
        return self.status()

    def wait_for_jpeg(self, _after_sequence, timeout_s=1.0):
        if not self.running:
            time.sleep(min(timeout_s, .02))
            return self.sequence, None
        time.sleep(.04)
        self.sequence += 1
        return self.sequence, self._jpeg

    def snapshot(self):
        directory = self.output_dir / "snapshots"
        directory.mkdir(parents=True, exist_ok=True)
        path = directory / "demo_snapshot.jpg"
        path.write_bytes(self._jpeg)
        self.event_log.append("snapshot", "camera", {"path": str(path), "demo": True})
        return path

    def start_recording(self, label):
        self.recording = True
        self.recording_path = str(self.output_dir / "recordings" / f"demo_{label}.avi")
        return self.status()

    def stop_recording(self):
        if not self.recording:
            raise RuntimeError("no recording is in progress")
        path = self.recording_path
        self.recording = False
        self.recording_path = None
        return {"path": path, "frames": 50}

    def status(self):
        return {"running": self.running, "frame_available": self.running, "sequence": self.sequence, "recording": self.recording, "recording_path": self.recording_path, "recording_frames": 0, "error": None, "actual": {"device": "demo", "width": 1280, "height": 720, "fps": 25.0, "pixel_format": "MJPG"}}

    def latest_snapshot(self):
        return {"sequence": self.sequence, "jpeg": self._jpeg if self.running else None, "frame": self._frame.copy() if self.running else None}

    def _active_recording_path(self):
        return Path(self.recording_path).resolve() if self.recording_path else None


class DemoLineService:
    """Stable line-sensor telemetry for browser-only validation."""

    def __init__(self, hub_state, _event_log=None) -> None:
        self.hub_state = hub_state
        # 0 = probe on the black line (vendor manual), so a centred robot with
        # the line under its middle probes reads 0b11000011, not 0b00111100.
        self._mask = 0b11000011
        self.connected = True
        self._sync()

    def start(self):
        self.connected = True
        self._sync()

    def poll_once(self):
        return self.status()

    def close(self):
        self.connected = False
        self.hub_state.update_module("line", state="DISCONNECTED", detail="演示巡线已停止")

    def status(self):
        bits = [(self._mask >> (7 - i)) & 1 for i in range(8)]
        return {
            "connected": self.connected,
            "state": "FOLLOWING" if self.connected else "DISCONNECTED",
            "error": None,
            "raw": "$D,x1:1,x2:1,x3:0,x4:0,x5:0,x6:0,x7:1,x8:1#",
            "sensor_mask": self._mask,
            "sensors": bits,
            "line_error": 0.0,
            "line_lost": False,
            "intersection": "none",
            "timestamp_ms": None,
            "device": "/dev/ttyAMA0",
            "rx_gpio": 15,
            "tx_gpio": 14,
            "baudrate": 115200,
        }

    def _sync(self):
        self.hub_state.update_module("line", state="FOLLOWING" if self.connected else "DISCONNECTED", detail="演示巡线数据")
