"""Safe Raspberry Pi discovery, probing, and console helpers for the arm."""

from __future__ import annotations

from dataclasses import asdict, dataclass
import json
from pathlib import Path
import time
from typing import Iterable

from .devices import ArmDevice
from .hardware_models import ArmMode
from .transports import LineTransport


class ArmTimeoutError(TimeoutError):
    """Raised after the arm fails to produce an expected reply in time."""


class ArmCommandCancelled(RuntimeError):
    """Raised when STOP interrupts a command while waiting for its reply."""


@dataclass(frozen=True)
class SerialPortInfo:
    device: str
    description: str
    vid: int | None
    pid: int | None
    is_ch340: bool


def discover_serial_ports(ports: Iterable[object] | None = None) -> list[SerialPortInfo]:
    if ports is None:
        try:
            from serial.tools import list_ports
        except ImportError as exc:  # pragma: no cover - environment dependent
            raise RuntimeError("pyserial is required for serial port discovery") from exc
        ports = list_ports.comports()
    result = []
    for port in ports:
        vid = getattr(port, "vid", None)
        pid = getattr(port, "pid", None)
        description = str(getattr(port, "description", ""))
        is_ch340 = (vid, pid) in {(0x1A86, 0x7523), (0x1A86, 0x5523)} or "CH340" in description.upper()
        result.append(SerialPortInfo(str(getattr(port, "device")), description, vid, pid, is_ch340))
    return result


