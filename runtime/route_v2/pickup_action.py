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
    # `chassis_distance`: a firmware `D` move, which is closed-loop on its own
    # encoders rather than timed here.  `forward_cm` is signed -- negative is
    # reverse -- matching `travel_cm`'s sign, which is the only convention the
    # codebase demonstrates (no package has used a negative D yet, so this is
    # the one part of the D format that is reasoned rather than measured).
    forward_cm: int = 0
    right_cm: int = 0
    rotate_deg: int = 0
    speed: int = 0


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
        if command == "distance":
            # Distance moves are what an automation package uses.  `velocity`
            # plus a recorded stop is how the console captures a hand-driven
            # nudge, and those durations are whatever the operator's thumb did
            # (measured: four 0.06-0.18 s taps where a 4 cm approach belonged),
            # so a package that means to travel a distance must say so.
            forward_cm = _integer(raw.get("forward_cm"), f"steps[{index}].forward_cm", -400, 400)
            right_cm = _integer(raw.get("right_cm", 0), f"steps[{index}].right_cm", -400, 400)
            rotate_deg = _integer(raw.get("rotate_deg", 0), f"steps[{index}].rotate_deg", -360, 360)
            speed = _integer(raw.get("speed"), f"steps[{index}].speed", 1, 100)
            if forward_cm == 0 and right_cm == 0 and rotate_deg == 0:
                raise ValueError(f"steps[{index}] distance command moves nowhere")
            compiled.append(CompiledActionStep(
                kind="chassis_distance",
                forward_cm=forward_cm, right_cm=right_cm,
                rotate_deg=rotate_deg, speed=speed,
            ))
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
    # (forward_cm, right_cm, rotate_deg, speed) for a `D` move, emitted exactly
    # once.  The firmware RESTARTS a `D` it is sent again -- unlike STOP, which is
    # idempotent -- so a re-send mid-move drives the car further every time.  The
    # runner must therefore send this on the tick it appears and never repeat it.
    chassis_distance: tuple[int, int, int, int] | None = None
    # The value of a `SUCTION` command issued this tick, or None when the step
    # that ran was not a suction step.  The runner keeps the route-level latch
    # from this, because that latch must outlive the executor:
    # `ActionCatalogExecutor.set_action` builds a NEW `ActionPackageExecutor`
    # per action, so a latch held inside one is silently forgotten at every
    # action switch -- which is exactly when the car is carrying a block to the
    # build area.
    suction: bool | None = None
    # Set for exactly one returned result when a `D` was accepted by the firmware
    # and never reported DONE within `distance_timeout_s`.  This is NOT a fault:
    # see the branch in `step()` for why.  Carries the command that timed out so
    # the runner can name it.
    distance_timeout: tuple[int, int, int, int] | None = None


