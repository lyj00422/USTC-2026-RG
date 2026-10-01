"""Thread-safe browser-facing service for the chassis serial link.

The link is the Pi's UART2 (GPIO0/GPIO1, physical pins 27/28) wired to the
STM32's USART3 on PB10/PB11, reached through the udev alias
``/dev/robogame-chassis``.  It used to be a JDY-31 Bluetooth SPP module; that
is gone (2026-10-01) and with it the idle-session keepalive this service used
to run -- a wire has no session to drop.
"""

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
        open_retry_s: float = 20.0,
        open_retry_interval_s: float = 2.5,
    ) -> None:
        self.hub_state = hub_state
        self.event_log = event_log
        self.transport_factory = transport_factory or (
            lambda device, baudrate: SerialTransport(device, baudrate, timeout_s=0.0)
        )
        self.port_discovery = port_discovery or self._discover_ports
        self.open_retry_s = max(0.0, float(open_retry_s))
        self.open_retry_interval_s = max(0.1, float(open_retry_interval_s))
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
        # Keeps route v2 from stealing this console's serial replies.  Until
        # 2026-10-01 there was a third party to hold off -- the
        # robogame-chassis-rfcomm maintainer service, which released and
        # rebuilt the tty whenever the JDY-31 hung up.  The chassis is on a
        # wire now and that service is gone, so route-vs-console is the only
        # contention left.
        self._port_lock = ChassisPortLock()
        self._distance = {"forward_cm": 0, "right_cm": 0}
        self._rotate_deg = 0
        self._motion_history: list[dict] = []
        self._velocity_started_at: float | None = None
        self._clock = time.monotonic

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
            transport = self._open_transport(device, baudrate)
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
            self.hub_state.update_module("chassis", state="CONNECTED", detail=f"{device} / {baudrate} 8N1")
            self.event_log.append("connected", "chassis", {"device": device, "baudrate": baudrate})
            return self.status()

    def _open_transport(self, device: str, baudrate: int):
        """Open the TTY, retrying until the device node exists.

        The retry used to cover the RFCOMM maintainer's rebuild windows (the
        JDY-31 dropped an idle SPP session every ~13-20 s and an open landing
        inside the teardown got `[Errno 5]`).  A wired UART has no such window,
        but the node is not instantaneously there either: on a cold boot the
        udev rule that creates ``/dev/robogame-chassis`` trails the console, and
        the operator should not have to press Connect twice.  `ChassisLink`
        keeps the same behaviour for the route; see its module docstring.

        The port lock is released between attempts on purpose -- ChassisPortBusy
        means another program owns the port and has to be stopped first, not
        waited out, but a bare ``No such file or directory`` is worth retrying.
        `_pi_chassis_ok.py` carries the same rule.
        """
        deadline = time.monotonic() + self.open_retry_s
        attempts = 0
        while True:
            attempts += 1
            # ChassisPortBusy raised from here is not a race to wait out:
            # another program owns the port and has to be stopped first.
            self._port_lock.acquire()
            try:
                return self.transport_factory(device, baudrate)
            except Exception:
                self._port_lock.release()
                if time.monotonic() >= deadline:
                    if attempts > 1:
                        self.event_log.append(
                            "fault",
                            "chassis",
                            {"message": f"could not open {device} in {attempts} attempts"},
                        )
                    raise
                time.sleep(self.open_retry_interval_s)

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
            if self._state != "FAULT":
                self._state = "DISCONNECTED"
                self._error = None
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
            }

    def snapshot_state(self) -> dict:
        return self.status()

    def _send_stop_locked(self, *, force: bool = False) -> None:
        if self._last_command_stop and not force:
            return
        self._device.stop()
        self._last_command_stop = True
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
            self.event_log.append("serial_tx", "chassis", {"raw": command})
            return self.status()

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
            # Release so a reconnect can pick the port straight back up.
            self._port_lock.release()
        self.hub_state.update_module("chassis", state="FAULT", detail=self._detail())

    def _require_connected(self) -> ChassisDevice:
        if self._device is None:
            raise RuntimeError("chassis is not connected")
        return self._device

    def _detail(self) -> str:
        if self._state == "DISCONNECTED":
            return "底盘串口未连接"
        if self._error:
            return self._error
        return f"{self._path} / {self._state}" if self._path else self._state

    @staticmethod
    def _discover_ports() -> list[dict]:
        """List the host's serial ports, flagging the chassis link.

        Before 2026-10-01 the module identified itself: a JDY-31 Bluetooth SPP
        link, whose product string pyserial reported and whose node was
        /dev/rfcomm0.  A UART has no product string, no VID/PID and no
        driver-specific name, so all that is left is the name we gave it
        ourselves -- the udev alias, and the raw tty behind it.  ``ttyAMA2`` is
        the one measured on the Pi at bring-up (see RASPBERRY_PI_UART.md); that
        number moves if the overlay changes, and all a wrong guess costs is a
        hint in a dropdown, so it is matched literally rather than derived.

        The flag is only a hint in the operator's port list, and one fallback in
        api.py for when the alias is missing.  The path the console actually
        opens is always ``config.chassis_device``.
        """
        try:
            from serial.tools import list_ports
        except ImportError:  # pragma: no cover - pyserial is an install dependency
            return []
        result = []
        for port in list_ports.comports():
            description = port.description or ""
            name = f"{port.device} {description}".upper()
            result.append(
                {
                    "device": port.device,
                    "description": description,
                    "is_chassis": "ROBOGAME-CHASSIS" in name or "TTYAMA2" in name,
                    "vid": port.vid,
                    "pid": port.pid,
                }
            )
        return result
