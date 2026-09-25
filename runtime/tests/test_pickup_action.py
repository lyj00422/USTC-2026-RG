from pathlib import Path
from copy import deepcopy

import pytest

from route_v2.pickup_action import (
    ActionPackageExecutor,
    CompiledActionStep,
    VisionOnlyPickupExecutor,
    compile_action,
    load_action_package,
)


ROOT = Path(__file__).parents[1]
PURPLE_ACTION = ROOT / "data" / "route_v2_actions" / "purple_pickup_v1"
PURPLE_PLACE_ACTION = ROOT / "data" / "route_v2_actions" / "purple_place_v1"


def test_vision_only_executor_waits_for_stop_ack_then_finishes_after_three_seconds():
    executor = VisionOnlyPickupExecutor(wait_s=3, action_ref="firmware_routine:3")

    assert executor.step(now=0, stop_acknowledged=False).done is False
    assert executor.step(now=2, stop_acknowledged=False).done is False
    assert executor.step(now=10, stop_acknowledged=True).done is False
    result = executor.step(now=13, stop_acknowledged=True)

    assert result.done is True
    assert result.metadata == {
        "action_ref": "firmware_routine:3",
        "executed": False,
        "result": "vision_only_complete",
    }


def test_recorded_purple_package_compiles_exact_motion_arm_and_suction():
    package = load_action_package(PURPLE_ACTION)

    compiled = compile_action(package, forward_speed_limit=20)

    moves = [step for step in compiled if step.kind == "chassis_velocity"]
    assert [(step.vx, step.vy, step.wz) for step in moves] == [
        (-20, 0, 0), (-20, 0, 0), (-20, 0, 0), (-20, 0, 0),
        (-20, 0, 0), (20, 0, 0), (20, 0, 0), (20, 0, 0),
    ]
    assert [step.duration_s for step in moves] == pytest.approx([
        0.169241, 0.065450, 0.145627, 0.118994,
        0.268398, 0.089820, 0.087508, 0.182553,
    ])
    assert [
        (step.servo_id, step.position, step.time_ms)
        for step in compiled if step.kind == "servo"
    ] == [
        (3, 1750, 500), (2, 1750, 500), (1, 750, 500),
        (3, 1650, 500), (2, 1600, 500), (1, 1500, 500),
    ]
    assert [step.enabled for step in compiled if step.kind == "suction"] == [True]


def test_forward_override_can_only_slow_the_recorded_speed():
    package = load_action_package(PURPLE_ACTION)

    compiled = compile_action(package, forward_speed_limit=8)

    assert max(step.vx for step in compiled if step.kind == "chassis_velocity") == 8
    assert min(step.vx for step in compiled if step.kind == "chassis_velocity") == -20
    with pytest.raises(ValueError, match="1..80"):
        compile_action(package, forward_speed_limit=81)


def test_purple_package_requires_exactly_one_suction_on_and_never_off():
    package = deepcopy(dict(load_action_package(PURPLE_ACTION)))
    package["action"]["steps"][-2]["enabled"] = False

    with pytest.raises(ValueError, match="exactly one SUCTION,true"):
        compile_action(package)


def test_recorded_purple_place_package_keeps_suction_until_final_release():
    package = load_action_package(
        PURPLE_PLACE_ACTION,
        expected_name="放紫色v1_无视觉窗口",
        require_capture_window=False,
    )

    compiled = compile_action(package, expected_suction=(True, False))

    moves = [step for step in compiled if step.kind == "chassis_velocity"]
    assert [(step.vx, step.vy, step.wz) for step in moves] == [
        (-20, 0, 0), (-20, 0, 0), (-20, 0, 0),
        (-20, 0, 0), (-20, 0, 0), (-20, 0, 0),
    ]
    assert [step.duration_s for step in moves] == pytest.approx([
        .088615, .280148, .164226, .126179, .111959, .052457,
    ])
    assert [
        (step.servo_id, step.position, step.time_ms)
        for step in compiled if step.kind == "servo"
    ] == [(2, 1800, 500), (3, 1000, 500), (1, 1300, 500)]
    assert [step.enabled for step in compiled if step.kind == "suction"] == [
        True, False,
    ]


class _Reply:
    def __init__(self, command):
        self.command = command


class _Arm:
    def __init__(self):
        self.sent = []
        self.replies = []

    def servo(self, servo_id, position, time_ms):
        self.sent.append(("SERVO", servo_id, position, time_ms))

    def suction(self, enabled):
        self.sent.append(("SUCTION", enabled))

    def poll(self):
        replies, self.replies = self.replies, []
        return replies


def test_executor_waits_for_stop_ack_and_servo_ack_without_blocking():
    arm = _Arm()
    steps = (
        CompiledActionStep(kind="servo", servo_id=3, position=1750, time_ms=500),
        CompiledActionStep(kind="servo", servo_id=2, position=1750, time_ms=500),
    )
    executor = ActionPackageExecutor(steps, arm, ack_timeout_s=1.0)

    assert not executor.step(now=0.0, stop_acknowledged=False).done
    assert arm.sent == []
    first = executor.step(now=1.0, stop_acknowledged=True)
    assert arm.sent == [("SERVO", 3, 1750, 500)]
    assert first.chassis_velocity is None and not first.done
    arm.replies.append(_Reply("SERVO"))
    assert not executor.step(now=1.1, stop_acknowledged=True).done
    assert not executor.step(now=1.59, stop_acknowledged=True).done
    executor.step(now=1.6, stop_acknowledged=True)
    assert arm.sent[-1] == ("SERVO", 2, 1750, 500)


def test_executor_stops_velocity_at_recorded_deadline_and_finishes():
    arm = _Arm()
    executor = ActionPackageExecutor((
        CompiledActionStep(
            kind="chassis_velocity", vx=-20, duration_s=0.169241,
        ),
    ), arm)

    moving = executor.step(now=2.0, stop_acknowledged=True)
    waiting = executor.step(now=2.169240, stop_acknowledged=False)
    stopped = executor.step(now=2.169241, stop_acknowledged=False)

    assert moving.chassis_velocity == (-20, 0, 0)
    assert moving.chassis_active is True
    assert waiting.chassis_velocity is None and not waiting.chassis_stop
    assert waiting.chassis_active is True
    assert stopped.chassis_stop is True
    assert stopped.chassis_active is False
    assert stopped.done is True


def test_executor_faults_on_ack_timeout_without_advancing():
    arm = _Arm()
    executor = ActionPackageExecutor((
        CompiledActionStep(kind="servo", servo_id=3, position=1750, time_ms=500),
        CompiledActionStep(kind="suction", enabled=True),
    ), arm, ack_timeout_s=1.0)

    executor.step(now=0.0, stop_acknowledged=True)
    result = executor.step(now=1.01, stop_acknowledged=True)

    assert result.fault == "arm SERVO acknowledgement timed out"
    assert arm.sent == [("SERVO", 3, 1750, 500)]
    assert result.done is False


def test_executor_records_suction_only_after_acknowledgement():
    arm = _Arm()
    executor = ActionPackageExecutor((
        CompiledActionStep(kind="suction", enabled=True),
    ), arm)

    executor.step(now=0.0, stop_acknowledged=True)
    assert executor.suction_enabled is False
    arm.replies.append(_Reply("SUCTION"))
    result = executor.step(now=0.1, stop_acknowledged=True)

    assert result.done is True
    assert executor.suction_enabled is True
