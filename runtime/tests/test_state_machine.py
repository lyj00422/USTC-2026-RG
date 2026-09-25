import pytest

from rg_runtime.config import RouteConfig, TagNode
from rg_runtime.models import (
    BlockColor,
    BlockObservation,
    LineIntersection,
    LineState,
    MotionMode,
    TagObservation,
    TaskAction,
)
from rg_runtime.state_machine import RouteState, RouteStateMachine


def route(*, line_hold_ms=100, state_timeout_ms=1000):
    return RouteConfig(
        name="test",
        line_hold_ms=line_hold_ms,
        state_timeout_ms=state_timeout_ms,
        forward_speed=0.25,
        turn_speed=0.20,
        tag_nodes=(
            TagNode("entry", "ENTER_ZONE", (5,), True),
            TagNode("build", "BUILD_ZONE", (6,), True),
        ),
    )


def line(*, lost=False, intersection=LineIntersection.NONE, completed=False, timestamp=0):
    return LineState(
        line_error=0.0 if not lost else None,
        sensor_mask=1 if not lost else 0,
        intersection=intersection,
        line_lost=lost,
        turn_completed=completed,
        timestamp_ms=timestamp,
    )


def tag(tag_id, frame_index=0):
    return TagObservation(
        id=tag_id,
        family="36H11",
        corners_px=((0.0, 0.0),) * 4,
        center_px=(0.0, 0.0),
        decision_margin=1.0,
        timestamp_ns=frame_index,
        frame_index=frame_index,
    )


def block(color=BlockColor.PURPLE):
    return BlockObservation(
        color=color,
        bounding_box=(10, 10, 20, 20),
        center_px=(20.0, 20.0),
        area=400.0,
        confidence=0.9,
    )


def test_dry_run_route_reaches_finish_and_emits_pickup():
    machine = RouteStateMachine(route())

    first = machine.step(0, line(timestamp=0))
    assert first.state == RouteState.INITIAL_RIGHT_TURN
    assert first.motion_commands[-1].mode == MotionMode.TURN_RIGHT

    assert machine.step(10, line(completed=True, timestamp=10)).state == RouteState.MAIN_LINE
    assert machine.step(20, line(timestamp=20), tags=(tag(5, 20),)).state == RouteState.ENTER_ZONE
    assert machine.step(30, line(intersection=LineIntersection.CROSS, timestamp=30)).state == RouteState.PICKUP_STOP

    pickup = machine.step(40, line(timestamp=40), block=block())
    assert pickup.state == RouteState.RETURN_MAIN_LINE
    assert pickup.task_commands[-1].action == TaskAction.PICKUP

    assert machine.step(50, line(timestamp=50), tags=(tag(6, 50),)).state == RouteState.BUILD_ZONE
    assert machine.step(60, line(completed=True, timestamp=60)).state == RouteState.FINISH


def test_short_line_loss_holds_then_long_loss_latches_fault():
    machine = RouteStateMachine(route(line_hold_ms=100))
    machine.step(0, line(timestamp=0))
    machine.step(10, line(completed=True, timestamp=10))

    held = machine.step(20, line(lost=True, timestamp=20))
    assert held.state == RouteState.MAIN_LINE
    assert held.motion_commands[-1].mode == MotionMode.HOLD

    fault = machine.step(121, line(lost=True, timestamp=121))
    assert fault.state == RouteState.FAULT_STOP
    assert fault.motion_commands[-1].mode == MotionMode.STOP

    latched = machine.step(130, line(timestamp=130))
    assert latched.state == RouteState.FAULT_STOP
    assert latched.motion_commands == ()


def test_state_timeout_and_non_monotonic_time_fault():
    machine = RouteStateMachine(route(state_timeout_ms=50))
    machine.step(0, line(timestamp=0))
    timed_out = machine.step(51, line(timestamp=51))
    assert timed_out.state == RouteState.FAULT_STOP
    assert timed_out.motion_commands[-1].mode == MotionMode.STOP

    with pytest.raises(ValueError, match="monotonic"):
        machine.step(50, line(timestamp=50))


def test_duplicate_tag_does_not_advance_unrelated_state():
    machine = RouteStateMachine(route())
    machine.step(0, line(timestamp=0))
    machine.step(10, line(completed=True, timestamp=10))
    machine.step(20, line(timestamp=20), tags=(tag(5, 20),))

    decision = machine.step(30, line(timestamp=30), tags=(tag(5, 30),))
    assert decision.state == RouteState.ENTER_ZONE
