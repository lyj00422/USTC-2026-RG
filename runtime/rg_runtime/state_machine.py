"""Pure route state machine for dry-run and future hardware adapters."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from .config import RouteConfig
from .models import (
    BlockObservation,
    LineState,
    MotionCommand,
    MotionMode,
    TagObservation,
    TaskAction,
    TaskCommand,
)


class RouteState(str, Enum):
    START = "START"
    INITIAL_RIGHT_TURN = "INITIAL_RIGHT_TURN"
    MAIN_LINE = "MAIN_LINE"
    ENTER_ZONE = "ENTER_ZONE"
    PICKUP_STOP = "PICKUP_STOP"
    RETURN_MAIN_LINE = "RETURN_MAIN_LINE"
    BUILD_ZONE = "BUILD_ZONE"
    FINISH = "FINISH"
    FAULT_STOP = "FAULT_STOP"


@dataclass(frozen=True)
class RouteDecision:
    state: RouteState
    previous_state: RouteState
    motion_commands: tuple[MotionCommand, ...] = ()
    task_commands: tuple[TaskCommand, ...] = ()
    reason: str = ""


class RouteStateMachine:
    def __init__(self, route: RouteConfig) -> None:
        self.route = route
        self.state = RouteState.START
        self._entered_at_ms = 0
        self._last_time_ms = -1
        self._line_loss_started_ms: int | None = None
        self._fault_reason: str | None = None

    def step(
        self,
        now_ms: int,
        line_state: LineState,
        tags: tuple[TagObservation, ...] = (),
        block: BlockObservation | None = None,
    ) -> RouteDecision:
        if now_ms < self._last_time_ms:
            raise ValueError("time must be monotonic")
        self._last_time_ms = now_ms
        previous = self.state
        if self.state == RouteState.FAULT_STOP:
            return RouteDecision(self.state, previous, reason=self._fault_reason or "fault latched")
        if now_ms - self._entered_at_ms > self.route.state_timeout_ms:
            return self._fault(now_ms, previous, "state timeout")

        if line_state.line_lost and self.state in {
            RouteState.INITIAL_RIGHT_TURN,
            RouteState.MAIN_LINE,
            RouteState.ENTER_ZONE,
            RouteState.RETURN_MAIN_LINE,
            RouteState.BUILD_ZONE,
        }:
            if self._line_loss_started_ms is None:
                self._line_loss_started_ms = now_ms
            if now_ms - self._line_loss_started_ms > self.route.line_hold_ms:
                return self._fault(now_ms, previous, "line lost beyond hold timeout")
            return self._decision(
                previous,
                self._motion(MotionMode.HOLD, None, now_ms, "line temporarily lost"),
                reason="line temporarily lost",
            )
        self._line_loss_started_ms = None

        if self.state == RouteState.START:
            self._transition(RouteState.INITIAL_RIGHT_TURN, now_ms)
            return self._decision(
                previous,
                self._motion(MotionMode.TURN_RIGHT, self.route.turn_speed, now_ms, "initial right turn"),
                reason="start initial right turn",
            )
        if self.state == RouteState.INITIAL_RIGHT_TURN:
            if line_state.turn_completed:
                self._transition(RouteState.MAIN_LINE, now_ms)
                return self._decision(
                    previous,
                    self._motion(MotionMode.FORWARD, self.route.forward_speed, now_ms, "initial turn complete"),
                    reason="initial turn complete",
                )
            return self._decision(
                previous,
                self._motion(MotionMode.TURN_RIGHT, self.route.turn_speed, now_ms, "complete initial right turn"),
                reason="turning right",
            )
        if self.state == RouteState.MAIN_LINE:
            if self._matching_tag(tags, RouteState.ENTER_ZONE):
                self._transition(RouteState.ENTER_ZONE, now_ms)
                return self._decision(
                    previous,
                    self._motion(MotionMode.FORWARD, self.route.forward_speed, now_ms, "route entry tag"),
                    reason="entered configured zone",
                )
            return self._decision(
                previous,
                self._motion(MotionMode.FORWARD, self.route.forward_speed, now_ms, "follow main line"),
                reason="following main line",
            )
        if self.state == RouteState.ENTER_ZONE:
            if line_state.intersection.value != "none":
                self._transition(RouteState.PICKUP_STOP, now_ms)
                return self._decision(
                    previous,
                    self._motion(MotionMode.STOP, None, now_ms, "pickup intersection"),
                    reason="pickup stop",
                )
            return self._decision(
                previous,
                self._motion(MotionMode.FORWARD, self.route.forward_speed, now_ms, "enter pickup zone"),
                reason="entering pickup zone",
            )
        if self.state == RouteState.PICKUP_STOP:
            if block is not None:
                self._transition(RouteState.RETURN_MAIN_LINE, now_ms)
                task = TaskCommand(
                    action=TaskAction.PICKUP,
                    target_color=block.color,
                    reason="stable block observation",
                    timestamp_ms=now_ms,
                )
                return self._decision(
                    previous,
                    self._motion(MotionMode.FORWARD, self.route.forward_speed, now_ms, "leave pickup stop"),
                    task=task,
                    reason="pickup command recorded",
                )
            return self._decision(
                previous,
                self._motion(MotionMode.STOP, None, now_ms, "waiting for stable block"),
                reason="waiting for stable block",
            )
        if self.state == RouteState.RETURN_MAIN_LINE:
            if self._matching_tag(tags, RouteState.BUILD_ZONE):
                self._transition(RouteState.BUILD_ZONE, now_ms)
                return self._decision(
                    previous,
                    self._motion(MotionMode.FORWARD, self.route.forward_speed, now_ms, "build zone tag"),
                    reason="entered build zone",
                )
            return self._decision(
                previous,
                self._motion(MotionMode.FORWARD, self.route.forward_speed, now_ms, "return to main line"),
                reason="returning to main line",
            )
        if self.state == RouteState.BUILD_ZONE:
            if line_state.turn_completed:
                self._transition(RouteState.FINISH, now_ms)
                return self._decision(
                    previous,
                    self._motion(MotionMode.STOP, None, now_ms, "build complete"),
                    reason="finished route",
                )
            return self._decision(
                previous,
                self._motion(MotionMode.FORWARD, self.route.forward_speed, now_ms, "enter build zone"),
                reason="building",
            )
        return self._decision(
            previous,
            self._motion(MotionMode.STOP, None, now_ms, "route finished"),
            reason="route finished",
        )

    def _matching_tag(self, tags: tuple[TagObservation, ...], target: RouteState) -> bool:
        ids = {
            tag_id
            for node in self.route.tag_nodes
            if node.state == target.value
            for tag_id in node.ids
        }
        return any(observation.id in ids for observation in tags)

    def _transition(self, state: RouteState, now_ms: int) -> None:
        self.state = state
        self._entered_at_ms = now_ms
        self._line_loss_started_ms = None

    def _fault(self, now_ms: int, previous: RouteState, reason: str) -> RouteDecision:
        self._fault_reason = reason
        self._transition(RouteState.FAULT_STOP, now_ms)
        return self._decision(
            previous,
            self._motion(MotionMode.STOP, None, now_ms, reason),
            reason=reason,
        )

    @staticmethod
    def _motion(mode, speed, timestamp_ms, reason) -> MotionCommand:
        return MotionCommand(mode, speed, None, reason, timestamp_ms)

    def _decision(self, previous, motion=None, *, task=None, reason="") -> RouteDecision:
        motions = () if motion is None else (motion,)
        tasks = () if task is None else (task,)
        return RouteDecision(self.state, previous, motions, tasks, reason)
