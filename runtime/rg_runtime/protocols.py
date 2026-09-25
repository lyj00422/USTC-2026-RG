"""ASCII command formatting and response parsing for the handed-off firmware."""

from __future__ import annotations

from dataclasses import dataclass


def _line(value: str) -> str:
    return value.rstrip("\r\n") + "\r\n"


def format_velocity(vx: int, vy: int, wz: int) -> str:
    return _line(f"V {int(vx)} {int(vy)} {int(wz)}")


def format_arm(command: str, *args: object) -> str:
    body = ",".join(["ARM", command, *(str(arg) for arg in args)])
    return _line(body)


@dataclass(frozen=True)
class ChassisReply:
    kind: str
    value: str


@dataclass(frozen=True)
class ArmAck:
    command: str
    value: str | None = None


@dataclass(frozen=True)
class ArmStateReply:
    mode: str
    routine: int | None
    step: int | None
    suction: int
    calibrated: int


@dataclass(frozen=True)
class ArmEvent:
    name: str
    value: str


@dataclass(frozen=True)
class ArmFault:
    value: str


def parse_chassis_reply(line: str) -> ChassisReply | None:
    value = line.strip()
    if value.startswith("Mecanum ready"):
        return ChassisReply("ready", value)
    if value.startswith("OK "):
        return ChassisReply("ok", value[3:].strip())
    if value.startswith("DONE "):
        return ChassisReply("done", value[5:].strip())
    if value.startswith("ERR:"):
        return ChassisReply("error", value[4:].strip())
    if value.startswith("ERR "):
        return ChassisReply("error", value[4:].strip())
    if value.startswith("ENC "):
        return ChassisReply("encoder", value[4:].strip())
    if value.startswith("SPD "):
        return ChassisReply("speed", value[4:].strip())
    return None


def parse_arm_reply(line: str) -> ArmAck | ArmStateReply | ArmEvent | ArmFault | None:
    fields = [field.strip() for field in line.strip().split(",")]
    if len(fields) >= 2 and fields[0] == "ACK":
        return ArmAck(fields[1], fields[2] if len(fields) > 2 else None)
    if len(fields) == 2 and fields[0] == "EVENT":
        return ArmEvent(fields[1], "")
    if len(fields) == 3 and fields[0] == "EVENT":
        return ArmEvent(fields[1], fields[2])
    if len(fields) == 2 and fields[0] == "FAULT":
        return ArmFault(fields[1])
    if len(fields) >= 2 and fields[0] == "STATE":
        values = {item.split("=", 1)[0]: item.split("=", 1)[1] for item in fields[2:] if "=" in item}
        try:
            routine = int(values["ROUTINE"])
            step = int(values["STEP"])
            suction = int(values["SUCTION"])
            calibrated = int(values["CAL"])
        except (KeyError, ValueError):
            return None
        if calibrated not in (0, 1) or suction not in (0, 1):
            return None
        return ArmStateReply(fields[1], None if routine == 255 else routine, step, suction, calibrated)
    return None
