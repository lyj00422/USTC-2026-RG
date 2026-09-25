"""Thread-safe browser-facing service for the Bluetooth chassis link."""

from __future__ import annotations

from dataclasses import asdict, is_dataclass
import time
import threading

from rg_runtime.chassis_lock import ChassisPortLock
from rg_runtime.devices import ChassisDevice
from rg_runtime.transports import SerialTransport

from ..state import HubState
from .event_log import EventLog


class ChassisService:
    def __init__(
        self,
        hub_state: HubState,
        event_log: EventLog,
        *,
        transport_factory=None,
        port_discovery=None,
        keepalive_enabled: bool = True,
        keepalive_s: float = 5.0,
        keepalive_quiet_s: float = 2.0,
        keepalive_log_every_s: float = 60.0,
    ) -> None:
        self.hub_state = hub_state
        self.event_log = event_log
        self.transport_factory = transport_factory or (
            lambda device, baudrate: SerialTransport(device, baudrate, timeout_s=0.0)
        )
        self.port_discovery = port_discovery or self._discover_ports
        self.keepalive_enabled = bool(keepalive_enabled)
        self.keepalive_s = max(1.0, float(keepalive_s))
        self.keepalive_quiet_s = max(0.0, float(keepalive_quiet_s))
        self.keepalive_log_every_s = max(0.0, float(keepalive_log_every_s))
        self._device: ChassisDevice | None = None
        self._transport = None
        self._path: str | None = None
        self._baudrate: int | None = None
        self._velocity = {"vx": 0, "vy": 0, "wz": 0}
        self._command: dict | None = None
        self._last_command_stop = True
        self._state = "DISCONNECTED"
        self._error: str | None = None
        self._last_reply: dict | None = None
        self._lock = threading.RLock()
        # Keeps the RFCOMM link maintainer from releasing the TTY underneath us
        # and keeps route v2 from stealing our serial replies.
        self._port_lock = ChassisPortLock()
        self._distance = {"forward_cm": 0, "right_cm": 0}
        self._rotate_deg = 0
        self._motion_history: list[dict] = []
        self._velocity_started_at: float | None = None
        self._clock = time.monotonic
        # Idle keepalive state.  _last_tx_at covers every byte this service puts
        # on the wire, so the keepalive can stay out of the operator's way.
        self._last_tx_at: float | None = None
        self._next_keepalive_at: float | None = None
        self._last_keepalive_at: float | None = None
        self._last_keepalive_log_at: float | None = None
        self._keepalive_sends = 0

    @property
    def connected(self) -> bool:
        with self._lock:
            return self._device is not None

    def ports(self) -> list[dict]:
        return list(self.port_discovery())

    def connect(self, device: str, baudrate: int = 9600) -> dict:
        if not isinstance(device, str) or not device.strip():
            raise ValueError("device must be a non-empty string")
        if type(baudrate) is not int or not 1200 <= baudrate <= 1_000_000:
            raise ValueError("baudrate must be an integer between 1200 and 1000000")
        with self._lock:
            if self._device is not None:
                raise RuntimeError("chassis serial port is already connected")
            self._port_lock.acquire()
            try:
                transport = self.transport_factory(device, baudrate)
            except Exception:
                self._port_lock.release()
                raise
            self._transport = transport
            self._device = ChassisDevice(transport)
            self._path = device
            self._baudrate = baudrate
            self._velocity = {"vx": 0, "vy": 0, "wz": 0}
            self._distance = {"forward_cm": 0, "right_cm": 0}
            self._rotate_deg = 0
            self._motion_history = []
            self._velocity_started_at = None
            self._command = None
            self._last_command_stop = False
            self._state = "CONNECTED"
            self._error = None
            self._last_tx_at = self._clock()
            self._next_keepalive_at = self._last_tx_at + self.keepalive_s
            self._last_keepalive_at = None
            self._last_keepalive_log_at = None
            self._keepalive_sends = 0
            self.hub_state.update_module("chassis", state="CONNECTED", detail=f"{device} / {baudrate} 8N1")
            self.event_log.append("connected", "chassis", {"device": device, "baudrate": baudrate})
            if self.keepalive_enabled:
                self.event_log.append(
                    "keepalive",
                    "chassis",
                    {"state": "enabled", "command": "SPD", "period_s": self.keepalive_s, "quiet_s": self.keepalive_quiet_s},
                )
            return self.status()

    def disconnect(self) -> dict:
        with self._lock:
            if self._device is not None:
                try:
                    self._send_stop_locked(force=True)
                except Exception as exc:
                    self._error = str(exc)
                    self._state = "FAULT"
                    self.event_log.append("fault", "chassis", {"message": str(exc)})
                try:
                    self._transport.close()
                finally:
                    self._device = None
                    self._transport = None
                    self._port_lock.release()
            self._path = None
            self._baudrate = None
            self._velocity = {"vx": 0, "vy": 0, "wz": 0}
            self._next_keepalive_at = None
            if self._state != "FAULT":
                self._state = "DISCONNECTED"
                self._error = None
            if self._keepalive_sends:
                self.event_log.append("keepalive", "chassis", {"state": "stopped", "sends": self._keepalive_sends})
            self._keepalive_sends = 0
            self._last_keepalive_at = None
            self.hub_state.update_module("chassis", state=self._state, detail=self._detail())
            self.event_log.append("disconnected", "chassis", {})
            return self.status()

    def set_velocity(self, vx: int, vy: int, wz: int) -> dict:
        values = (vx, vy, wz)
        if any(type(value) is not int for value in values):
            raise ValueError("velocity values must be integers")
        if any(not -100 <= value <= 100 for value in values):
            raise ValueError("velocity values must be between -100 and 100")
        with self._lock:
            device = self._require_connected()
            now = self._clock()
            try:
                device.set_velocity(vx, vy, wz)
            except Exception as exc:
                self._handle_fault_locked(exc)
                raise
            self._last_tx_at = now
            previous = dict(self._velocity)
            if self._velocity_is_nonzero(previous) and previous != {"vx": vx, "vy": vy, "wz": wz}:
                self._finish_velocity_segment_locked(now)
            if any((vx, vy, wz)) and (
                not self._velocity_is_nonzero(previous)
                or previous != {"vx": vx, "vy": vy, "wz": wz}
            ):
                self._velocity_started_at = now
            self._velocity = {"vx": vx, "vy": vy, "wz": wz}
            self._command = None
            self._last_command_stop = not any(values)
            self._state = "RUNNING" if any(values) else "CONNECTED"
            self.event_log.append("serial_tx", "chassis", {"raw": f"V {vx} {vy} {wz}"})
            self.event_log.append("command", "chassis", {"command": "V", **self._velocity})
            self.hub_state.update_module("chassis", state=self._state, detail=self._detail())
            return self.status()

    def run_distance(self, forward_cm: int, right_cm: int, rotate_deg: int, speed: int) -> dict:
        values = (forward_cm, right_cm, rotate_deg, speed)
        if any(type(value) is not int for value in values):
            raise ValueError("distance command values must be integers")
        if not 1 <= speed <= 100:
            raise ValueError("speed must be between 1 and 100")
        if not -10000 <= forward_cm <= 10000 or not -10000 <= right_cm <= 10000 or not -3600 <= rotate_deg <= 3600:
            raise ValueError("distance command values are out of range")
        if forward_cm == 0 and right_cm == 0 and rotate_deg == 0:
            raise ValueError("distance command must include movement")
        with self._lock:
            device = self._require_connected()
            self._finish_velocity_segment_locked(self._clock())
            command = {"forward_cm": forward_cm, "right_cm": right_cm, "rotate_deg": rotate_deg, "speed": speed}
            try:
                device.run_distance(forward_cm, right_cm, rotate_deg, speed)
            except Exception as exc:
                self._handle_fault_locked(exc)
                raise
            self._last_tx_at = self._clock()
            self._command = command
            self._distance["forward_cm"] += forward_cm
            self._distance["right_cm"] += right_cm
            self._rotate_deg += rotate_deg
            self._velocity = {"vx": 0, "vy": 0, "wz": 0}
            self._last_command_stop = False
            self._state = "RUNNING"
            self.event_log.append("serial_tx", "chassis", {"raw": f"D {forward_cm} {right_cm} {rotate_deg} {speed}"})
            self.event_log.append("command", "chassis", {"command": "D", **command})
            self.hub_state.update_module("chassis", state=self._state, detail=self._detail())
            return {**self.status(), "command": command}

    def stop(self) -> dict:
        with self._lock:
            if self._device is not None:
                self._send_stop_locked()
            self._finish_velocity_segment_locked(self._clock())
            self._velocity = {"vx": 0, "vy": 0, "wz": 0}
            self._command = None
            if self._state != "FAULT":
                self._state = "CONNECTED" if self._device is not None else "DISCONNECTED"
            self.hub_state.update_module("chassis", state=self._state, detail=self._detail())
            return self.status()

    def run_sequence(self) -> dict:
        with self._lock:
            device = self._require_connected()
            try:
                device.run_sequence()
            except Exception as exc:
                self._handle_fault_locked(exc)
                raise
            self._last_tx_at = self._clock()
            self._command = {"kind": "SEQ"}
            self._velocity = {"vx": 0, "vy": 0, "wz": 0}
            self._last_command_stop = False
            self._state = "RUNNING"
            self._error = None
            self.event_log.append("serial_tx", "chassis", {"raw": "SEQ"})
            self.event_log.append("command", "chassis", {"command": "SEQ"})
            self.hub_state.update_module("chassis", state=self._state, detail=self._detail())
            return self.status()

    def request_encoder(self) -> dict:
        return self._send_query("ENC", lambda device: device.request_encoder())

    def request_speed(self) -> dict:
        return self._send_query("SPD", lambda device: device.request_speed())

    def motor_test(self, wheel: str, speed: int) -> dict:
        if wheel not in {"LF", "RF", "LR", "RR"}:
            raise ValueError("wheel must be LF, RF, LR or RR")
        if type(speed) is not int or not -100 <= speed <= 100:
            raise ValueError("speed must be an integer between -100 and 100")
        with self._lock:
            result = self._send_query(f"M {wheel} {speed}", lambda device: device.motor_test(wheel, speed))
            self._command = {"kind": "M", "wheel": wheel, "speed": speed}
            self._velocity = {"vx": 0, "vy": 0, "wz": 0}
            self._state = "RUNNING" if speed else "CONNECTED"
            self._last_command_stop = speed == 0
            self.hub_state.update_module("chassis", state=self._state, detail=self._detail())
            return {**result, "command": dict(self._command)}

    def reset_encoder(self) -> dict:
        return self._send_query("ENC RESET", lambda device: device.reset_encoder())

    def reset_distance(self) -> dict:
        with self._lock:
            self._finish_velocity_segment_locked(self._clock())
            self._distance = {"forward_cm": 0, "right_cm": 0}
            self._rotate_deg = 0
            self._motion_history = []
            self.event_log.append("command", "chassis", {"command": "DISTANCE_RESET"})
            return self.status()

    def poll_once(self) -> list:
        with self._lock:
            if self._device is None:
                return []
            try:
                replies = self._device.poll()
            except Exception as exc:
                self._handle_fault_locked(exc)
                raise
            for reply in replies:
                self._last_reply = asdict(reply) if is_dataclass(reply) else {"value": str(reply)}
                self.event_log.append("reply", "chassis", {"kind": reply.__class__.__name__, **self._last_reply})
                if getattr(reply, "kind", None) == "done":
                    self._command = None
                    self._state = "CONNECTED"
                    self._error = None
                elif getattr(reply, "kind", None) == "error":
                    self._command = None
                    self._state = "FAULT"
                    self._error = reply.value
                elif getattr(reply, "kind", None) == "ready":
                    self._state = "CONNECTED"
                    self._error = None
            if replies:
                self.hub_state.update_module("chassis", state=self._state, detail=self._detail())
            return replies

    def status(self) -> dict:
        with self._lock:
            return {
                "connected": self._device is not None,
                "device": self._path,
                "baudrate": self._baudrate,
                "state": self._state,
                "velocity": dict(self._velocity),
                "command": dict(self._command) if self._command else None,
                "last_reply": dict(self._last_reply) if self._last_reply else None,
                "error": self._error,
                "distance": dict(self._distance),
                "pose": {**self._distance, "rotate_deg": self._rotate_deg},
                "motion_history": self._motion_history_snapshot_locked(),
                "keepalive": self._keepalive_status_locked(),
            }

    def _keepalive_status_locked(self) -> dict:
        now = self._clock()
        return {
            "enabled": self.keepalive_enabled,
            "period_s": self.keepalive_s,
            "sends": self._keepalive_sends,
            "last_send_s_ago": None if self._last_keepalive_at is None else round(max(0.0, now - self._last_keepalive_at), 1),
            "next_in_s": None if self._next_keepalive_at is None else round(max(0.0, self._next_keepalive_at - now), 1),
        }

    def snapshot_state(self) -> dict:
        return self.status()

    def _send_stop_locked(self, *, force: bool = False) -> None:
        if self._last_command_stop and not force:
            return
        self._device.stop()
        self._last_command_stop = True
        self._last_tx_at = self._clock()
        self.event_log.append("serial_tx", "chassis", {"raw": "STOP"})
        self.event_log.append("command", "chassis", {"command": "STOP"})

    def _send_query(self, command: str, action) -> dict:
        with self._lock:
            device = self._require_connected()
            try:
                action(device)
            except Exception as exc:
                self._handle_fault_locked(exc)
                raise
            self._last_tx_at = self._clock()
            self.event_log.append("serial_tx", "chassis", {"raw": command})
            return self.status()

    def keepalive_tick(self, now: float | None = None) -> bool:
        """Send one read-only query when the link has been idle.

        The JDY-31 drops an idle SPP session after ~13-20 s and the maintainer
        service repairs it by recreating /dev/rfcomm0, which destroys the tty
        the console is holding (permanent ``[Errno 5]`` on the next read).  One
        ``SPD`` every ``keepalive_s`` keeps the session up; ``SPD`` is a query,
        so it commands no motion and cannot disturb a held ``V``.

        The tick is skipped whenever this service put anything on the wire
        within ``keepalive_quiet_s``: the firmware answers only the first
        command of a back-to-back pair, so a keepalive landing on top of an
        operator command could swallow that command -- a dropped ``V`` looks
        exactly like a dead robot.

        Returns True when a keepalive byte was actually sent.
        """
        if not self.keepalive_enabled:
            return False
        with self._lock:
            if self._device is None:
                return False
            now = self._clock() if now is None else now
            if self._next_keepalive_at is None or now < self._next_keepalive_at:
                return False
            self._next_keepalive_at = now + self.keepalive_s
            if self._last_tx_at is not None and now - self._last_tx_at < self.keepalive_quiet_s:
                return False
            try:
                self._device.request_speed()
            except Exception as exc:
                self._handle_fault_locked(exc)
                raise
            self._last_tx_at = now
            self._last_keepalive_at = now
            self._keepalive_sends += 1
            if self._last_keepalive_log_at is None or now - self._last_keepalive_log_at >= self.keepalive_log_every_s:
                self._last_keepalive_log_at = now
                self.event_log.append("keepalive", "chassis", {"command": "SPD", "sends": self._keepalive_sends})
            return True

    def _motion_history_snapshot_locked(self) -> list[dict]:
        history = [dict(item, velocity=dict(item["velocity"]), command_integral=dict(item["command_integral"])) for item in self._motion_history]
        if self._velocity_started_at is not None and self._velocity_is_nonzero(self._velocity):
            duration_ms = max(0, round((self._clock() - self._velocity_started_at) * 1000))
            history.append(self._segment_payload(self._velocity, duration_ms))
        return history

    def _finish_velocity_segment_locked(self, now: float) -> None:
        if self._velocity_started_at is None or not self._velocity_is_nonzero(self._velocity):
            self._velocity_started_at = None
            return
        duration_ms = max(0, round((now - self._velocity_started_at) * 1000))
        self._motion_history.append(self._segment_payload(self._velocity, duration_ms))
        self._velocity_started_at = None

    @staticmethod
    def _velocity_is_nonzero(velocity: dict) -> bool:
        return any(velocity.get(axis, 0) for axis in ("vx", "vy", "wz"))

    @staticmethod
    def _segment_payload(velocity: dict, duration_ms: int) -> dict:
        return {
            "velocity": dict(velocity),
            "duration_ms": duration_ms,
            "command_integral": {
                "vx_ms": velocity["vx"] * duration_ms,
                "vy_ms": velocity["vy"] * duration_ms,
                "wz_ms": velocity["wz"] * duration_ms,
            },
        }

    def _handle_fault_locked(self, exc: Exception) -> None:
        self._error = str(exc)
        self._state = "FAULT"
        self._velocity = {"vx": 0, "vy": 0, "wz": 0}
        self._command = None
        self.event_log.append("fault", "chassis", {"message": str(exc)})
        if self._device is not None:
            try:
                self._send_stop_locked()
            except Exception as stop_exc:
                self.event_log.append("fault", "chassis", {"message": f"stop failed: {stop_exc}"})
            try:
                self._transport.close()
            except Exception as close_exc:
                self.event_log.append("fault", "chassis", {"message": f"close failed: {close_exc}"})
            self._device = None
            self._transport = None
            # Release so the link maintainer is allowed to reconnect the TTY.
            self._port_lock.release()
        self.hub_state.update_module("chassis", state="FAULT", detail=self._detail())

    def _require_connected(self) -> ChassisDevice:
        if self._device is None:
            raise RuntimeError("chassis is not connected")
        return self._device

    def _detail(self) -> str:
        if self._state == "DISCONNECTED":
            return "蓝牙串口未连接"
        if self._error:
            return self._error
        return f"{self._path} / {self._state}" if self._path else self._state

    @staticmethod
    def _discover_ports() -> list[dict]:
        try:
            from serial.tools import list_ports
        except ImportError:  # pragma: no cover - pyserial is an install dependency
            return []
        result = []
        for port in list_ports.comports():
            description = port.description or ""
            result.append(
                {
                    "device": port.device,
                    "description": description,
                    "is_chassis": "JDY" in description.upper() or "BLUETOOTH" in description.upper() or "RFCOMM" in port.device.upper(),
                    "vid": port.vid,
                    "pid": port.pid,
                }
            )
        return result
