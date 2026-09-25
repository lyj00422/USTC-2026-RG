"""Thread-safe mechanical arm service for browser and API clients."""

from __future__ import annotations

from dataclasses import asdict, is_dataclass
import threading
import time

from rg_runtime.arm_tools import ArmCommandCancelled, ArmSession, ArmTimeoutError, discover_serial_ports
from rg_runtime.devices import ArmDevice
from rg_runtime.hardware_models import ArmMode
from rg_runtime.transports import SerialTransport

from ..state import HubState
from .event_log import EventLog


class ArmService:
    def __init__(
        self,
        hub_state: HubState,
        event_log: EventLog,
        *,
        transport_factory=None,
        background: bool = True,
        action_timeout_s: float = 45.0,
        probe_timeout_s: float = 2.0,
        command_ack_timeout_s: float = 1.0,
        clock=time.monotonic,
    ) -> None:
        self.hub_state = hub_state
        self.event_log = event_log
        self.transport_factory = transport_factory or (
            lambda device, baudrate: SerialTransport(device, baudrate, timeout_s=0.0)
        )
        self.background = background
        self.action_timeout_s = action_timeout_s
        self.probe_timeout_s = probe_timeout_s
        self.command_ack_timeout_s = command_ack_timeout_s
        self.clock = clock
        self._session: ArmSession | None = None
        self._device: str | None = None
        self._baudrate: int | None = None
        self._lock = threading.RLock()
        self._stop_event = threading.Event()
        self._command_cancel = threading.Event()
        self._worker: threading.Thread | None = None
        self._active_routine: int | None = None
        self._action_started_at: float | None = None
        self._last_servo_targets: dict[int, dict] = {}
        self._last_move_targets: list[int] | None = None

    @property
    def connected(self) -> bool:
        with self._lock:
            return self._session is not None

    def ports(self) -> list[dict]:
        return [asdict(port) for port in discover_serial_ports()]

    def connect(self, device: str, baudrate: int = 115200) -> dict:
        with self._lock:
            if self._session is not None:
                raise RuntimeError("arm serial port is already connected")
            transport = self.transport_factory(device, baudrate)
            self._session = ArmSession(ArmDevice(transport), transport)
            self._device = device
            self._baudrate = baudrate
            self._stop_event.clear()
            self._command_cancel.clear()
            self.hub_state.update_module("arm", state="CONNECTED", detail=f"{device}，等待安全探测")
            self.event_log.append("connected", "arm", {"device": device, "baudrate": baudrate})
            return self.status()

    def probe(self, timeout_s: float | None = None) -> dict:
        with self._lock:
            session = self._require_session()
            for raw in ("ARM,STOP", "ARM,PING", "ARM,STATUS"):
                self._record_tx(raw)
            replies = session.safe_probe(self.probe_timeout_s if timeout_s is None else timeout_s)
            self._record_raw_rx(session.last_operation_raw)
            self._record_replies(replies)
            self._sync_module_state()
            if self.background:
                self._start_worker()
            return self.status()

    def enable(self) -> dict:
        with self._lock:
            session = self._require_session()
            session.enable()
            self._record_tx("ARM,ENABLE")
            self.event_log.append("command", "arm", {"command": "ENABLE"})
            return self.status()

    def run(self, routine: int) -> dict:
        with self._lock:
            session = self._require_ready()
            session.run(routine)
            self._record_tx(f"ARM,RUN,{routine}")
            self._active_routine = routine
            self._action_started_at = self.clock()
            self.event_log.append("command", "arm", {"command": "RUN", "routine": routine})
            return self.status()

    def suction(self, enabled: bool) -> dict:
        with self._lock:
            session = self._require_ready()
            session.suction(enabled)
            self._record_tx(f"ARM,SUCTION,{int(enabled)}")
            self.event_log.append("command", "arm", {"command": "SUCTION", "enabled": enabled})
            return self.status()

    def servo(self, servo_id: int, position: int, time_ms: int) -> dict:
        if type(servo_id) is not int or not 0 <= servo_id <= 4:
            raise ValueError("servo_id must be an integer between 0 and 4")
        if type(position) is not int or not 500 <= position <= 2500:
            raise ValueError("position must be an integer between 500 and 2500")
        if type(time_ms) is not int or not 100 <= time_ms <= 10000:
            raise ValueError("time_ms must be an integer between 100 and 10000")
        with self._lock:
            session = self._require_ready()
            session.servo(servo_id, position, time_ms)
            self._record_tx(f"ARM,SERVO,{servo_id},{position},{time_ms}")
            self._wait_for_ack_locked(session, "SERVO")
            self._last_servo_targets[servo_id] = {"id": servo_id, "position": position, "time_ms": time_ms}
            self.event_log.append("command", "arm", {"command": "SERVO", "id": servo_id, "position": position, "time_ms": time_ms})
            return self.status()

    def move(self, positions: list[int], time_ms: int) -> dict:
        if type(positions) is not list or len(positions) != 5:
            raise ValueError("positions must be a list of five integers")
        if any(type(value) is not int or not (value == 65535 or 500 <= value <= 2500) for value in positions):
            raise ValueError("positions must be 500..2500 or 65535")
        if type(time_ms) is not int or not 100 <= time_ms <= 10000:
            raise ValueError("time_ms must be an integer between 100 and 10000")
        with self._lock:
            session = self._require_ready()
            session.move(positions, time_ms)
            self._record_tx("ARM,MOVE," + ",".join(map(str, positions)) + f",{time_ms}")
            self._wait_for_ack_locked(session, "MOVE")
            self._last_move_targets = list(positions)
            self.event_log.append("command", "arm", {"command": "MOVE", "positions": list(positions), "time_ms": time_ms})
            return self.status()

    def stop(self) -> dict:
        # Set this before taking the serial lock so a waiting command releases it.
        self._command_cancel.set()
        with self._lock:
            if self._session is not None:
                self._session.stop()
                self._record_tx("ARM,STOP")
                self._active_routine = None
                self._action_started_at = None
                self.event_log.append("command", "arm", {"command": "STOP"})
                self._sync_module_state()
            self._command_cancel.clear()
            return self.status()

    def poll_once(self) -> list:
        with self._lock:
            if self._session is None:
                return []
            replies = self._session.poll()
            self._record_raw_rx(self._session.arm.last_raw_lines)
            self._record_replies(replies)
            if (
                self._active_routine is not None
                and self._session.arm.last_done_routine == self._active_routine
            ):
                self._active_routine = None
                self._action_started_at = None
            elif (
                self._action_started_at is not None
                and self.clock() - self._action_started_at > self.action_timeout_s
            ):
                routine = self._active_routine
                self._session.stop()
                self._record_tx("ARM,STOP")
                self._active_routine = None
                self._action_started_at = None
                self.event_log.append("fault", "arm", {"message": "action timeout", "routine": routine})
            self._sync_module_state()
            return replies

    def disconnect(self) -> dict:
        self._stop_event.set()
        worker = self._worker
        if worker is not None and worker is not threading.current_thread():
            worker.join(timeout=1.0)
        with self._lock:
            if self._session is not None:
                try:
                    self._session.close()
                finally:
                    self._session = None
                    self._worker = None
                    self._device = None
                    self._baudrate = None
                    self._active_routine = None
                    self._action_started_at = None
            self.hub_state.update_module("arm", state="DISCONNECTED", detail="USB 串口未连接")
            self.event_log.append("disconnected", "arm", {})
            return self.status()

    def status(self) -> dict:
        with self._lock:
            if self._session is None:
                return {
                    "connected": False,
                    "device": None,
                    "baudrate": None,
                    "mode": "DISCONNECTED",
                    "routine": None,
                    "step": None,
                    "suction_commanded": False,
                    "calibrated": None,
                    "servo_targets": {},
                    "move_targets": None,
                }
            state = self._session.arm.state
            return {
                "connected": True,
                "device": self._device,
                "baudrate": self._baudrate,
                "mode": state.mode.value,
                "routine": state.routine,
                "step": state.step,
                "suction_commanded": state.suction_commanded,
                "calibrated": state.calibrated,
                "servo_targets": {str(k): dict(v) for k, v in self._last_servo_targets.items()},
                "move_targets": list(self._last_move_targets) if self._last_move_targets else None,
            }

    def _require_session(self) -> ArmSession:
        if self._session is None:
            raise RuntimeError("arm is not connected")
        return self._session

    def _require_ready(self) -> ArmSession:
        session = self._require_session()
        if session.arm.state.mode is not ArmMode.READY:
            raise RuntimeError("arm must report READY before this command")
        return session

    def _record_replies(self, replies) -> None:
        for reply in replies:
            payload = asdict(reply) if is_dataclass(reply) else {"value": str(reply)}
            self.event_log.append("reply", "arm", {"kind": reply.__class__.__name__, **payload})

    def _record_tx(self, raw: str) -> None:
        self.event_log.append("serial_tx", "arm", {"raw": raw})

    def _record_raw_rx(self, lines: list[str]) -> None:
        for raw in lines:
            self.event_log.append("serial_rx", "arm", {"raw": raw})

    def _wait_for_ack_locked(self, session: ArmSession, command: str) -> None:
        session.last_operation_raw = []
        try:
            replies = session.wait_for_ack(command, self.command_ack_timeout_s, self._command_cancel)
        except (ArmTimeoutError, ArmCommandCancelled) as exc:
            self.event_log.append("fault", "arm", {"message": str(exc), "command": command})
            raise
        finally:
            self._record_raw_rx(session.last_operation_raw)
        self._record_replies(replies)

    def _sync_module_state(self) -> None:
        status = self.status()
        if status["connected"]:
            detail = f"{status['device']} / {status['mode']}"
            self.hub_state.update_module("arm", state=status["mode"], detail=detail)

    def _start_worker(self) -> None:
        if self._worker is not None and self._worker.is_alive():
            return
        self._worker = threading.Thread(target=self._poll_loop, name="arm-poll", daemon=True)
        self._worker.start()

    def _poll_loop(self) -> None:
        while not self._stop_event.wait(0.02):
            try:
                self.poll_once()
            except Exception as exc:  # pragma: no cover - hardware dependent
                self.handle_poll_fault(exc)

    def handle_poll_fault(self, exc: Exception) -> None:
        with self._lock:
            self.event_log.append("fault", "arm", {"message": str(exc)})
            if self._session is not None:
                try:
                    self._session.stop()
                    self._record_tx("ARM,STOP")
                except Exception as stop_exc:
                    self.event_log.append("fault", "arm", {"message": f"stop failed: {stop_exc}"})
            self._active_routine = None
            self._action_started_at = None
            self.hub_state.update_module("arm", state="FAULT", detail=str(exc))
            self._stop_event.set()
