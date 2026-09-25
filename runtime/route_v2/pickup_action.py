from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
import json
from pathlib import Path
from types import MappingProxyType
from typing import Mapping


_PURPLE_PICKUP_ACTION_NAME = "拿紫色v1_有抓取窗口"


@dataclass(frozen=True)
class CompiledActionStep:
    kind: str
    servo_id: int | None = None
    position: int | None = None
    time_ms: int | None = None
    vx: int = 0
    vy: int = 0
    wz: int = 0
    duration_s: float = 0.0
    enabled: bool | None = None


def load_action_package(
    path: str | Path,
    *,
    expected_name: str = _PURPLE_PICKUP_ACTION_NAME,
    require_capture_window: bool = True,
) -> Mapping[str, object]:
    root = Path(path)
    action = json.loads((root / "action.json").read_text(encoding="utf-8"))
    zones = json.loads((root / "zones.json").read_text(encoding="utf-8"))
    if not isinstance(action, dict) or not isinstance(zones, dict):
        raise ValueError("action package files must contain JSON objects")
    if action.get("protocol") != "RG-ARM-1" or action.get("name") != expected_name:
        raise ValueError(f"unexpected action package: expected {expected_name}")
    if zones != action.get("zones"):
        raise ValueError("action and zones files differ")
    if require_capture_window and "capture" not in zones:
        raise ValueError("action package needs a capture window")
    steps = action.get("steps")
    if not isinstance(steps, list) or not steps:
        raise ValueError("action package needs at least one step")
    return MappingProxyType({"action": action, "zones": zones})


def load_action_catalog(path: str | Path) -> Mapping[str, Mapping[str, object]]:
    """Load the runtime action catalog and validate every referenced package."""
    root = Path(path)
    document = json.loads((root / "catalog.json").read_text(encoding="utf-8"))
    entries = document.get("catalog") if isinstance(document, dict) else None
    if not isinstance(entries, dict) or not entries:
        raise ValueError("action catalog must contain catalog entries")
    loaded: dict[str, Mapping[str, object]] = {}
    for role, entry in entries.items():
        if not isinstance(entry, dict) or not isinstance(entry.get("path"), str):
            raise ValueError(f"invalid action catalog entry: {role}")
        package = load_action_package(
            root / entry["path"],
            expected_name=str(entry.get("name", "")),
            require_capture_window=entry.get("window_required", role == "purple_pickup"),
        )
        loaded[str(role)] = package
    return MappingProxyType(loaded)


def _integer(value: object, name: str, minimum: int, maximum: int) -> int:
    if type(value) is not int or not minimum <= value <= maximum:
        raise ValueError(f"{name} must be an integer in {minimum}..{maximum}")
    return value


def _timestamp(value: object, name: str) -> datetime:
    if not isinstance(value, str):
        raise ValueError(f"{name} must be an ISO-8601 timestamp")
    try:
        return datetime.fromisoformat(value)
    except ValueError as exc:
        raise ValueError(f"{name} must be an ISO-8601 timestamp") from exc


def compile_action(
    package: Mapping[str, object], *, forward_speed_limit: int = 80,
    expected_suction: tuple[bool, ...] = (True,),
) -> tuple[CompiledActionStep, ...]:
    # Ceiling raised 20 -> 40 on 2026-09-22 at the operator's request (they want
    # the packages' forward speed at 21).  It is a sanity bound, not a tuning
    # value: the clamp itself is `vx = min(vx, forward_speed_limit)` below, and
    # the recorded packages carry vx = +-20, so the clamp only starts to matter
    # once the package DATA is raised too.
    if type(forward_speed_limit) is not int or not 1 <= forward_speed_limit <= 80:
        raise ValueError("forward_speed_limit must be an integer in 1..80")
    action = package.get("action")
    if not isinstance(action, dict) or not isinstance(action.get("steps"), list):
        raise ValueError("invalid loaded action package")
    steps = action["steps"]
    compiled: list[CompiledActionStep] = []
    index = 0
    while index < len(steps):
        raw = steps[index]
        if not isinstance(raw, dict):
            raise ValueError(f"action step {index} must be an object")
        kind = raw.get("kind")
        if kind == "SERVO":
            compiled.append(CompiledActionStep(
                kind="servo",
                servo_id=_integer(raw.get("id"), f"steps[{index}].id", 0, 4),
                position=_integer(raw.get("position"), f"steps[{index}].position", 500, 2500),
                time_ms=_integer(raw.get("time_ms"), f"steps[{index}].time_ms", 100, 10000),
            ))
            index += 1
            continue
        if kind == "SUCTION":
            enabled = raw.get("enabled")
            if type(enabled) is not bool:
                raise ValueError(f"steps[{index}].enabled must be a boolean")
            compiled.append(CompiledActionStep(kind="suction", enabled=enabled))
            index += 1
            continue
        if kind != "CHASSIS":
            raise ValueError(f"unsupported action step kind at index {index}: {kind}")
        command = raw.get("command")
        if command == "stop":
            index += 1
            continue
        if command != "velocity":
            raise ValueError(f"unsupported chassis command at index {index}: {command}")
        if index + 1 >= len(steps):
            raise ValueError(f"chassis velocity at index {index} is missing its stop")
        following = steps[index + 1]
        if not isinstance(following, dict) or following.get("kind") != "CHASSIS" \
                or following.get("command") != "stop":
            raise ValueError(f"chassis velocity at index {index} must be followed by stop")
        started = _timestamp(raw.get("timestamp"), f"steps[{index}].timestamp")
        stopped = _timestamp(following.get("timestamp"), f"steps[{index + 1}].timestamp")
        duration_s = (stopped - started).total_seconds()
        if duration_s <= 0:
            raise ValueError(f"chassis velocity at index {index} has a non-positive duration")
        vx = _integer(raw.get("vx"), f"steps[{index}].vx", -100, 100)
        vy = _integer(raw.get("vy"), f"steps[{index}].vy", -100, 100)
        wz = _integer(raw.get("wz"), f"steps[{index}].wz", -100, 100)
        if vx > 0:
            vx = min(vx, forward_speed_limit)
        compiled.append(CompiledActionStep(
            kind="chassis_velocity", vx=vx, vy=vy, wz=wz, duration_s=duration_s,
        ))
        index += 2
    if not compiled:
        raise ValueError("action package compiles to no executable steps")
    suction = tuple(step.enabled for step in compiled if step.kind == "suction")
    if suction != expected_suction:
        if expected_suction == (True,):
            raise ValueError("purple pickup action must contain exactly one SUCTION,true")
        raise ValueError(f"action suction sequence must be {expected_suction}")
    return tuple(compiled)


