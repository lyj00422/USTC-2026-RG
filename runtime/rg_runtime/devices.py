"""Device adapters for the existing chassis and arm ASCII firmware."""

from __future__ import annotations

from dataclasses import dataclass, replace

from .hardware_models import ArmMode, ArmState, DeviceHealth
from .protocols import (
    ArmAck,
    ArmEvent,
    ArmFault,
    ArmStateReply,
    ChassisReply,
    format_arm,
    format_velocity,
    parse_arm_reply,
    parse_chassis_reply,
)
from .transports import LineTransport


@dataclass(frozen=True)
class ArmEventRecord:
    command: str
    value: str | None = None


class ChassisDevice:
    def __init__(self, transport: LineTransport) -> None:
        self.transport = transport
        self.health = DeviceHealth()
        self.last_reply: ChassisReply | None = None

    def set_velocity(self, vx: int, vy: int, wz: int) -> None:
        self.transport.send_line(format_velocity(vx, vy, wz))

    def stop(self) -> None:
        self.transport.send_line("STOP\r\n")

    def run_distance(self, forward_cm: int, right_cm: int, rotate_deg: int, speed: int) -> None:
        # int() is load-bearing, not cosmetic.  The firmware parses integers and
        # rejects anything else outright: measured on 2026-09-14,
        #   "D 0 -1 0 20"   -> OK D POSITION DONE D
        #   "D 0 -1.0 0 20" -> ERR: use V, M, D, SEQ or STOP
        # A float that reaches here (a YAML value written as 80.0, a computed
        # distance) would silently stall the route at that state.  V already
        # coerces; D did not.
        self.transport.send_line(
            f"D {int(forward_cm)} {int(right_cm)} {int(rotate_deg)} {int(speed)}\r\n"
        )

    def run_sequence(self) -> None:
        self.transport.send_line("SEQ\r\n")

    def request_encoder(self) -> None:
        self.transport.send_line("ENC\r\n")

    def request_speed(self) -> None:
        self.transport.send_line("SPD\r\n")

    def motor_test(self, wheel: str, speed: int) -> None:
        self.transport.send_line(f"M {wheel} {speed}\r\n")

    def reset_encoder(self) -> None:
        self.transport.send_line("ENC RESET\r\n")

    def poll(self) -> list[ChassisReply]:
        replies = [reply for line in self.transport.read_lines() if (reply := parse_chassis_reply(line))]
        if replies:
            self.last_reply = replies[-1]
            self.health = DeviceHealth(True, self.health.last_rx_ms, None)
        return replies


class ArmDevice:
    def __init__(self, transport: LineTransport) -> None:
        self.transport = transport
        self.state = ArmState()
        self.health = DeviceHealth()
        self.last_done_routine: int | None = None
        self.last_raw_lines: list[str] = []

    def ping(self) -> None:
        self.transport.send_line(format_arm("PING"))

    def status(self) -> None:
        self.transport.send_line(format_arm("STATUS"))

    def enable(self) -> None:
        if self.state.mode is not ArmMode.LOCKED or self.state.calibrated is not True:
            raise RuntimeError("arm must report calibrated LOCKED state before enable")
        self.transport.send_line(format_arm("ENABLE"))

    def run(self, routine: int) -> None:
        if self.state.mode is not ArmMode.READY:
            raise RuntimeError("arm must report READY before run")
        if not 0 <= routine <= 5:
            raise ValueError("routine must be between 0 and 5")
        self.last_done_routine = None
        self.transport.send_line(format_arm("RUN", routine))

    def suction(self, enabled: bool) -> None:
        if self.state.mode is not ArmMode.READY:
            raise RuntimeError("arm must report READY before suction")
        if type(enabled) is not bool:
            raise ValueError("enabled must be a boolean")
        self.transport.send_line(format_arm("SUCTION", int(enabled)))
        self.state = replace(self.state, suction_commanded=enabled)

    def servo(self, servo_id: int, position: int, time_ms: int) -> None:
        if self.state.mode is not ArmMode.READY:
            raise RuntimeError("arm must report READY before manual servo control")
        if type(servo_id) is not int or not 0 <= servo_id <= 4:
            raise ValueError("servo_id must be between 0 and 4")
        if type(position) is not int or not 500 <= position <= 2500:
            raise ValueError("position must be between 500 and 2500")
        if type(time_ms) is not int or not 100 <= time_ms <= 10000:
            raise ValueError("time_ms must be between 100 and 10000")
        self.transport.send_line(format_arm("SERVO", servo_id, position, time_ms))

    def move(self, positions: list[int], time_ms: int) -> None:
        if self.state.mode is not ArmMode.READY:
            raise RuntimeError("arm must report READY before manual pose control")
        if len(positions) != 5:
            raise ValueError("positions must contain five values")
        if any(type(value) is not int for value in positions):
            raise ValueError("positions must be integers")
        values = list(positions)
        if any(not 500 <= value <= 2500 for value in values):
            raise ValueError("positions must be between 500 and 2500")
        if type(time_ms) is not int or not 100 <= time_ms <= 10000:
            raise ValueError("time_ms must be between 100 and 10000")
        self.transport.send_line(format_arm("MOVE", *values, time_ms))

    def stop(self) -> None:
        self.transport.send_line(format_arm("STOP"))
        self.state = ArmState(mode=ArmMode.LOCKED, calibrated=self.state.calibrated)

    def poll(self) -> list[ArmAck | ArmStateReply | ArmEvent | ArmFault]:
        events = []
        self.last_raw_lines = self.transport.read_lines()
        for line in self.last_raw_lines:
            reply = parse_arm_reply(line)
            if reply is None:
                continue
            events.append(reply)
            self.health = DeviceHealth(True, self.health.last_rx_ms, None)
            if isinstance(reply, ArmAck):
                if reply.command == "ENABLED":
                    self.state = ArmState(ArmMode.READY, suction_commanded=self.state.suction_commanded, calibrated=self.state.calibrated)
                elif reply.command == "RUN":
                    routine = int(reply.value) if reply.value is not None else None
                    self.state = ArmState(ArmMode.BUSY, routine=routine, suction_commanded=self.state.suction_commanded, calibrated=self.state.calibrated)
            elif isinstance(reply, ArmStateReply):
                mode = ArmMode(reply.mode) if reply.mode in {item.value for item in ArmMode} else ArmMode.UNKNOWN
                self.state = ArmState(mode, reply.routine, reply.step, bool(reply.suction), bool(reply.calibrated))
            elif isinstance(reply, ArmEvent) and reply.name == "DONE":
                self.last_done_routine = int(reply.value) if reply.value.isdigit() else None
                self.state = ArmState(ArmMode.READY, suction_commanded=self.state.suction_commanded, calibrated=self.state.calibrated)
            elif isinstance(reply, ArmFault):
                self.state = ArmState(ArmMode.FAULT, calibrated=self.state.calibrated)
        return events