class ArmSession:
    def __init__(
        self,
        arm: ArmDevice,
        transport: LineTransport,
        log_path: str | Path | None = None,
    ) -> None:
        self.arm = arm
        self.transport = transport
        self.log_path = Path(log_path).expanduser() if log_path else None
        self._closed = False
        self.last_operation_raw: list[str] = []

    def _record(self, direction: str, raw: str) -> None:
        if self.log_path is None:
            return
        self.log_path.parent.mkdir(parents=True, exist_ok=True)
        record = {"timestamp_ns": time.time_ns(), "source": "arm", "direction": direction, "raw": raw}
        with self.log_path.open("a", encoding="utf-8") as stream:
            stream.write(json.dumps(record, ensure_ascii=False) + "\n")

    def _send(self, raw: str, action) -> None:
        action()
        self._record("tx", raw)

    def stop(self) -> None:
        self._send("ARM,STOP", self.arm.stop)

    def ping(self) -> None:
        self._send("ARM,PING", self.arm.ping)

    def status(self) -> None:
        self._send("ARM,STATUS", self.arm.status)

    def enable(self) -> None:
        self._send("ARM,ENABLE", self.arm.enable)

    def run(self, routine: int) -> None:
        self._send(f"ARM,RUN,{routine}", lambda: self.arm.run(routine))

    def suction(self, enabled: bool) -> None:
        self._send(f"ARM,SUCTION,{int(enabled)}", lambda: self.arm.suction(enabled))

    def servo(self, servo_id: int, position: int, time_ms: int) -> None:
        self._send(f"ARM,SERVO,{int(servo_id)},{int(position)},{int(time_ms)}", lambda: self.arm.servo(servo_id, position, time_ms))

    def move(self, positions: list[int], time_ms: int) -> None:
        self._send("ARM,MOVE," + ",".join(str(int(value)) for value in positions) + f",{int(time_ms)}", lambda: self.arm.move(positions, time_ms))

    def poll(self):
        replies = self.arm.poll()
        for raw in self.arm.last_raw_lines:
            self._record("rx", raw)
        return replies

    def wait_for_ack(self, command: str, timeout_s: float, stop_requested=None) -> list:
        """Wait for a firmware ACK while allowing a pending STOP to preempt it."""
        deadline = time.monotonic() + timeout_s
        replies = []
        while time.monotonic() < deadline:
            if stop_requested is not None and stop_requested.is_set():
                raise ArmCommandCancelled(f"{command} cancelled before acknowledgement")
            batch = self.poll()
            replies.extend(batch)
            self.last_operation_raw.extend(self.arm.last_raw_lines)
            if any(getattr(reply, "command", None) == command for reply in batch):
                return replies
            time.sleep(min(0.02, max(0.0, deadline - time.monotonic())))
        raise ArmTimeoutError(f"arm command timed out waiting for ACK,{command}")

    def safe_probe(self, timeout_s: float = 2.0):
        deadline = time.monotonic() + timeout_s
        replies = []
        self.last_operation_raw = []

        stages = (
            (self.stop, lambda reply: getattr(reply, "command", None) == "STOPPED_LOCKED", "ACK,STOPPED_LOCKED"),
            (self.ping, lambda reply: getattr(reply, "command", None) == "PONG", "ACK,PONG"),
            (self.status, lambda reply: reply.__class__.__name__ == "ArmStateReply", "STATE"),
        )
        for index, (send, matches, expected) in enumerate(stages):
            if index and time.monotonic() >= deadline:
                raise ArmTimeoutError(f"arm probe timed out waiting for {expected}")
            send()
            self._wait_for_probe_reply(replies, matches, expected, deadline)
        return replies

    def _wait_for_probe_reply(self, replies, matches, expected: str, deadline: float) -> None:
        while True:
            if time.monotonic() >= deadline:
                raise ArmTimeoutError(f"arm probe timed out waiting for {expected}")
            batch = self.poll()
            replies.extend(batch)
            self.last_operation_raw.extend(self.arm.last_raw_lines)
            if time.monotonic() >= deadline:
                raise ArmTimeoutError(f"arm probe timed out waiting for {expected}")
            if any(matches(reply) for reply in batch):
                return
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise ArmTimeoutError(f"arm probe timed out waiting for {expected}")
            time.sleep(min(0.02, remaining))

    def wait_for_action(self, routine: int, timeout_s: float = 45.0):
        deadline = time.monotonic() + timeout_s
        replies = []
        while True:
            batch = self.poll()
            replies.extend(batch)
            if any(
                getattr(reply, "name", None) == "DONE" and getattr(reply, "value", None) == str(routine)
                for reply in batch
            ):
                return replies
            if self.arm.state.mode is ArmMode.FAULT:
                raise RuntimeError("arm entered FAULT during action")
            if time.monotonic() >= deadline:
                self.stop()
                raise ArmTimeoutError(f"arm action {routine} timed out and was stopped")
            time.sleep(0.02)

    def wait_for_mode(self, mode: ArmMode, timeout_s: float = 2.0):
        deadline = time.monotonic() + timeout_s
        while True:
            self.poll()
            if self.arm.state.mode is mode:
                return self.arm.state
            if self.arm.state.mode is ArmMode.FAULT:
                raise RuntimeError("arm entered FAULT")
            if time.monotonic() >= deadline:
                raise ArmTimeoutError(f"arm did not enter {mode.value} before timeout")
            time.sleep(0.02)

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        try:
            self.stop()
        finally:
            self.transport.close()


def execute_console_command(session: ArmSession, command_line: str) -> str:
    fields = command_line.strip().lower().split()
    if not fields:
        return ""
    command = fields[0]
    if command == "ping" and len(fields) == 1:
        session.ping()
        return "ping sent"
    if command == "status" and len(fields) == 1:
        session.status()
        return "status requested"
    if command == "stop" and len(fields) == 1:
        session.stop()
        return "arm stopped and locked"
    if command == "enable" and len(fields) == 1:
        session.enable()
        return "enable requested"
    if command == "run" and len(fields) == 2:
        if session.arm.state.mode is not ArmMode.READY:
            raise RuntimeError("arm must report READY before run")
        routine = int(fields[1])
        session.run(routine)
        return f"running action {routine}"
    if command == "suction" and len(fields) == 2 and fields[1] in {"on", "off"}:
        if session.arm.state.mode is not ArmMode.READY:
            raise RuntimeError("arm must report READY before suction")
        enabled = fields[1] == "on"
        session.suction(enabled)
        return f"suction {'on' if enabled else 'off'} requested"
    if command in {"quit", "exit"} and len(fields) == 1:
        return "quit"
    raise ValueError("unsupported command; use ping, status, stop, enable, run 0..5, suction on/off, quit")