@dataclass(frozen=True)
class PickupActionResult:
    done: bool
    metadata: Mapping[str, object]
    chassis_velocity: tuple[int, int, int] | None = None
    chassis_stop: bool = False
    chassis_active: bool = False
    fault: str | None = None


class ActionPackageExecutor:
    """Advance one validated action package without blocking the route tick."""

    def __init__(
        self,
        steps: tuple[CompiledActionStep, ...],
        arm,
        *,
        ack_timeout_s: float = 1.0,
        action_ref: str = "action_package:purple_pickup_v1",
    ) -> None:
        if not steps:
            raise ValueError("action package executor needs at least one step")
        if ack_timeout_s <= 0 or not action_ref:
            raise ValueError("ack_timeout_s and action_ref are required")
        self.steps = tuple(steps)
        self.arm = arm
        self.ack_timeout_s = float(ack_timeout_s)
        self.action_ref = action_ref
        self.reset()

    def reset(self) -> None:
        self._index = 0
        self._started = False
        self._done = False
        self._fault: str | None = None
        self._waiting_command: str | None = None
        self._ack_deadline: float | None = None
        self._wait_until: float | None = None
        self._motion_until: float | None = None
        self._suction_enabled = False

    @property
    def suction_enabled(self) -> bool:
        return self._suction_enabled

    def _result(
        self,
        *,
        chassis_velocity: tuple[int, int, int] | None = None,
        chassis_stop: bool = False,
    ) -> PickupActionResult:
        if self._fault is not None:
            status = "fault"
        elif self._done:
            status = "complete"
        elif self._started:
            status = "running"
        else:
            status = "waiting_for_stop"
        metadata = MappingProxyType({
            "action_ref": self.action_ref,
            "executed": self._started,
            "result": status,
            "step_index": self._index,
            "step_count": len(self.steps),
        })
        return PickupActionResult(
            done=self._done,
            metadata=metadata,
            chassis_velocity=chassis_velocity,
            chassis_stop=chassis_stop,
            chassis_active=self._motion_until is not None and not chassis_stop,
            fault=self._fault,
        )

    def _fail(self, message: str) -> PickupActionResult:
        self._fault = message
        return self._result(chassis_stop=True)

    def step(self, *, now: float, stop_acknowledged: bool) -> PickupActionResult:
        if self._fault is not None or self._done:
            return self._result()
        if not self._started:
            if not stop_acknowledged:
                return self._result()
            self._started = True
        try:
            if self._waiting_command is not None:
                replies = self.arm.poll()
                if any(
                    getattr(reply, "command", None) == self._waiting_command
                    for reply in replies
                ):
                    current = self.steps[self._index]
                    self._waiting_command = None
                    self._ack_deadline = None
                    if current.kind == "servo":
                        self._wait_until = now + current.time_ms / 1000.0
                    else:
                        self._suction_enabled = bool(current.enabled)
                        self._index += 1
                        if self._index >= len(self.steps):
                            self._done = True
                    return self._result()
                if self._ack_deadline is not None and now >= self._ack_deadline:
                    return self._fail(
                        f"arm {self._waiting_command} acknowledgement timed out"
                    )
                return self._result()

            if self._wait_until is not None:
                if now < self._wait_until:
                    return self._result()
                self._wait_until = None
                self._index += 1

            if self._motion_until is not None:
                if now < self._motion_until:
                    return self._result()
                self._motion_until = None
                self._index += 1
                if self._index >= len(self.steps):
                    self._done = True
                return self._result(chassis_stop=True)

            if self._index >= len(self.steps):
                self._done = True
                return self._result()

            current = self.steps[self._index]
            if current.kind == "servo":
                self.arm.servo(current.servo_id, current.position, current.time_ms)
                self._waiting_command = "SERVO"
                self._ack_deadline = now + self.ack_timeout_s
                return self._result()
            if current.kind == "suction":
                self.arm.suction(current.enabled)
                self._waiting_command = "SUCTION"
                self._ack_deadline = now + self.ack_timeout_s
                return self._result()
            if current.kind == "chassis_velocity":
                self._motion_until = now + current.duration_s
                return self._result(
                    chassis_velocity=(current.vx, current.vy, current.wz)
                )
            return self._fail(f"unsupported compiled action step: {current.kind}")
        except Exception as exc:
            return self._fail(f"pickup action failed: {exc}")