class ActionPackageExecutor:
    """Advance one validated action package without blocking the route tick."""

    def __init__(
        self,
        steps: tuple[CompiledActionStep, ...],
        arm,
        *,
        ack_timeout_s: float = 1.0,
        # 10.0 -> 5.0 on 2026-10-01, at the operator's request: 「吸橙色到右边的时候
        # 到槽内要放的时候 有长时间的停顿」.  Measured, that pause is NOT a
        # designed hold -- the deposit steps themselves take 0.11-0.37 s -- it is
        # this deadline running out when a `D` is lost (the .out says
        # `ACTION D 3 0 0 20 reported no DONE`).  10 s of standing still is what the
        # operator sees.
        #
        # 5.0 is still ~2x the longest move any package makes: the biggest is
        # orange_left's `D +17`, measured at ~2.4 s.  The reason this must stay
        # comfortably above the real duration is recorded below -- a 2 s deadline was
        # once SHORTER than the moves and cut them short, which the operator read as
        # 「抓取的后退总是执行没成功」.
        distance_timeout_s: float = 5.0,
        distance_verdict_s: float = 0.5,
        distance_resend_settle_s: float = 0.3,
        max_distance_retries: int = 1,
        suction_settle_s: float = 0.0,
        arm_lift_settle_s: float = 0.0,
        arm_move_settle_s: float = 0.0,
        arm_lift_servo_id: int = 1,
        action_ref: str = "action_package:purple_pickup_v1",
    ) -> None:
        if not steps:
            raise ValueError("action package executor needs at least one step")
        if ack_timeout_s <= 0 or not action_ref:
            raise ValueError("ack_timeout_s and action_ref are required")
        if distance_timeout_s <= 0:
            raise ValueError("distance_timeout_s must be positive")
        if distance_verdict_s <= 0:
            raise ValueError("distance_verdict_s must be positive")
        if distance_resend_settle_s < 0:
            raise ValueError("distance_resend_settle_s cannot be negative")
        if max_distance_retries < 0:
            raise ValueError("max_distance_retries cannot be negative")
        if suction_settle_s < 0 or arm_lift_settle_s < 0 or arm_move_settle_s < 0:
            raise ValueError("settle times cannot be negative")
        self.steps = tuple(steps)
        self.arm = arm
        self.ack_timeout_s = float(ack_timeout_s)
        # 30.0 -> 5.0 on 2026-09-29, in two steps, and the second one is the one
        # that matters.
        #
        # 30 s was cut after ONE lost `D` made the whole route look hung: the arm
        # did its two opening servo moves, then nothing moved for 30 s while this
        # deadline ran out, and the operator read that as 机械臂卡死.
        #
        # 2 s then turned out to be SHORTER THAN THE MOVES THEMSELVES, which is
        # worse than it sounds.  Measured on the bench, wheels off the ground:
        #
        #     D 12 0 0 20   ->  +12.10 cm, DONE in 1.60 s
        #     D -12 0 0 20  ->  -12.15 cm, DONE in 1.60 s
        #
        # i.e. about 0.133 s/cm at speed 20, and the packages ask for moves up to
        # 17 cm -- 2.3 s, past a 2 s deadline.  The executor STOPped mid-move and
        # reported "no DONE", which is exactly what the operator saw as 抓取的后退
        # 总是执行没成功.  The loss counts agree: `D 16` (16 cm, the longest of the
        # common ones) accounted for 26 of 40 recorded losses, while `D -12`
        # (1.60 s, under the deadline) lost only 4, and `D 3` is short enough to
        # have been lost for a different, rarer reason.
        #
        # 10 s on the operator's call (2026-09-29).  The bench figures above are
        # for a car at rest on a stand; a loaded chassis on a low battery is
        # slower, and 10 s gives the longest move better than 4x the measured
        # time, so a move has to be genuinely lost -- not merely slow -- before
        # this deadline matters.
        #
        # A move cut short is the expensive failure: the package advances anyway,
        # so the car ends up short of where the step expected it, with nothing in
        # the log except the "no DONE" line.  Waiting too long, by contrast, is
        # visible and costs only time -- and with the deadline no longer below the
        # moves' own duration, that wait should now be rare.
        self.distance_timeout_s = float(distance_timeout_s)
        # How long to wait, after a `D` deadline expires, before deciding what
        # to do about it.  The move is stopped first and the chassis is then left
        # alone, so the runner's encoder polls can answer the only question that
        # matters: did the car move at all?
        #
        # That question is the whole reason a naive retry is unsafe.  The
        # firmware RESTARTS a re-sent `D`, so re-issuing a move that was merely
        # slow drives it a second time from wherever it had got to.  0.5 s is
        # ~4 poll ticks at the runner's 0.15 s cadence -- enough for a reading
        # that postdates the STOP.
        self.distance_verdict_s = float(distance_verdict_s)
        # Quiet gap between the retry's STOP and the re-sent `D`.
        #
        # This is the state machine's own proven recipe, copied: its `d` branch
        # flushes a STOP and then waits `D_SETTLE_S` before sending, because
        # "the chassis answers only the FIRST command of a burst" -- a `D`
        # arriving inside a STOP/V stream is dropped, and on 2026-09-22 that was
        # measured swallowing turn after turn until the flush was added.  The
        # first retry (2026-10-03, `route_v2_full_20261003_122052`) re-sent the
        # `D` with no gap of its own and the car still did not move, so the
        # re-send gets the same treatment the turns got.
        self.distance_resend_settle_s = float(distance_resend_settle_s)
        self._distance_resend_at: float | None = None
        # How many times a `D` may be RE-SENT when the verdict says the car never
        # moved.  One: a second identical loss means something the route cannot
        # fix by asking again, and the operator wants the arm to carry on rather
        # than the run to stall.
        self.max_distance_retries = int(max_distance_retries)
        self._distance_retries = 0
        self._distance_verdict_at: float | None = None
        # Hold after the arm acknowledges a SUCTION-ON step, before the next step
        # runs.  Operator, 2026-09-29, for the purple pickup: 「要求吸的动作执行
        # 之后 停顿1s」 -- the vacuum needs a moment to take hold, and the recorded
        # package goes straight from `SUCTION true` into the lift.
        #
        # 0.0 for every package except the one the runner hands a value to, so the
        # orange picks behave exactly as before.
        self.suction_settle_s = float(suction_settle_s)
        # Hold after the 大臂 has finished RISING, before the next step runs.
        # Operator, 2026-09-29: 「大臂抬起完成 停顿1s 再进行下一步动作」.
        #
        # 大臂 is servo id 1 -- taken from the console's own axis labels, which are
        # the only place the joints are named (control_hub/static/operate.html:
        # 轴1 底座=0, 轴2 大臂=1, 轴3 小臂=2, 轴4 腕部=3, 轴5 夹具=4).
        #
        # Only a RISE counts.  A reach DOWN to a block gets no hold -- the operator
        # asked for the pause on the lift, and pausing on the way in as well would
        # add a second dead second to every grab for nothing.
        self.arm_lift_settle_s = float(arm_lift_settle_s)
        # Hold after EVERY 大臂 move, whichever way it went -- the BUILD half of
        # the same rule.  Operator, 2026-09-29: 「搭建动作中 大臂会分步骤下移 所以
        # 在那样的情况下 大臂移动一次停顿1s」.
        #
        # `arm_lift_settle_s` above cannot express this: it is gated on a RISE,
        # and the build packages spend their 大臂 moves going the other way.  The
        # descent is what needs the hold here -- the recorder walks the arm down
        # to a layer in more than one servo step with a chassis nudge in between
        # (`build_base`: id1 1200 -> nudge -> id1 1000), and the nudge must not
        # start while the arm is still moving.
        #
        # 0.0 for every package except the build catalog the runner hands a value
        # to, so the pickup actions keep exactly the two holds they already had.
        self.arm_move_settle_s = float(arm_move_settle_s)
        self.arm_lift_servo_id = int(arm_lift_servo_id)
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
        # A deliberate hold that is NOT a step: unlike `_wait_until`, letting this
        # expire must not advance the index, so it is kept separately.  Both
        # settles in the pickup actions (after the suction takes, and after the
        # 大臂 finishes rising) run through here.
        self._settle_until: float | None = None
        # Decided when a servo stop is ISSUED, honoured when its own time_ms has
        # elapsed -- the hold is "the arm has arrived", not "the command went out".
        self._arm_lift_pending = False
        # Same shape, for the build rule: true for one wait whenever the step
        # that went out moved the 大臂 at all.  Only ever set when
        # `arm_move_settle_s > 0`, so the expiry check needs no second guard.
        self._arm_move_pending = False
        # Last commanded position per servo, so "rising" can be told from
        # "reaching down".  Package-scoped: cleared by reset().
        self._last_servo_position: dict[int, int] = {}
        self._motion_until: float | None = None
        self._distance_deadline: float | None = None
        self._suction_enabled = False
        # Set for exactly one returned result when a SUCTION step goes out, so
        # the runner can keep the route-level latch.  Cleared by `_result`.
        self._suction_command: bool | None = None
        self._distance_timeout: tuple[int, int, int, int] | None = None
        # The `D` currently in flight, kept so a timeout can name it.
        self._last_distance_command: tuple[int, int, int, int] | None = None

    @property
    def suction_enabled(self) -> bool:
        return self._suction_enabled

    def _result(
        self,
        *,
        chassis_velocity: tuple[int, int, int] | None = None,
        chassis_stop: bool = False,
        chassis_distance: tuple[int, int, int, int] | None = None,
    ) -> PickupActionResult:
        # Take-and-clear: a suction command is reported on exactly one result.
        # `step` returns one result per call, so nothing else can consume it.
        suction_command = self._suction_command
        self._suction_command = None
        # Same take-and-clear for a lost `D`.
        distance_timeout = self._distance_timeout
        self._distance_timeout = None
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
            # A D move owns the chassis for its whole flight, not just the tick
            # that issues it: the route must not send its own STOP or queries
            # into a closed-loop move.
            chassis_active=(self._motion_until is not None or self._distance_deadline is not None)
            and not chassis_stop,
            fault=self._fault,
            chassis_distance=chassis_distance,
            suction=suction_command,
            distance_timeout=distance_timeout,
        )

    def _fail(self, message: str) -> PickupActionResult:
        self._fault = message
        return self._result(chassis_stop=True)

    def step(
        self, *, now: float, stop_acknowledged: bool, chassis_done: bool = False,
        chassis_moved: bool | None = None,
    ) -> PickupActionResult:
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
                        if current.enabled and self.suction_settle_s > 0:
                            # Suction ON and acknowledged: hold before the lift, so
                            # the vacuum can take hold of the block.  The state is
                            # already applied above, so every tick of the hold
                            # reports the pump as on.
                            self._settle_until = now + self.suction_settle_s
                    return self._result()
                if self._ack_deadline is not None and now >= self._ack_deadline:
                    return self._fail(
                        f"arm {self._waiting_command} acknowledgement timed out"
                    )
                return self._result()

            if self._settle_until is not None:
                if now < self._settle_until:
                    return self._result()
                self._settle_until = None

            if self._wait_until is not None:
                if now < self._wait_until:
                    return self._result()
                self._wait_until = None
                self._index += 1
                if self._arm_lift_pending and self.arm_lift_settle_s > 0:
                    # The 大臂 has just finished rising: hold before the next step.
                    # The index has already moved past the servo stop, so nothing
                    # is skipped when this expires.
                    self._arm_lift_pending = False
                    # A rise satisfies both rules; take the pickup hold once.
                    self._arm_move_pending = False
                    self._settle_until = now + self.arm_lift_settle_s
                    return self._result()
                if self._arm_move_pending:
                    # Build rule: the 大臂 has finished moving, either way.  Note
                    # there is no `time_ms` on top of this -- this expiry happens
                    # after the servo's own travel time has already elapsed.
                    self._arm_move_pending = False
                    self._settle_until = now + self.arm_move_settle_s
                    return self._result()

            if self._motion_until is not None:
                if now < self._motion_until:
                    return self._result()
                self._motion_until = None
                self._index += 1
                if self._index >= len(self.steps):
                    self._done = True
                return self._result(chassis_stop=True)

            # A `D` move is closed-loop in the firmware: it reports `DONE` when
            # the encoders say the distance is covered, which is not a duration
            # we could guess.  Wait for that report, and on no account re-send --
            # the firmware restarts a re-sent D, so the car would drive the move
            # again from wherever it had got to.
            if self._distance_deadline is not None:
                if chassis_done:
                    self._distance_deadline = None
                    self._distance_retries = 0
                    self._index += 1
                    if self._index >= len(self.steps):
                        self._done = True
                    return self._result()
                if now >= self._distance_deadline:
                    # NOT a fault.  Operator, 2026-09-29: the car was NOT blocked
                    # against anything, so a `D` the firmware accepted (it kept
                    # reporting speed 20) and never reported DONE on means the
                    # firmware lost the move.
                    #
                    # Faulting here killed two long field runs outright, both at
                    # `PICK_ORANGE_LEFT` step 14 -- the same package, the same
                    # step -- and the run that died was otherwise healthy and
                    # 39-50 states deep.  A lost move is worth a STOP and a note,
                    # not the whole run.
                    #
                    # STOP first: if the move IS still in flight somewhere, it
                    # must not be left running while the arm does something else
                    # on top of it.  STOP is idempotent, so this is free when the
                    # move really was lost.
                    self._distance_deadline = None
                    # Do NOT advance yet, and do not report the loss yet: the
                    # deadline expiring only says the firmware never said DONE.
                    # Until 2026-10-03 this advanced straight away, and the
                    # operator watched the arm come down on a car that had not
                    # reversed -- `purple_pickup_latest`'s `D -12` lost three
                    # times in one afternoon, each time ending the same way.
                    #
                    # The STOP above is what makes the next question answerable:
                    # with the move abandoned and the chassis left alone, the
                    # runner's own encoder polls can say whether the car moved
                    # at all.  `chassis_moved` is that answer, and it is the only
                    # thing that makes a RE-SEND safe -- re-sending a move that
                    # was merely slow makes the firmware drive it twice.
                    self._distance_verdict_at = now + self.distance_verdict_s
                    return self._result(chassis_stop=True)
                return self._result()

            if self._distance_verdict_at is not None:
                if now >= self._distance_verdict_at:
                    self._distance_verdict_at = None
                    if (chassis_moved is False
                            and self._distance_retries < self.max_distance_retries):
                        # The car never moved, so the firmware swallowed the
                        # frame rather than running it slowly.  Safe to ask
                        # again: there is no move in flight to restart.
                        #
                        # STOP first and re-send only after
                        # `distance_resend_settle_s` of quiet, so the re-sent `D`
                        # is the first command of its own burst -- the fix the
                        # state machine's `d` branch already carries for exactly
                        # this failure.  `chassis_active` keeps the runner off
                        # the port for the whole flight, so the gap really is
                        # quiet once the STOP this tick carries has landed.
                        self._distance_retries += 1
                        self._distance_resend_at = now + self.distance_resend_settle_s
                        return self._result(chassis_stop=True)
                    # Moved (so the move did run and only the DONE was lost), or
                    # out of retries.  Take the old road: report it and advance.
                    # STOP on the reporting tick as well as on the deadline tick
                    # -- unchanged from before this retry existed, and worth
                    # keeping: whatever the verdict said, a `D` the firmware
                    # never finished may still be in flight somewhere.
                    self._distance_timeout = self._last_distance_command
                    self._distance_retries = 0
                    self._index += 1
                    if self._index >= len(self.steps):
                        self._done = True
                    return self._result(chassis_stop=True)
                # Waiting for a reading that postdates the STOP.
                return self._result()

            if self._distance_resend_at is not None:
                # The quiet gap before a re-sent `D` has elapsed: send it now, as
                # the state machine's `d` branch does after its own settle.
                if now >= self._distance_resend_at:
                    self._distance_resend_at = None
                    self._distance_deadline = now + self.distance_timeout_s
                    return self._result(chassis_distance=self._last_distance_command)
                return self._result()

            if self._index >= len(self.steps):
                self._done = True
                return self._result()

            current = self.steps[self._index]
            if current.kind == "servo":
                previous = self._last_servo_position.get(current.servo_id)
                # "Rising" needs a baseline, and the only honest one is this
                # package's own previous command to the same servo.  A first move
                # on an id is therefore never a lift: with nothing to compare
                # against, a hold would be a guess.  Every pickup package here
                # reaches DOWN before it lifts, so the lift that matters is always
                # a later move.
                self._arm_lift_pending = (
                    current.servo_id == self.arm_lift_servo_id
                    and previous is not None
                    and current.position > previous
                )
                # The build rule takes the FIRST 大臂 move too: `previous` is
                # None there, which only disqualifies the rise test, not "the
                # arm moved".  Every build package opens by walking the arm to
                # the layer it is about to place on, and that approach is the
                # one the operator is watching.
                # Was `servo_id == arm_lift_servo_id` only, which quieted the
                # 大臂 and nothing else.  A build package walks 小臂/腕部 to a
                # layer in the same way (2026-10-01: id 2 1800->2000 and id 3
                # 1700->2000, each as two back-to-back segments), and those ran
                # with no hold at all between them.  Operator, 2026-10-01:
                # 「同个舵机多段调节参数时要停顿一下」.
                #
                # "Multi-segment" is read literally: the NEXT step commands the
                # SAME servo again.  That is the hold the operator is asking for
                # -- between two bites of one walk -- and it deliberately does
                # NOT quiet a servo's ordinary single moves, which would add a
                # dead second to every step of every build package.
                #
                # Decided here, at ISSUE time: `self._index` still points at
                # `current`, and by the time the hold is honoured the index has
                # moved past it.
                _next_step = (self.steps[self._index + 1]
                              if self._index + 1 < len(self.steps) else None)
                _more_segments = (
                    _next_step is not None
                    and _next_step.kind == "servo"
                    and _next_step.servo_id == current.servo_id
                )
                self._arm_move_pending = (
                    self.arm_move_settle_s > 0
                    and (current.servo_id == self.arm_lift_servo_id or _more_segments)
                )
                self._last_servo_position[current.servo_id] = current.position
                self.arm.servo(current.servo_id, current.position, current.time_ms)
                self._waiting_command = "SERVO"
                self._ack_deadline = now + self.ack_timeout_s
                return self._result()
            if current.kind == "suction":
                self.arm.suction(current.enabled)
                self._suction_command = bool(current.enabled)
                self._waiting_command = "SUCTION"
                self._ack_deadline = now + self.ack_timeout_s
                return self._result()
            if current.kind == "chassis_velocity":
                self._motion_until = now + current.duration_s
                return self._result(
                    chassis_velocity=(current.vx, current.vy, current.wz)
                )
            if current.kind == "chassis_distance":
                # Issued once; `_distance_deadline` is what stops this branch
                # being reached again on the next tick.
                self._distance_deadline = now + self.distance_timeout_s
                self._last_distance_command = (
                    current.forward_cm, current.right_cm,
                    current.rotate_deg, current.speed,
                )
                return self._result(chassis_distance=self._last_distance_command)
            return self._fail(f"unsupported compiled action step: {current.kind}")
        except Exception as exc:
            return self._fail(f"pickup action failed: {exc}")


