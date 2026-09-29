"""`D` distance steps in an action package.

The property that matters most here is that a distance move is issued EXACTLY
once.  The firmware restarts a `D` it is sent again -- STOP is idempotent, `D` is
not -- so a package that re-sent on every waiting tick would drive the car
further each time.  These tests pin that down, plus the wait-for-DONE contract.
"""
from __future__ import annotations

import pytest

from route_v2.pickup_action import ActionPackageExecutor, compile_action


class FakeArm:
    """Acknowledges instantly; the executor's arm handling is not under test."""

    def __init__(self):
        self.sent = []
        self._pending = []

    def servo(self, servo_id, position, time_ms):
        self.sent.append(("SERVO", servo_id, position))
        self._pending.append("SERVO")

    def suction(self, enabled):
        self.sent.append(("SUCTION", enabled))
        self._pending.append("SUCTION")

    def poll(self):
        if not self._pending:
            return []
        return [type("Reply", (), {"command": self._pending.pop(0)})()]


def make_package(steps):
    return {"action": {"steps": steps, "name": "x", "protocol": "RG-ARM-1"}}


def distance(fwd, speed=20):
    return {"kind": "CHASSIS", "command": "distance", "forward_cm": fwd,
            "right_cm": 0, "rotate_deg": 0, "speed": speed}


def servo(servo_id, position):
    return {"kind": "SERVO", "id": servo_id, "position": position, "time_ms": 500}


def compile_pkg(steps):
    return compile_action(
        make_package(steps), forward_speed_limit=80,
        expected_suction=tuple(s.get("enabled") for s in steps if s.get("kind") == "SUCTION"),
    )


def test_a_distance_step_compiles_without_needing_a_trailing_stop():
    """`velocity` must be followed by `stop`; a distance move carries its own
    end condition and must not be forced into that shape."""
    steps = compile_pkg([servo(3, 1650), distance(-17), servo(1, 800), distance(5)])
    kinds = [(s.kind, s.forward_cm) for s in steps]
    assert kinds == [("servo", 0), ("chassis_distance", -17), ("servo", 0), ("chassis_distance", 5)]


def test_a_distance_step_that_moves_nowhere_is_rejected():
    with pytest.raises(ValueError, match="moves nowhere"):
        compile_pkg([distance(0)])


def test_distance_is_issued_once_and_the_executor_waits_for_done():
    steps = compile_pkg([distance(-17)])
    arm = FakeArm()
    ex = ActionPackageExecutor(steps, arm)

    first = ex.step(now=0.0, stop_acknowledged=False)          # not started yet
    assert first.chassis_distance is None
    issued = ex.step(now=0.1, stop_acknowledged=True)
    assert issued.chassis_distance == (-17, 0, 0, 20)
    assert issued.chassis_active, "a D move owns the chassis for its whole flight"

    # Every subsequent tick while the move runs must be silent.
    for i in range(200):
        waiting = ex.step(now=0.2 + i * 0.05, stop_acknowledged=True, chassis_done=False)
        assert waiting.chassis_distance is None, "the D was re-sent -- the car would drive it again"
        assert not waiting.done
        assert waiting.fault is None

    done = ex.step(now=20.0, stop_acknowledged=True, chassis_done=True)
    assert done.done
    assert done.chassis_distance is None
    assert done.fault is None


def test_a_distance_move_that_never_reports_done_faults():
    steps = compile_pkg([distance(-17)])
    ex = ActionPackageExecutor(steps, arm=FakeArm(), distance_timeout_s=5.0)
    ex.step(now=0.0, stop_acknowledged=True)
    for i in range(200):
        result = ex.step(now=0.1 + i * 0.1, stop_acknowledged=True, chassis_done=False)
        if result.fault:
            break
    assert result.fault and "DONE" in result.fault


def test_a_stale_done_does_not_complete_the_next_move():
    """The runner clears its DONE latch when it issues a move, so a `DONE` left
    over from the previous one cannot short-circuit this one -- but the executor
    must also not treat a done seen before the issue as completion."""
    steps = compile_pkg([distance(-17), distance(5)])
    ex = ActionPackageExecutor(steps, arm=FakeArm())
    first = ex.step(now=0.0, stop_acknowledged=True)
    assert first.chassis_distance == (-17, 0, 0, 20)
    ex.step(now=0.1, stop_acknowledged=True, chassis_done=True)      # first move done
    second = ex.step(now=0.2, stop_acknowledged=True)
    assert second.chassis_distance == (5, 0, 0, 20)
    # A done arriving now belongs to the second move, and completes it.
    final = ex.step(now=0.3, stop_acknowledged=True, chassis_done=True)
    assert final.done