class ActionCatalogExecutor:
    """Select one compiled action from a catalog without blocking the route."""

    def __init__(self, actions: Mapping[str, tuple[CompiledActionStep, ...]], arm,
                 *, ack_timeout_s: float = 1.0, action_ref_prefix: str = "action_package"):
        if not actions:
            raise ValueError("action catalog executor needs actions")
        self._actions = dict(actions)
        self.arm = arm
        self.ack_timeout_s = float(ack_timeout_s)
        self.action_ref_prefix = action_ref_prefix
        self.action = next(iter(self._actions))
        self._executor = ActionPackageExecutor(
            self._actions[self.action], arm, ack_timeout_s=ack_timeout_s,
            action_ref=f"{action_ref_prefix}:{self.action}",
        )

    def set_action(self, action: str) -> None:
        if action not in self._actions:
            raise ValueError(f"unknown action: {action}")
        self.action = action
        self._executor = ActionPackageExecutor(
            self._actions[action], self.arm, ack_timeout_s=self.ack_timeout_s,
            action_ref=f"{self.action_ref_prefix}:{action}",
        )

    def reset(self) -> None:
        self._executor.reset()

    def step(self, *, now: float, stop_acknowledged: bool) -> PickupActionResult:
        result = self._executor.step(now=now, stop_acknowledged=stop_acknowledged)
        metadata = dict(result.metadata)
        metadata["selected_action"] = self.action
        return PickupActionResult(
            done=result.done, metadata=MappingProxyType(metadata),
            chassis_velocity=result.chassis_velocity, chassis_stop=result.chassis_stop,
            chassis_active=result.chassis_active, fault=result.fault,
        )


class VisionOnlyPickupExecutor:
    """Three-second visual placeholder. It deliberately has no arm dependency."""

    def __init__(self, *, wait_s: float = 3.0, action_ref: str = "firmware_routine:3"):
        if wait_s <= 0 or not action_ref:
            raise ValueError("wait_s and action_ref are required")
        self.wait_s = float(wait_s)
        self.action_ref = action_ref
        self._started_at: float | None = None
        self._metadata = MappingProxyType({
            "action_ref": action_ref,
            "executed": False,
            "result": "vision_only_complete",
        })

    def reset(self) -> None:
        self._started_at = None

    def set_action(self, action: str) -> None:
        if not action:
            raise ValueError("action is required")
        self.action = action
        self.reset()

    def step(self, *, now: float, stop_acknowledged: bool) -> PickupActionResult:
        if self._started_at is None and stop_acknowledged:
            self._started_at = float(now)
        done = self._started_at is not None and now - self._started_at >= self.wait_s
        return PickupActionResult(done=done, metadata=self._metadata)


class PlaceholderActionExecutor:
    """Hardware independent three-second placeholder for an arm action.

    The chassis is held stopped for the complete interval.  Completion is the
    only success signal; no real arm command is sent until action packages are
    supplied.
    """

    def __init__(self, action: str, *, wait_s: float = 3.0) -> None:
        if not action or wait_s <= 0:
            raise ValueError("action and wait_s are required")
        self.action = action
        self.wait_s = float(wait_s)
        self._started_at: float | None = None

    def reset(self) -> None:
        self._started_at = None

    def step(self, *, now: float, stop_acknowledged: bool) -> PickupActionResult:
        if self._started_at is None:
            if not stop_acknowledged:
                return PickupActionResult(
                    done=False,
                    metadata=MappingProxyType({"action": self.action, "executed": False}),
                    chassis_stop=True,
                )
            self._started_at = float(now)
        done = float(now) - self._started_at >= self.wait_s
        metadata = MappingProxyType({
            "action": self.action,
            "executed": True,
            "result": "action_done" if done else "running",
        })
        return PickupActionResult(done=done, metadata=metadata, chassis_stop=True)