class ActionCatalogExecutor:
    """Select one compiled action from a catalog without blocking the route."""

    def __init__(self, actions: Mapping[str, tuple[CompiledActionStep, ...]], arm,
                 *, ack_timeout_s: float = 1.0, action_ref_prefix: str = "action_package",
                 suction_settle_s: float = 0.0, arm_lift_settle_s: float = 0.0,
                 arm_move_settle_s: float = 0.0, arm_lift_servo_id: int = 1):
        if not actions:
            raise ValueError("action catalog executor needs actions")
        self._actions = dict(actions)
        self.arm = arm
        self.ack_timeout_s = float(ack_timeout_s)
        # Both carried across every `set_action`, so a settle configured for this
        # catalog applies to each of its packages -- RESET included.
        self.suction_settle_s = float(suction_settle_s)
        self.arm_lift_settle_s = float(arm_lift_settle_s)
        self.arm_move_settle_s = float(arm_move_settle_s)
        self.arm_lift_servo_id = int(arm_lift_servo_id)
        self.action_ref_prefix = action_ref_prefix
        self.action = next(iter(self._actions))
        self._executor = self._build(self.action)

    def _build(self, action: str) -> ActionPackageExecutor:
        return ActionPackageExecutor(
            self._actions[action], self.arm, ack_timeout_s=self.ack_timeout_s,
            suction_settle_s=self.suction_settle_s,
            arm_lift_settle_s=self.arm_lift_settle_s,
            arm_move_settle_s=self.arm_move_settle_s,
            arm_lift_servo_id=self.arm_lift_servo_id,
            action_ref=f"{self.action_ref_prefix}:{action}",
        )

    def set_action(self, action: str) -> None:
        if action not in self._actions:
            raise ValueError(f"unknown action: {action}")
        self.action = action
        self._executor = self._build(action)

    def reset(self) -> None:
        self._executor.reset()

    def step(
        self, *, now: float, stop_acknowledged: bool, chassis_done: bool = False,
        chassis_moved: bool | None = None,
    ) -> PickupActionResult:
        result = self._executor.step(
            now=now, stop_acknowledged=stop_acknowledged, chassis_done=chassis_done,
            chassis_moved=chassis_moved,
        )
        metadata = dict(result.metadata)
        metadata["selected_action"] = self.action
        return PickupActionResult(
            done=result.done, metadata=MappingProxyType(metadata),
            chassis_velocity=result.chassis_velocity, chassis_stop=result.chassis_stop,
            chassis_active=result.chassis_active, fault=result.fault,
            chassis_distance=result.chassis_distance,
            suction=result.suction,
            distance_timeout=result.distance_timeout,
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

    def step(
        self, *, now: float, stop_acknowledged: bool, chassis_done: bool = False
    ) -> PickupActionResult:
        # `chassis_done` is accepted and ignored: this executor never issues a
        # distance move, but the runner passes the same keyword to every executor.
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

    def step(
        self, *, now: float, stop_acknowledged: bool, chassis_done: bool = False,
        chassis_moved: bool | None = None,
    ) -> PickupActionResult:
        # Accepted and ignored -- see VisionOnlyPickupExecutor.step.
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
