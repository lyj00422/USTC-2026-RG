from pathlib import Path
from types import SimpleNamespace

import pytest

from run_route_v2 import (
    EncoderContactDetector, RouteRunner, RouteVisionRuntime, _MOTION_INTENTS, _close_route_arm,
    _configure_camera, _forward_counts, _load_route_camera_config,
    _prepare_route_arm, main, vision_result_is_fresh, vision_task_for_state,
)
from route_v2.pickup_action import ActionPackageExecutor, CompiledActionStep
from route_v2.config import RouteV2Config, load_route_v2_config
from route_v2.pickup_vision import PickupPhase
from route_v2.state_machine import RouteState, VisionRouteInput
from route_v2.vision_worker import VisionTask
from rg_runtime.models import TagObservation


def test_visual_tasks_are_state_scoped_and_tag_ids_cannot_cross():
    assert vision_task_for_state(RouteState.JUNCTION_1_STRAFE_TO_TAG_2) is VisionTask.TAG2
    assert vision_task_for_state(RouteState.PICKUP_SEEK_LINE) is VisionTask.TAG3
    assert vision_task_for_state(RouteState.PICKUP_2_SEEK_LINE) is VisionTask.TAG4
    assert vision_task_for_state(RouteState.PURPLE_PRESCAN) is VisionTask.PURPLE_PRESCAN
    assert vision_task_for_state(RouteState.START_TO_JUNCTION_1) is VisionTask.NONE


def test_full_route_refuses_uncalibrated_visual_config_before_hardware(tmp_path, capsys):
    source = Path("config/route_v2.yaml").read_text(encoding="utf-8")
    uncalibrated = tmp_path / "route_v2.yaml"
    uncalibrated.write_text(source.replace("calibrated: true", "calibrated: false", 1), encoding="utf-8")
    assert main(["--full", "--config", str(uncalibrated)]) == 2
    assert "visual route is not calibrated" in capsys.readouterr().out


def test_visual_result_freshness_checks_generation_and_capture_age():
    result = SimpleNamespace(generation=4, captured_at=10.0)
    assert vision_result_is_fresh(result, generation=4, now=10.5, max_age_s=.5)
    assert not vision_result_is_fresh(result, generation=3, now=10.1, max_age_s=.5)
    assert not vision_result_is_fresh(result, generation=4, now=10.51, max_age_s=.5)


def _startup_runtime(result_holder, *, selected_at=0.0):
    runtime = RouteVisionRuntime.__new__(RouteVisionRuntime)
    runtime.start = lambda: None
    runtime.clock = lambda: 0.0
    runtime.vision = _visual_route_config().vision
    runtime.config = _visual_route_config()
    runtime.frames = SimpleNamespace(snapshot=lambda **_kwargs: SimpleNamespace(
        fresh=True, frame_id=20, captured_at=.68, age_s=0.01, image=None,
    ))
    runtime.capture = SimpleNamespace(status=lambda: SimpleNamespace(error=None))
    runtime.worker = SimpleNamespace(
        status=lambda: SimpleNamespace(error=None, running=True, frame_id=20),
        result=lambda: result_holder["result"],
        select=lambda _task: 2,
    )
    runtime._task = VisionTask.PURPLE_CLOSE
    runtime._generation = 1
    runtime._task_selected_at = selected_at
    runtime._task_first_fresh_frame_id = None
    runtime._task_ready = False
    runtime._pickup_controller = SimpleNamespace(
        phase=PickupPhase.SEARCH_RIGHT,
        select_target=lambda _items: None,
        step=lambda **_kwargs: SimpleNamespace(
            phase=SimpleNamespace(value="SEARCH_RIGHT"),
            kind="strafe",
            speed=-28,
            reason="search_right",
            result=None,
        ),
    )
    runtime._pickup_area = "purple"
    runtime._pickup_baseline_cm = 0.0
    runtime._return_controller = None
    return runtime


def _vision_result(frame_id, captured_at):
    return SimpleNamespace(
        generation=1,
        captured_at=captured_at,
        completed_at=captured_at + .29,
        processing_s=.29,
        frame_id=frame_id,
        value=SimpleNamespace(accepted=()),
    )


def test_visual_task_holds_until_two_distinct_fresh_results_then_resumes():
    result_holder = {"result": _vision_result(1, .03)}
    runtime = _startup_runtime(result_holder)

    first, first_diagnostics = runtime.observe(
        state=RouteState.PICKUP_VISION_ONLY, now=.38,
        absolute_lateral_cm=0.0, wall_contact=False, stopped=True,
        sensor_mask=0xFF,
    )
    stale_first, stale_diagnostics = runtime.observe(
        state=RouteState.PICKUP_VISION_ONLY, now=.54,
        absolute_lateral_cm=0.0, wall_contact=False, stopped=True,
        sensor_mask=0xFF,
    )
    result_holder["result"] = _vision_result(14, .66)
    ready, ready_diagnostics = runtime.observe(
        state=RouteState.PICKUP_VISION_ONLY, now=.69,
        absolute_lateral_cm=0.0, wall_contact=False, stopped=True,
        sensor_mask=0xFF,
    )

    assert (first.vision_pending, first.camera_fault) == (True, False)
    assert (stale_first.vision_pending, stale_first.camera_fault) == (True, False)
    assert (ready.vision_pending, ready.camera_fault) == (False, False)
    assert ready.pickup_kind == "strafe"
    assert first_diagnostics["vision"]["ready"] is False
    assert stale_diagnostics["vision"]["fault_reason"] is None
    assert ready_diagnostics["vision"]["ready"] is True


def test_visual_task_startup_timeout_faults_while_chassis_is_held():
    result_holder = {"result": _vision_result(1, .03)}
    runtime = _startup_runtime(result_holder)

    route_input, diagnostics = runtime.observe(
        state=RouteState.PICKUP_VISION_ONLY, now=2.01,
        absolute_lateral_cm=0.0, wall_contact=False, stopped=True,
        sensor_mask=0xFF,
    )

    assert route_input.camera_fault is True
    assert route_input.vision_pending is False
    assert diagnostics["vision"]["fault_reason"] == "vision_startup_timeout"


def test_ready_visual_task_continues_bounded_search_on_a_transient_stale_result():
    result_holder = {"result": _vision_result(14, .1)}
    runtime = _startup_runtime(result_holder)
    runtime._task_first_fresh_frame_id = 1
    runtime._task_ready = True

    route_input, diagnostics = runtime.observe(
        state=RouteState.PICKUP_VISION_ONLY, now=.61,
        absolute_lateral_cm=0.0, wall_contact=False, stopped=True,
        sensor_mask=0xFF,
    )

    assert route_input.camera_fault is False
    assert route_input.vision_hold is False
    assert route_input.pickup_kind == "strafe"
    assert diagnostics["vision"]["hold"] is True
    assert diagnostics["vision"]["search_continued"] is True
    assert diagnostics["vision"]["fault_reason"] is None
    assert diagnostics["vision"]["worker_running"] is True
    assert diagnostics["vision"]["worker_frame_id"] == 20
    assert diagnostics["vision"]["processing_s"] == pytest.approx(.29)


def test_ready_visual_task_still_holds_alignment_on_a_transient_stale_result():
    result_holder = {"result": _vision_result(14, .1)}
    runtime = _startup_runtime(result_holder)
    runtime._task_first_fresh_frame_id = 1
    runtime._task_ready = True
    runtime._pickup_controller.phase = PickupPhase.ALIGNING

    route_input, diagnostics = runtime.observe(
        state=RouteState.PICKUP_VISION_ONLY, now=.61,
        absolute_lateral_cm=0.0, wall_contact=False, stopped=False,
        sensor_mask=0xFF,
    )

    assert route_input.camera_fault is False
    assert route_input.vision_hold is True
    assert route_input.pickup_kind is None
    assert diagnostics["vision"]["hold"] is True


def test_ready_visual_task_faults_after_the_measured_result_stall_budget():
    result_holder = {"result": _vision_result(14, .1)}
    runtime = _startup_runtime(result_holder)
    runtime._task_first_fresh_frame_id = 1
    runtime._task_ready = True

    route_input, diagnostics = runtime.observe(
        state=RouteState.PICKUP_VISION_ONLY, now=1.40,
        absolute_lateral_cm=0.0, wall_contact=False, stopped=True,
        sensor_mask=0xFF,
    )

    assert route_input.camera_fault is True
    assert route_input.vision_hold is False
    assert diagnostics["vision"]["fault_reason"] == "vision_result_timeout"


def test_camera_frame_staleness_faults_during_visual_startup():
    result_holder = {"result": _vision_result(1, .03)}
    runtime = _startup_runtime(result_holder)
    runtime.frames = SimpleNamespace(snapshot=lambda **_kwargs: SimpleNamespace(
        fresh=False, frame_id=1, captured_at=.03, age_s=.55, image=None,
    ))

    route_input, diagnostics = runtime.observe(
        state=RouteState.PICKUP_VISION_ONLY, now=.58,
        absolute_lateral_cm=0.0, wall_contact=False, stopped=True,
        sensor_mask=0xFF,
    )

    assert route_input.camera_fault is True
    assert diagnostics["vision"]["fault_reason"] == "camera_frame_stale"


@pytest.mark.parametrize(
    ("source_state", "target_state"),
    [
        (RouteState.JUNCTION_1_TO_JUNCTION_2, RouteState.JUNCTION_1_STRAFE_TO_TAG_2),
        (RouteState.JUNCTION_3_TURN_LEFT, RouteState.PICKUP_SEEK_LINE),
        (RouteState.PICKUP_2_TURN_RIGHT, RouteState.PICKUP_2_SEEK_LINE),
    ],
)
def test_runner_stops_and_selects_visual_task_on_transition_tick(
    source_state, target_state,
):
    class Chassis(_VisionTestChassis):
        def poll(self):
            return [SimpleNamespace(kind="done", value="")]

    class Line:
        def poll_once(self):
            return {"sensor_mask": 0xFF, "line_error": None}

    class Runtime:
        def __init__(self):
            self.states = []

        def observe(self, *, state, **_kwargs):
            self.states.append(state)
            pending = vision_task_for_state(state) is not VisionTask.NONE
            return VisionRouteInput(vision_pending=pending), {
                "vision": {
                    "task": vision_task_for_state(state).value,
                    "pending": pending,
                    "ready": False,
                    "fault_reason": None,
                }
            }

    chassis = Chassis()
    runtime = Runtime()
    runner = RouteRunner(
        _visual_route_config(), chassis, Line(), vision_runtime=runtime,
        clock=lambda: 0,
    )
    runner.machine._enter(source_state, 0.0)
    if source_state is RouteState.JUNCTION_1_TO_JUNCTION_2:
        runner.machine._aligned = True
    else:
        runner.machine._action_pending = True

    intent = runner.tick(.1)

    assert runner.machine.state is target_state
    assert intent.kind == "stop"
    assert chassis.commands == [("STOP",)]
    assert runtime.states == [
        source_state,
        target_state,
    ]


def test_visual_task_switch_stop_is_not_throttled_after_velocity_command():
    class Line:
        mask = 0xFF

        def poll_once(self):
            return {"sensor_mask": self.mask, "line_error": None}

    class Runtime:
        def observe(self, *, state, **_kwargs):
            pending = state is RouteState.PURPLE_PRESCAN
            return VisionRouteInput(vision_pending=pending), {
                "vision": {
                    "task": vision_task_for_state(state).value,
                    "pending": pending,
                    "ready": not pending,
                    "fault_reason": None,
                }
            }

    chassis = _VisionTestChassis()
    line = Line()
    runner = RouteRunner(
        _visual_route_config(), chassis, line, vision_runtime=Runtime(),
        clock=lambda: 0,
    )
    runner.machine._enter(RouteState.PICKUP_SEEK_LINE, 0.0)
    runner._command_stop(0.0)
    chassis.commands.clear()

    runner.tick(.04)
    line.mask = 0x81
    runner.tick(.06)

    assert runner.machine.state is RouteState.PURPLE_PRESCAN
    assert chassis.commands == [("V", 0, 40, 0), ("STOP",)]


class _VisionTestChassis:
    def __init__(self):
        self.commands = []

    def set_velocity(self, *args): self.commands.append(("V",) + args)
    def run_distance(self, *args): self.commands.append(("D",) + args)
    def stop(self): self.commands.append(("STOP",))
    def poll(self): return []


class _VisionTestLine:
    def poll_once(self): return {"sensor_mask": 0x81, "line_error": 0.0}


def _visual_route_config():
    return load_route_v2_config(Path(__file__).parents[1] / "config" / "route_v2.yaml")


def test_stale_camera_stops_and_faults_while_tick_remains_live():
    class Runtime:
        def observe(self, **_kwargs):
            return VisionRouteInput(camera_fault=True), {"camera": {"fresh": False}}

    chassis = _VisionTestChassis()
    runner = RouteRunner(_visual_route_config(), chassis, _VisionTestLine(),
                         vision_runtime=Runtime(), clock=lambda: 0)
    runner.machine._enter(RouteState.PURPLE_PRESCAN, 0)

    intent = runner.tick(0.1)

    assert intent.kind == "stop"
    assert runner.machine.state is RouteState.FAULT
    assert ("STOP",) in chassis.commands


def test_purple_return_to_line_runs_the_return_controller_off_the_saved_baseline():
    """The post-grab line re-acquisition must actually run -- on the saved baseline.

    Two wiring lines carry this and both are easy to drop: PURPLE_RETURN_TO_LINE
    has to be exempt in _select(), where a switch to VisionTask.NONE would
    otherwise throw the controller away on the very tick the state is entered,
    and it has to be in the observe() branch that feeds the controller.  With
    either one missing the state can never report the line, which is the state
    the car was left in on 20260918: 18.5 s of STOP with nothing to explain it.

    _startup_runtime leaves _task at PURPLE_CLOSE precisely so that _select
    really runs here rather than short-circuiting.
    """
    runtime = _startup_runtime({"result": None})
    runtime._pickup_baseline_cm = -20.0

    strafing, _ = runtime.observe(
        state=RouteState.PURPLE_RETURN_TO_LINE, now=1.0,
        absolute_lateral_cm=-34.0, wall_contact=False, stopped=True,
        sensor_mask=0xFF,
    )
    # 14 cm off the baseline, so it strafes back toward it -- and the strafe
    # speed comes from the controller, not from a stale pickup phase.
    assert strafing.pickup_kind == "return_baseline"
    assert strafing.pickup_speed > 0

    # Back at the baseline, still no line under the bar: bounded seek, not done.
    seeking, _ = runtime.observe(
        state=RouteState.PURPLE_RETURN_TO_LINE, now=1.1,
        absolute_lateral_cm=-20.0, wall_contact=False, stopped=True,
        sensor_mask=0xFF,
    )
    assert seeking.pickup_kind == "seek_line"
    assert seeking.return_line_done is False

    # On the line (0x81 is centred), for seek_line_confirm_frames frames.
    done = None
    for index in range(runtime.config.seek_line_confirm_frames):
        done, _ = runtime.observe(
            state=RouteState.PURPLE_RETURN_TO_LINE, now=1.2 + 0.1 * index,
            absolute_lateral_cm=-20.0, wall_contact=False, stopped=True,
            sensor_mask=0x81,
        )
    assert done.return_line_done is True


def test_visual_placeholder_reports_metadata_and_never_emits_arm_command():
    class Runtime:
        def observe(self, *, now, **_kwargs):
            done = now >= 3
            return VisionRouteInput(pickup_kind="pickup_ready", action_done=done), {
                "pickup_action": {
                    "action_ref": "firmware_routine:3",
                    "executed": False,
                    "result": "vision_only_complete" if done else "waiting",
                }
            }

    records = []
    chassis = _VisionTestChassis()
    runner = RouteRunner(_visual_route_config(), chassis, _VisionTestLine(),
                         vision_runtime=Runtime(), telemetry=records.append, clock=lambda: 0)
    runner.machine._enter(RouteState.PICKUP_VISION_ONLY, 0)
    runner.tick(0)
    runner.tick(3)

    # The action is done, so the route leaves PICKUP_VISION_ONLY -- but for the
    # line, not for the landmark hunt.  This fake never reports the line
    # reconfirmed, so the car stops here; that is the point of the state.
    assert runner.machine.state is RouteState.PURPLE_RETURN_TO_LINE
    assert records[-1]["pickup_action"] == {
        "action_ref": "firmware_routine:3",
        "executed": False,
        "result": "vision_only_complete",
    }
    assert all(command[0] != "ARM" for command in chassis.commands)


class _PickupReadyRuntime:
    def __init__(self):
        self.observe_calls = 0
        self.suspend_calls = 0

    def observe(self, **_kwargs):
        self.observe_calls += 1
        return VisionRouteInput(pickup_kind="pickup_ready"), {}

    def suspend(self, **_kwargs):
        self.suspend_calls += 1
        return VisionRouteInput(pickup_kind="pickup_ready"), {
            "vision": {"task": "NONE", "suspended": "pickup_action"},
        }


class _PickupArm:
    def __init__(self):
        self.sent = []

    def servo(self, servo_id, position, time_ms):
        self.sent.append(("SERVO", servo_id, position, time_ms))

    def suction(self, enabled):
        self.sent.append(("SUCTION", enabled))

    def poll(self):
        return []


def test_purple_ready_runs_real_action_but_orange_keeps_placeholder():
    arm = _PickupArm()
    action = ActionPackageExecutor((
        CompiledActionStep(kind="servo", servo_id=3, position=1750, time_ms=500),
    ), arm)
    chassis = _VisionTestChassis()
    runner = RouteRunner(
        _visual_route_config(), chassis, _VisionTestLine(),
        vision_runtime=_PickupReadyRuntime(), purple_action=action, clock=lambda: 0,
    )
    runner._last_intent_kind = "stop"
    runner.machine._enter(RouteState.PICKUP_VISION_ONLY, 0)

    runner.tick(0)

    assert arm.sent == [("SERVO", 3, 1750, 500)]

    arm.sent.clear()
    runner.machine._enter(RouteState.PICKUP_2_VISION_ONLY, 1)
    runner.tick(1)
    assert arm.sent == []


def test_action_velocity_overrides_route_stop_until_recorded_deadline():
    arm = _PickupArm()
    action = ActionPackageExecutor((
        CompiledActionStep(kind="chassis_velocity", vx=8, duration_s=0.1),
    ), arm)
    chassis = _VisionTestChassis()
    runner = RouteRunner(
        _visual_route_config(), chassis, _VisionTestLine(),
        vision_runtime=_PickupReadyRuntime(), purple_action=action, clock=lambda: 0,
    )
    runner._last_intent_kind = "stop"
    runner.machine._enter(RouteState.PICKUP_VISION_ONLY, 0)

    runner.tick(0)
    runner.tick(0.05)
    runner.tick(0.1)

    assert chassis.commands[0] == ("V", 8, 0, 0)
    assert chassis.commands[1:] == [("STOP",)]
    # Past the recorded deadline the route owns the chassis again, and the grab
    # hands over to the line re-acquisition -- see the note in
    # test_visual_placeholder_reports_metadata_and_never_emits_arm_command.
    assert runner.machine.state is RouteState.PURPLE_RETURN_TO_LINE


def test_started_pickup_action_advances_while_vision_is_suspended():
    runtime = _PickupReadyRuntime()
    arm = _PickupArm()
    action = ActionPackageExecutor((
        CompiledActionStep(kind="chassis_velocity", vx=-20, duration_s=0.2),
        CompiledActionStep(kind="suction", enabled=True),
    ), arm)
    chassis = _VisionTestChassis()
    runner = RouteRunner(
        _visual_route_config(), chassis, _VisionTestLine(),
        vision_runtime=runtime, purple_action=action, clock=lambda: 0,
    )
    runner._last_intent_kind = "stop"
    runner.machine._enter(RouteState.PICKUP_VISION_ONLY, 0)

    runner.tick(0)
    runner.tick(.1)

    assert runtime.observe_calls == 1
    assert runtime.suspend_calls == 1
    assert chassis.commands == [("V", -20, 0, 0)]
    assert runner.machine.state is RouteState.PICKUP_VISION_ONLY


class _RouteArmTransport:
    def __init__(self, *, calibrated=True):
        self.calibrated = calibrated
        self.sent = []
        self.incoming = []
        self.closed = False

    def send_line(self, line):
        self.sent.append(line)
        command = line.strip()
        if command == "ARM,STOP":
            self.incoming.append("ACK,STOPPED_LOCKED")
        elif command == "ARM,PING":
            self.incoming.append("ACK,PONG")
        elif command == "ARM,STATUS":
            cal = 1 if self.calibrated else 0
            self.incoming.append(
                f"STATE,LOCKED,CAL={cal},SUCTION=0,ROUTINE=255,STEP=0,RX3=0"
            )
        elif command == "ARM,ENABLE":
            self.incoming.append("ACK,ENABLED")

    def read_lines(self):
        lines, self.incoming = self.incoming, []
        return lines

    def close(self):
        self.closed = True


def _route_arm_runtime():
    return SimpleNamespace(
        arm_device="/dev/robogame-arm",
        arm_baudrate=115200,
        arm_probe_timeout_ms=2000,
    )


def test_route_arm_is_calibrated_and_ready_before_use():
    transport = _RouteArmTransport(calibrated=True)

    session = _prepare_route_arm(
        _route_arm_runtime(), transport_factory=lambda *_args, **_kwargs: transport,
    )

    assert transport.sent == [
        "ARM,STOP\r\n", "ARM,PING\r\n", "ARM,STATUS\r\n", "ARM,ENABLE\r\n",
    ]
    assert session.arm.state.mode.value == "READY"
    assert transport.closed is False


def test_route_arm_rejects_cal_zero_and_closes_transport():
    transport = _RouteArmTransport(calibrated=False)

    with pytest.raises(RuntimeError, match="CAL=1"):
        _prepare_route_arm(
            _route_arm_runtime(), transport_factory=lambda *_args, **_kwargs: transport,
        )

    assert "ARM,ENABLE\r\n" not in transport.sent
    assert transport.closed is True


def test_normal_route_close_keeps_suction_command_and_only_closes_serial():
    transport = _RouteArmTransport()
    session = SimpleNamespace(
        transport=transport,
        stops=0,
    )
    session.stop = lambda: setattr(session, "stops", session.stops + 1)

    _close_route_arm(session, keep_suction=True)

    assert session.stops == 0
    assert transport.closed is True


def test_fault_route_close_stops_arm_before_closing_serial():
    transport = _RouteArmTransport()
    session = SimpleNamespace(
        transport=transport,
        stops=0,
    )
    session.stop = lambda: setattr(session, "stops", session.stops + 1)

    _close_route_arm(session, keep_suction=False)

    assert session.stops == 1
    assert transport.closed is True


def test_contact_is_the_odometer_stopping_while_the_leg_still_runs():
    """SPD's OUT is the duty the firmware is commanding, not a measurement, so
    the odometer is the whole test.  Run 6 left the encoders frozen at a single
    count for 62 s while the old two-signal version stayed silent throughout."""
    detector = EncoderContactDetector()
    assert not detector.update(0.0, encoder_delta=0.0)      # nothing seen yet
    assert not detector.update(0.5, encoder_delta=120.0)    # moving: arms it
    assert not detector.update(1.0, encoder_delta=120.0)
    assert not detector.update(1.6, encoder_delta=0.0)      # stops -- clock starts
    assert not detector.update(1.9, encoder_delta=0.0)
    assert detector.update(2.2, encoder_delta=0.0)


def test_contact_ignores_a_stall_before_the_leg_has_ever_moved():
    """Before the first ENC reply lands every delta is zero.  Read as a stall,
    the car would be declared arrived the moment the leg began."""
    detector = EncoderContactDetector()
    for tick in range(40):
        assert not detector.update(tick * 0.2, encoder_delta=0.0)


def test_contact_restarts_the_clock_when_the_car_moves_again():
    """A car creeping in stop-start steps against the wall is not arrived until
    it has genuinely stopped; any motion has to clear the accumulator."""
    detector = EncoderContactDetector()
    assert not detector.update(0.0, encoder_delta=200.0)
    assert not detector.update(1.6, encoder_delta=0.0)
    assert not detector.update(2.0, encoder_delta=0.0)
    assert not detector.update(2.1, encoder_delta=200.0)    # lurched forward again
    assert not detector.update(2.3, encoder_delta=0.0)
    assert not detector.update(2.6, encoder_delta=0.0)      # only 0.3 s since
    assert detector.update(2.9, encoder_delta=0.0)


def test_contact_ignores_a_stop_the_route_asked_for():
    """PICKUP_VISION_ONLY halts to confirm a target, and every approach leg
    halts the instant it arrives.  A car the route stopped itself is not a
    wall, however long it sits there."""
    detector = EncoderContactDetector()
    assert not detector.update(0.0, encoder_delta=200.0)    # moving: arms it

    for tick in range(40):
        assert not detector.update(2.0 + tick * 0.2, encoder_delta=0.0, commanded=False)

    # Nothing accumulated while uncommanded: the clock only starts now.
    assert not detector.update(10.4, encoder_delta=0.0)
    assert not detector.update(10.7, encoder_delta=0.0)
    assert detector.update(11.0, encoder_delta=0.0)


def test_motion_intents_exclude_a_deliberate_halt():
    """Which intents the detector is told are driving the car.

    stop and wait are deliberate halts and must never read as a wall; `d` is
    included because a distance move is still a move.  This is the runner's own
    dispatched intent, not the firmware's SPD echo -- the old detector compared
    that echo and it is what broke it."""
    assert "stop" not in _MOTION_INTENTS
    assert "wait" not in _MOTION_INTENTS
    assert _MOTION_INTENTS == {"v", "creep", "strafe", "d"}


# The real ENC counter tuples from the ramp ticks of run 11 (2026-09-18),
# `_full_r11_20260918.jsonl` rows 1240-1271.  The car was driving straight up the
# ramp at about 16 cm/s when the mean-based detector called it stopped and
# faulted the run 54 cm into a 280 cm leg.
_RUN11_RAMP_COUNTERS = [
    (-88372, 44638, -34558, 100117),
    (-88543, 44755, -34695, 100267),
    (-88705, 44896, -34848, 100418),
    (-88904, 45093, -35049, 100614),
    (-89049, 45239, -35198, 100759),
    (-89201, 45391, -35349, 100909),
    (-89408, 45596, -35553, 101114),
    (-89558, 45747, -35704, 101266),
]


def test_the_mirrored_mean_cannot_see_a_straight_drive_but_the_projection_can():
    """Run 11's fault, as one assertion pair.

    Straight up the ramp the four counters summed to a constant 21749..21761 --
    the wheels are mounted mirrored, so the left pair falls while the right pair
    rises by the same amount and their sum barely moves.  The mean delta sat at
    0.25..0.75, under counts_epsilon, while the forward projection advanced
    143..205 counts per sample.  The detector was reading the mean.
    """
    forward_deltas = []
    mean_deltas = []
    previous = None
    for raw in _RUN11_RAMP_COUNTERS:
        forward = _forward_counts(raw)
        mean = sum(raw) / 4.0
        if previous is not None:
            forward_deltas.append(abs(forward - previous[0]))
            mean_deltas.append(abs(mean - previous[1]))
        previous = (forward, mean)

    assert min(forward_deltas) > 100.0, forward_deltas
    assert mean_deltas[-1] <= 1.0, mean_deltas


def test_contact_does_not_fire_on_a_real_straight_drive_up_the_ramp():
    """The same eight real samples, through the detector itself.

    Spaced far enough apart that both the arm window and the hold window
    (min_approach_s + stationary_s) elapse, so a detector still reading the mean
    would fire here and one reading the projection must not.
    """
    detector = EncoderContactDetector()
    spacing = 0.3
    assert (len(_RUN11_RAMP_COUNTERS) - 1) * spacing > detector.min_approach_s + detector.stationary_s

    previous = None
    for index, raw in enumerate(_RUN11_RAMP_COUNTERS):
        forward = _forward_counts(raw)
        delta = 0.0 if previous is None else abs(forward - previous)
        previous = forward
        assert not detector.update(index * spacing, encoder_delta=delta)


class _EncoderReply:
    kind = "encoder"

    def __init__(self, lf, rf, lr, rr):
        self.value = f"LF {lf} RF {rf} LR {lr} RR {rr}"


class _ScriptedEncoderChassis:
    """Answers ENC with the next scripted tuple, and nothing otherwise.

    Faithful to the link in the way that matters here: the firmware answers only
    what it was asked, and the runner deliberately never queries on a tick that
    dispatched a command (the firmware answers only the first command of a
    burst).  A stub that replies on every poll would hide that.
    """

    def __init__(self, counters):
        self.counters = list(counters)
        self.sent = []
        self._pending = False

    def set_velocity(self, *args): self.sent.append(("V", *args))
    def run_distance(self, *args): self.sent.append(("D", *args))
    def stop(self): self.sent.append(("STOP",))
    def request_encoder(self): self.sent.append(("ENC",)); self._pending = True
    def request_speed(self): self.sent.append(("SPD",)); self._pending = False

    def poll(self):
        if not self._pending or not self.counters:
            return []
        self._pending = False
        return [_EncoderReply(*self.counters.pop(0))]


def test_runner_feeds_the_detector_the_forward_projection():
    """The runner-level half of the run-11 bug -- and the only test that covers
    the line computing the delta at all.

    Every other odometer test pokes _last_encoder_raw directly and its chassis
    returns [] from poll(), so the delta was never computed under test.  This
    one drives real ENC replies through poll(), at the route's own 0.05 s
    cadence so the query slot actually comes round.
    """
    chassis = _ScriptedEncoderChassis(_RUN11_RAMP_COUNTERS)
    runner = RouteRunner(RouteV2Config(), chassis, _CentredLine())
    runner.machine._enter(RouteState.JUNCTION_3_TO_PICKUP, 0.0)

    for tick in range(80):                     # 4 s, past the 2 s arm window
        runner.tick(tick * 0.05)

    assert not chassis.counters, "not every scripted reply was consumed"
    # The projection saw the car moving; the mean -- what the detector used to
    # be fed -- says it barely moved at all.
    assert runner._encoder_delta_forward > 100.0
    assert runner._encoder_delta <= 1.0
    assert runner.contact.stationary_since is None
    assert "ENC" in [name for name, *_ in chassis.sent]


class _StopCountingChassis:
    def __init__(self, fail_first=0):
        self.stops = 0
        self.fail_first = fail_first

    def stop(self):
        self.stops += 1
        if self.stops <= self.fail_first:
            raise OSError("link down")

    def set_velocity(self, *args): pass

    def run_distance(self, *args): pass

    def poll(self): return []


class _CentredLine:
    def poll_once(self): return {"sensor_mask": 0x81, "line_error": 0.0}


def test_the_exit_path_does_not_let_go_after_a_single_stop():
    """One STOP does not stop this chassis -- _command_stop has the measurement.
    The controlled halt on the --until path sent exactly one and the process
    exited, so on 2026-09-15 the car went on grinding into the pickup wall for
    several seconds after the arrival had already been declared, until the
    operator hit the emergency stop."""
    chassis = _StopCountingChassis()
    runner = RouteRunner(RouteV2Config(), chassis, _CentredLine(), sleeper=lambda _: None)
    runner._safe_stop()
    assert chassis.stops >= 10, f"let go after {chassis.stops} STOPs"


def test_the_exit_path_keeps_trying_when_a_stop_raises():
    """A dead link on the way out must not skip the remaining frames: the link
    can come back, and this is the last code that ever runs."""
    chassis = _StopCountingChassis(fail_first=3)
    runner = RouteRunner(RouteV2Config(), chassis, _CentredLine(), sleeper=lambda _: None)
    runner._safe_stop()
    assert chassis.stops >= 10


def test_dry_run_reports_a_bounded_loop_and_fake_tag(capsys):
    """The simulator runs a finite number of rounds; the real route keeps looping."""
    assert main(["--dry-run", "--config", "config/route_v2.yaml"]) == 0
    output = capsys.readouterr().out
    assert "DRY_RUN LOOPED" in output
    assert "tag 2 faked by the simulator" in output


def test_until_does_not_dispatch_the_target_states_own_command():
    """--until must end a leg without starting the next one.

    If the target state's own first intent went out, `--until
    JUNCTION_2_TURN_LEFT` would begin the left turn on its way to stopping --
    and a D in flight cannot reliably be interrupted on this chassis.
    """
    class Line:
        def poll_once(self): return {"sensor_mask": 0x81, "line_error": 0.0}

    class Chassis:
        def __init__(self): self.sent = []
        def set_velocity(self, *args): self.sent.append(("V", *args))
        def run_distance(self, *args): self.sent.append(("D", *args))
        def stop(self): self.sent.append(("STOP",))
        def poll(self): return []

    chassis = Chassis()
    runner = RouteRunner(RouteV2Config(), chassis, Line(),
                         stop_at=RouteState.JUNCTION_2_TURN_LEFT)
    runner.machine._enter(RouteState.JUNCTION_2_TURN_LEFT, 0.0)
    runner.tick(0.0)

    assert runner._reached_target is True
    assert ("STOP",) in chassis.sent
    assert not [item for item in chassis.sent if item[0] == "D"], (
        "--until started the turn it was supposed to stop before")


def test_run_returns_as_soon_as_the_target_is_reached():
    class Line:
        def poll_once(self): return {"sensor_mask": 0x81, "line_error": 0.0}

    class Chassis:
        def set_velocity(self, *args): pass
        def run_distance(self, *args): pass
        def stop(self): pass
        def poll(self): return []

    runner = RouteRunner(RouteV2Config(), Chassis(), Line(),
                         initial_state=RouteState.PICKUP_ARRIVED,
                         stop_at=RouteState.PICKUP_ARRIVED, sleeper=lambda _: None)
    assert runner.run(timeout_s=5.0) is RouteState.PICKUP_ARRIVED


def test_unknown_state_names_are_reported_cleanly(capsys):
    """--from NOPE used to raise an uncaught KeyError and print a traceback where
    every other bad argument prints a FAULT_SAFE line."""
    for argv in (["--dry-run", "--only", "NOPE", "--config", "config/route_v2.yaml"],
                 ["--dry-run", "--from", "NOPE", "--config", "config/route_v2.yaml"],
                 ["--dry-run", "--to", "NOPE", "--config", "config/route_v2.yaml"],
                 ["--dry-run", "--until", "NOPE", "--config", "config/route_v2.yaml"]):
        capsys.readouterr()
        assert main(argv) == 2, argv
        output = capsys.readouterr().out
        assert "unknown state: NOPE" in output, argv
        assert "Traceback" not in output, argv


def test_dry_run_honours_until(capsys):
    argv = ["--dry-run", "--until", "JUNCTION_2_TURN_LEFT", "--config", "config/route_v2.yaml"]
    assert main(argv) == 0
    assert "DRY_RUN REACHED JUNCTION_2_TURN_LEFT" in capsys.readouterr().out

    # A state the route never reaches must be reported as not reached.
    argv = ["--dry-run", "--until", "FAULT", "--config", "config/route_v2.yaml"]
    assert main(argv) == 1
    assert "never reached FAULT" in capsys.readouterr().out


def test_dry_run_visits_both_post_turn_seeks():
    """Both post-turn line hunts must actually be exercised by the simulator.

    They were not: the mask fixture is chosen from the state at the TOP of the
    loop, but a turn hands over to a seek *inside* one step() call, so the first
    seek tick inherited the turn's mask -- which defaults to 0x81, a "line
    found" reading -- and the seek was entered and left in the same tick.  The
    dry run then reported a clean route while never practising the manoeuvre
    that has already cost one field run.
    """
    from run_route_v2 import run_simulation
    visited = run_simulation(RouteV2Config(), (RouteState.START_TO_JUNCTION_1,))
    assert RouteState.JUNCTION_2_SEEK_LINE in visited
    assert RouteState.JUNCTION_3_SEEK_LINE in visited
    # And in that order, with the legs they belong to.
    assert visited.index(RouteState.JUNCTION_2_SEEK_LINE) < visited.index(RouteState.JUNCTION_2_LINE_FOLLOW)
    assert visited.index(RouteState.JUNCTION_3_SEEK_LINE) < visited.index(RouteState.JUNCTION_2_TO_JUNCTION_3)


def test_dry_run_refuses_to_call_a_placeholder_window_a_success(monkeypatch, capsys):
    """A dry run that comes to rest inside the tag-2 strafe or the J2 east leg
    must say why rather than reporting a plain stop."""
    monkeypatch.setattr("run_route_v2.run_simulation",
                        lambda config, states: [RouteState.JUNCTION_1_STRAFE_TO_TAG_2])
    assert main(["--dry-run", "--config", "config/route_v2.yaml"]) == 1
    output = capsys.readouterr().out
    assert "DRY_RUN STOPPED JUNCTION_1_STRAFE_TO_TAG_2" in output
    assert "measured on the track" in output


def test_unknown_state_is_rejected():
    assert main(["--dry-run", "--only", "NOPE", "--config", "config/route_v2.yaml"]) == 2


def test_runner_sends_v_periodically_and_d_once():
    class Line:
        def poll_once(self): return {"sensor_mask": 0, "line_error": 0.0}
    class Chassis:
        def __init__(self): self.sent = []
        def set_velocity(self, *args): self.sent.append(("V", *args))
        def run_distance(self, *args): self.sent.append(("D", *args))
        def stop(self): self.sent.append(("STOP",))
        def poll(self): return []
    chassis = Chassis()
    runner = RouteRunner(RouteV2Config(), chassis, Line())
    runner.tick(0.0)
    runner.tick(0.1)
    assert [item[0] for item in chassis.sent] == ["V"]
    runner.machine.state = RouteState.JUNCTION_3_TURN_LEFT
    runner.tick(1.0)
    runner.tick(1.1)
    assert [item[0] for item in chassis.sent].count("D") == 1


def test_runner_stops_when_line_error_is_missing():
    class Line:
        def poll_once(self): return {"sensor_mask": 0, "line_error": None}
    class Chassis:
        def __init__(self): self.sent = []
        def set_velocity(self, *args): self.sent.append(("V", *args))
        def run_distance(self, *args): self.sent.append(("D", *args))
        def stop(self): self.sent.append(("STOP",))
        def poll(self): return []
    chassis = Chassis()
    RouteRunner(RouteV2Config(), chassis, Line()).tick(0.0)
    assert chassis.sent == [("STOP",)]


def test_runner_holds_course_on_line_loss_where_configured():
    """The final leg's line detection stutters WHILE THE CAR IS ON THE LINE, so
    a lost frame is noise, not news.  Operator 2026-09-16: "即使丢线了 也继续直行".

    Measured on that day's two runs, the leg is 42% lost frames and the PID
    answered every one of them with STOP (18 and 20 of them), which is what
    turned a 3.2 m straight into stop-start."""
    class Line:
        def poll_once(self): return {"sensor_mask": 0xFF, "line_error": None}
    class Chassis:
        def __init__(self): self.sent = []
        def set_velocity(self, *args): self.sent.append(("V", *args))
        def run_distance(self, *args): self.sent.append(("D", *args))
        def stop(self): self.sent.append(("STOP",))
        def poll(self): return []
    config = RouteV2Config(hold_course_on_line_loss=("JUNCTION_PICKUP_3_TO_AREA",))
    chassis = Chassis()
    runner = RouteRunner(config, chassis, Line())
    runner.machine.state = RouteState.JUNCTION_PICKUP_3_TO_AREA
    runner.tick(0.0)
    assert chassis.sent == [("V", config.pickup_speed, 0, 0)], (
        "a stutter on this leg must drive straight, not stop the car")


def test_runner_still_stops_on_line_loss_elsewhere():
    """The hold-course rule is scoped: every other leg keeps the STOP, because
    on those a lost line is information.  JUNCTION_2_TO_JUNCTION_3 in particular
    exists to absorb the previous turn's error by following the line."""
    class Line:
        def poll_once(self): return {"sensor_mask": 0xFF, "line_error": None}
    class Chassis:
        def __init__(self): self.sent = []
        def set_velocity(self, *args): self.sent.append(("V", *args))
        def run_distance(self, *args): self.sent.append(("D", *args))
        def stop(self): self.sent.append(("STOP",))
        def poll(self): return []
    config = RouteV2Config(hold_course_on_line_loss=("JUNCTION_PICKUP_3_TO_AREA",))
    chassis = Chassis()
    runner = RouteRunner(config, chassis, Line())
    runner.machine.state = RouteState.JUNCTION_2_TO_JUNCTION_3
    runner.tick(0.0)
    assert chassis.sent == [("STOP",)]


def test_runner_handles_line_service_before_first_frame():
    class Line:
        def poll_once(self): return {"sensor_mask": None, "line_error": None, "state": "WAITING_DATA"}
    class Chassis:
        def __init__(self): self.sent = []
        def set_velocity(self, *args): self.sent.append(("V", *args))
        def run_distance(self, *args): self.sent.append(("D", *args))
        def stop(self): self.sent.append(("STOP",))
        def poll(self): return []
    chassis = Chassis()
    RouteRunner(RouteV2Config(), chassis, Line()).tick(0.0)
    assert chassis.sent == [("STOP",)]


class StrafeLine:
    """The car is strafing across the all-black area immediately right of J1."""
    def poll_once(self): return {"sensor_mask": 0x00, "line_error": 0.0}


class StrafeChassis:
    def __init__(self): self.sent = []
    def set_velocity(self, *args): self.sent.append(("V", *args))
    def run_distance(self, *args): self.sent.append(("D", *args))
    def stop(self): self.sent.append(("STOP",))
    def poll(self): return []


def strafing_runner(config, chassis, **kwargs):
    """A runner sitting in the strafe state with a known lateral odometer.

    The runner derives travel_cm and lateral_cm from the last ENC reply, so the
    baseline is seeded here rather than faked through a poll: the strafe moves
    the car sideways, and it is the LATERAL projection that has to be non-zero.
    """
    runner = RouteRunner(config, chassis, StrafeLine(), **kwargs)
    runner.machine._enter(RouteState.JUNCTION_1_STRAFE_TO_TAG_2, 0.0)
    runner._travel_state = RouteState.JUNCTION_1_STRAFE_TO_TAG_2
    runner._travel_base = 0.0
    runner._lateral_base = 0.0
    return runner


def lateral_counts(cm):
    """Raw encoder counts whose lateral projection is `cm`."""
    counts = round(cm * 56.8 * 4)
    return [counts // 2, counts - counts // 2, 0, 0]


def test_runner_strafes_with_a_v_command_and_never_a_d():
    """D is self-terminating and open loop, and whether STOP interrupts one in
    flight is unverified on this chassis.  A strafe that ends on a landmark has
    to stop on the tick the tag appears, so it must be a held velocity."""
    chassis = StrafeChassis()
    runner = strafing_runner(RouteV2Config(tag2_strafe_speed=20), chassis)
    runner._last_encoder_raw = lateral_counts(5.0)   # below the floor
    runner.tick(0.0)
    runner.tick(0.1)
    # vy negative = rightward, and V's lateral sign is measured -- unlike D's
    # rotation sign, which is not.
    assert [item for item in chassis.sent if item[0] == "V"] == [("V", 0, -20, 0)]
    assert not [item for item in chassis.sent if item[0] == "D"]


def test_runner_leaves_the_strafe_the_tick_the_tag_becomes_stable():
    """The end-to-end proof that the tag reaches the state machine: the tracker
    needs three consecutive detections, and the strafe must end on that tick."""
    class Camera:
        def read(self): return True, object()
        def release(self): pass

    class Detector:
        def detect(self, frame, **kwargs):
            return (TagObservation(
                id=2,
                family="36H11",
                corners_px=((10.0, 10.0), (20.0, 10.0), (20.0, 20.0), (10.0, 20.0)),
                center_px=(15.0, 15.0),
                decision_margin=0.0,
                timestamp_ns=kwargs["timestamp_ns"],
                frame_index=kwargs.get("frame_index", 0),
            ),)

    chassis = StrafeChassis()
    runner = strafing_runner(RouteV2Config(), chassis,
                             camera=Camera(), tag_detector=Detector())
    runner._last_encoder_raw = lateral_counts(40.0)   # inside the window
    runner.tick(0.0)
    assert runner.machine.state is RouteState.JUNCTION_1_STRAFE_TO_TAG_2
    runner.tick(0.1)
    assert runner.machine.state is RouteState.JUNCTION_1_STRAFE_TO_TAG_2
    runner.tick(0.2)   # third consecutive detection -> stable
    assert runner.machine.state is RouteState.TAG_2_TURN_RIGHT
    assert chassis.sent[-1][0] == "D"


def test_runner_passes_tag_stability_into_the_state_machine():
    """The tag result used to reach telemetry and nothing else -- no route
    decision could depend on it.  This pins the call site so the wiring cannot
    silently disappear again."""
    class Line:
        def poll_once(self): return {"sensor_mask": 0x81, "line_error": 0.0}

    class Chassis:
        def set_velocity(self, *args): pass
        def run_distance(self, *args): pass
        def stop(self): pass
        def poll(self): return []

    class Camera:
        def read(self): return True, object()
        def release(self): pass

    class Detector:
        def detect(self, frame, **kwargs):
            return (TagObservation(
                id=2,
                family="36H11",
                corners_px=((10.0, 10.0), (20.0, 10.0), (20.0, 20.0), (10.0, 20.0)),
                center_px=(15.0, 15.0),
                decision_margin=0.0,
                timestamp_ns=kwargs["timestamp_ns"],
                frame_index=kwargs.get("frame_index", 0),
            ),)

    runner = RouteRunner(RouteV2Config(), Chassis(), Line(),
                         camera=Camera(), tag_detector=Detector())
    seen = []
    original = runner.machine.step

    def spy(now, **kwargs):
        seen.append(kwargs.get("tag_stable"))
        return original(now, **kwargs)

    runner.machine.step = spy
    runner.tick(0.0)
    runner.tick(0.1)
    runner.tick(0.2)
    assert seen == [False, False, True]


def test_runner_turns_at_the_strafe_ceiling_with_no_camera():
    """With no camera the tracker never reports stability, so the strafe runs to
    its odometer ceiling -- and now commits to the turn there instead of holding.
    What keeps that bounded is the state after it: JUNCTION_2_SEEK_LINE has to
    find the line on the sensor within seek_line_max_cm, or stop and hold."""
    chassis = StrafeChassis()
    config = RouteV2Config(tag2_search_min_cm=10, tag2_search_max_cm=120)
    runner = strafing_runner(config, chassis)
    runner._last_encoder_raw = lateral_counts(125.0)   # past the ceiling
    runner.tick(0.0)
    assert runner.machine.state is RouteState.TAG_2_TURN_RIGHT
    assert runner.machine.tag2_search_failed is True
    assert [item for item in chassis.sent if item[0] == "D"]


def test_telemetry_records_one_line_per_tick():
    class Line:
        def poll_once(self): return {"sensor_mask": 0x81, "line_error": 0.25}
    class Chassis:
        def set_velocity(self, *args): pass
        def run_distance(self, *args): pass
        def stop(self): pass
        def poll(self): return []
    records = []
    runner = RouteRunner(RouteV2Config(), Chassis(), Line(), telemetry=records.append)
    runner.tick(0.0)
    runner.tick(0.5)
    assert len(records) == 2
    assert records[0]["state"] == "START_TO_JUNCTION_1"
    assert records[0]["intent"] == "v"
    assert records[0]["mask"] == 0x81
    assert records[0]["line_error"] == 0.25
    assert records[0]["issued"].startswith("V ")


def test_pickup_diagnostics_include_reason_and_result():
    runtime = RouteVisionRuntime.__new__(RouteVisionRuntime)
    target = SimpleNamespace(
        center_px=(323.0, 310.0),
        frame_index=4,
        to_dict=lambda: {"center_px": [323.0, 310.0]},
    )
    pickup = SimpleNamespace(
        phase=SimpleNamespace(value="FAULT"),
        kind="fault",
        speed=0,
        reason="pickup_depth_out_of_range",
        result="pickup_depth_out_of_range",
    )
    runtime.start = lambda: None
    runtime.vision = _visual_route_config().vision
    runtime.config = _visual_route_config()
    runtime.frames = SimpleNamespace(snapshot=lambda **_kwargs: SimpleNamespace(
        fresh=True, frame_id=4, captured_at=.4, age_s=0.0, image=None,
    ))
    runtime.capture = SimpleNamespace(status=lambda: SimpleNamespace(error=None))
    runtime.worker = SimpleNamespace(
        status=lambda: SimpleNamespace(error=None),
        result=lambda: SimpleNamespace(
            generation=1,
            captured_at=.4,
            frame_id=4,
            value=SimpleNamespace(accepted=(target,)),
        ),
    )
    runtime._task = VisionTask.PURPLE_CLOSE
    runtime._generation = 1
    runtime._task_selected_at = 0.0
    runtime._task_first_fresh_frame_id = 3
    runtime._task_ready = True
    runtime._pickup_controller = SimpleNamespace(
        select_target=lambda _items: target,
        step=lambda **_kwargs: pickup,
    )
    runtime._pickup_area = "purple"
    runtime._pickup_baseline_cm = 0.0
    runtime._return_controller = None

    _route_input, diagnostics = runtime.observe(
        state=RouteState.PICKUP_VISION_ONLY,
        now=.4,
        absolute_lateral_cm=0.0,
        wall_contact=False,
        stopped=True,
        sensor_mask=0xFF,
    )

    assert diagnostics["pickup_reason"] == "pickup_depth_out_of_range"
    assert diagnostics["pickup_result"] == "pickup_depth_out_of_range"


def test_runner_reports_stable_tag2_observation_in_telemetry():
    class Line:
        def poll_once(self): return {"sensor_mask": 0x81, "line_error": 0.0}

    class Chassis:
        def set_velocity(self, *args): pass
        def run_distance(self, *args): pass
        def stop(self): pass
        def poll(self): return []

    class Camera:
        def read(self): return True, object()
        def release(self): pass

    class Detector:
        def detect(self, frame, **kwargs):
            return (TagObservation(
                id=2,
                family="36H11",
                corners_px=((10.0, 10.0), (20.0, 10.0), (20.0, 20.0), (10.0, 20.0)),
                center_px=(15.0, 15.0),
                decision_margin=0.0,
                timestamp_ns=kwargs["timestamp_ns"],
                frame_index=kwargs.get("frame_index", 0),
            ),)

    records = []
    runner = RouteRunner(RouteV2Config(), Chassis(), Line(), camera=Camera(),
                         tag_detector=Detector(), telemetry=records.append)
    runner.tick(0.0)
    runner.tick(0.1)
    runner.tick(0.2)

    assert records[-1]["tag2"]["stable"] is True
    assert records[-1]["tag2"]["id"] == 2
    assert records[-1]["tag2"]["consecutive_frames"] == 3


def test_runner_converts_chassis_io_error_to_safe_fault_without_second_traceback():
    class Line:
        def poll_once(self): return {"sensor_mask": 0, "line_error": 0.0}
        def close(self): pass
    class BrokenChassis:
        def poll(self): raise OSError(5, "Input/output error")
        def stop(self): raise OSError(5, "Input/output error")
    runner = RouteRunner(RouteV2Config(), BrokenChassis(), Line(), sleeper=lambda _: None)
    try:
        runner.run(timeout_s=1)
    except RuntimeError as exc:
        assert "chassis I/O" in str(exc)
    else:
        raise AssertionError("expected controlled chassis I/O fault")


def test_main_reports_disconnected_chassis_as_fault_safe(monkeypatch, capsys):
    class BrokenChassis:
        def poll(self):
            raise OSError(5, "Input/output error")
        def stop(self):
            raise OSError(5, "Input/output error")

    class Line:
        snapshot = type("Snapshot", (), {"connected": True, "error": None, "state": "FOLLOWING"})()
        def start(self): pass
        def poll_once(self): return {"sensor_mask": 0, "line_error": 0.0}
        def close(self): pass

    monkeypatch.setattr("rg_runtime.transports.SerialTransport", lambda *args, **kwargs: object())
    monkeypatch.setattr("rg_runtime.devices.ChassisDevice", lambda transport: BrokenChassis())
    monkeypatch.setattr("control_hub.services.line_service.LineSensorService", lambda **kwargs: Line())
    monkeypatch.setattr("cv2.VideoCapture", lambda _: type("Camera", (), {
        "isOpened": lambda self: False,
        "release": lambda self: None,
    })())

    assert main(["--full", "--config", "config/route_v2.yaml", "--runtime-config", "config/runtime.yaml"]) == 2
    output = capsys.readouterr().out
    # Not startswith(): _wait_for_chassis_device prints its "waiting for ... /
    # ... is back" notice first whenever the RFCOMM maintainer is rebuilding the
    # node at that moment -- which is exactly when it is missing.  On Windows it
    # returns early, so startswith held there and only the Pi could show this.
    # What the test is actually about is a clean fault line and no stack trace.
    assert any(line.startswith("FAULT_SAFE:") for line in output.splitlines())
    assert "Traceback" not in output


def test_route_hardware_reuses_runtime_line_sensor_settings(monkeypatch, tmp_path):
    captured = {}

    class FakeLine:
        snapshot = type("Snapshot", (), {"connected": True, "error": None, "state": "FOLLOWING"})()

        def start(self):
            pass

        def close(self):
            pass

    class FakeChassis:
        def stop(self):
            pass

    class FakeTransport:
        def close(self):
            pass

    class FakeCamera:
        def isOpened(self):
            return False

        def release(self):
            pass

    def make_line(**kwargs):
        captured.update(kwargs)
        return FakeLine()

    monkeypatch.setattr("rg_runtime.transports.SerialTransport", lambda *args, **kwargs: FakeTransport())
    monkeypatch.setattr("rg_runtime.devices.ChassisDevice", lambda transport: FakeChassis())
    monkeypatch.setattr("control_hub.services.line_service.LineSensorService", make_line)
    monkeypatch.setattr("cv2.VideoCapture", lambda _: FakeCamera())

    calibrated = tmp_path / "route_v2.yaml"
    calibrated.write_text(
        Path("config/route_v2.yaml").read_text(encoding="utf-8").replace(
            "calibrated: false", "calibrated: true", 1
        ),
        encoding="utf-8",
    )
    assert main(["--full", "--config", str(calibrated),
                 "--runtime-config", "config/runtime.yaml"]) == 2
    assert captured["active_level"] == 0
    assert captured["enabled"] is True


class _StubCv2:
    """Just the constants _configure_camera reads, so these do not depend on
    OpenCV being importable or on its real enum values."""

    CAP_PROP_FOURCC = 6
    CAP_PROP_FRAME_WIDTH = 3
    CAP_PROP_FRAME_HEIGHT = 4
    CAP_PROP_FPS = 5
    CAP_PROP_BUFFERSIZE = 38

    @staticmethod
    def VideoWriter_fourcc(*fourcc):
        return fourcc


class _Frame:
    def __init__(self, shape):
        self.shape = shape


class _FakeCapture:
    def __init__(self, shape=(720, 1280, 3), frames=15):
        self.set_calls = []
        self._shape = shape
        self._frames = frames

    def set(self, prop, value):
        self.set_calls.append((prop, value))
        return True

    def read(self):
        if self._frames <= 0:
            return False, None
        self._frames -= 1
        return True, _Frame(self._shape)


def _camera_config():
    return SimpleNamespace(pixel_format="MJPG", width=1280, height=720, fps=30.0)


def test_camera_setup_never_sets_fps_the_property_that_killed_the_pipeline():
    """MEASURED 2026-09-15 on the Pi: of six capture configurations, the only two
    that failed to open were the two that set CAP_PROP_FPS -- and they failed in
    the middle of the sweep, with the configurations after them succeeding, so it
    is the property and not a device left busy.

    Setting it costs a whole field run: the pipeline dies at startup, tag 2 is
    never detected, and the car strafes to its ceiling before anything says so.
    """
    capture = _FakeCapture()
    assert _configure_camera(capture, _camera_config(), _StubCv2) == []

    props = [prop for prop, _ in capture.set_calls]
    assert _StubCv2.CAP_PROP_FPS not in props
    assert _StubCv2.CAP_PROP_BUFFERSIZE not in props


def test_camera_setup_asks_for_fourcc_before_the_size():
    """On V4L2 the pixel format has to be set first or the driver clamps the size
    to what the current format supports -- and this is the order that measured
    1280x720."""
    capture = _FakeCapture()
    _configure_camera(capture, _camera_config(), _StubCv2)
    assert [prop for prop, _ in capture.set_calls] == [
        _StubCv2.CAP_PROP_FOURCC,
        _StubCv2.CAP_PROP_FRAME_WIDTH,
        _StubCv2.CAP_PROP_FRAME_HEIGHT,
    ]


def test_camera_setup_refuses_to_start_on_a_capture_that_delivers_no_frames():
    """The 2026-09-15 failure as a unit test: a camera that opens but never
    produces a frame looked exactly like a camera that works, until 91 cm of
    strafe had been spent proving otherwise."""
    capture = _FakeCapture(frames=0)
    with pytest.raises(RuntimeError, match="delivered no frames"):
        _configure_camera(capture, _camera_config(), _StubCv2)


def test_camera_setup_warns_when_the_frames_are_not_the_calibrated_size():
    """get() is deliberately not consulted: measured the same day, a capture
    delivering real 1280x720 frames reports 640x480 through get(), so only a
    decoded frame is honest."""
    capture = _FakeCapture(shape=(480, 640, 3))
    warnings = _configure_camera(capture, _camera_config(), _StubCv2)
    assert warnings and "640x480" in warnings[0]


def test_visual_route_camera_setup_rejects_noncalibrated_frame_size():
    capture = _FakeCapture(shape=(480, 640, 3))

    with pytest.raises(RuntimeError, match="calibrated 1280x720"):
        _configure_camera(
            capture,
            _camera_config(),
            _StubCv2,
            require_calibrated_size=True,
        )


def test_visual_route_requires_a_readable_camera_calibration_config():
    def broken_loader(_path):
        raise ValueError("invalid calibration")

    with pytest.raises(RuntimeError, match="requires a valid camera calibration"):
        _load_route_camera_config(
            Path("missing-camera-config.yaml"),
            broken_loader,
            required=True,
        )


def test_nonvisual_route_can_fall_back_when_camera_config_is_unavailable():
    def broken_loader(_path):
        raise ValueError("invalid calibration")

    assert _load_route_camera_config(
        Path("missing-camera-config.yaml"),
        broken_loader,
        required=False,
    ) is None


class _TickRecordingChassis:
    """Records every write against the tick it was made on.

    The firmware answers only the first command of a burst (handoff section
    6.1), so "how many commands went out on a single tick" is the property under
    test.  A flat list of calls cannot express it.
    """

    def __init__(self):
        self.sent = []
        self.tick = -1

    def _record(self, name):
        self.sent.append((self.tick, name))

    def set_velocity(self, *args): self._record("V")
    def run_distance(self, *args): self._record("D")
    def stop(self): self._record("STOP")
    def request_encoder(self): self._record("ENC")
    def request_speed(self): self._record("SPD")
    def poll(self): return []


def test_the_wall_approach_never_puts_two_commands_on_the_wire_in_one_tick():
    """The regression that cost the 2026-09-15 evening run.

    Arrival on the three wall-approach legs is the odometer gate, and this
    branch used to feed it by writing SPD and ENC back to back.  The firmware
    answers only the first command of a burst, so ENC was the one it dropped --
    on exactly the legs whose arrival test reads the encoder.  Measured on
    JUNCTION_3_TO_PICKUP: 25 distinct SPD values across 377 ticks against a raw
    encoder tuple that never changed once, so travel_cm sat at 0.0 and
    _reached_gate(0.0, 90.0) could never fire.  The stall detector died with it,
    because its arm condition wants abs(delta) > 1.0 and the frozen delta was
    0.25.  Both arrival criteria, one dropped reply.
    """
    chassis = _TickRecordingChassis()
    runner = RouteRunner(RouteV2Config(), chassis, _CentredLine())
    runner.machine._enter(RouteState.JUNCTION_3_TO_PICKUP, 0.0)

    for tick in range(60):
        chassis.tick = tick
        runner.tick(tick * 0.1)

    assert chassis.sent, "the leg issued nothing at all"
    per_tick = {tick: sum(1 for t, _ in chassis.sent if t == tick)
                for tick, _ in chassis.sent}
    worst = max(per_tick.values())
    assert worst == 1, f"{worst} commands on one tick: {chassis.sent}"

    names = [name for _, name in chassis.sent]
    assert "ENC" in names, "the approach leg never asked for the encoder at all"


class _SettableLine:
    """A line service whose reading the test can change mid-leg."""

    def __init__(self, mask=0x81, line_error=0.0):
        self.mask = mask
        self.line_error = line_error

    def poll_once(self):
        return {"sensor_mask": self.mask, "line_error": self.line_error}


class _CommandLog:
    def __init__(self): self.sent = []

    def set_velocity(self, *args): self.sent.append(("V",) + args)
    def run_distance(self, *args): self.sent.append(("D",) + args)
    def stop(self): self.sent.append(("STOP",))
    def poll(self): return []


def test_the_first_return_creeps_off_the_line_instead_of_stopping_on_it():
    """The bug that ended the 2026-09-15 full3 run 4.98 cm into the reverse.

    The runner sends a "v" intent to the line-following PID, and that PID issues
    STOP the moment line_error disappears.  This leg reverses off the line on
    purpose -- that is what it is FOR -- so routing it through "v" stopped the
    car on the first tick it succeeded, and the run then re-sent STOP until it
    was killed by hand.  Both phases must be "creep": a straight vx with the PID
    reset, the same path the J1 manoeuvre uses.

    The state machine's own test asserted kind == "v" for years of this leg's
    life, which is exactly how the bug survived -- so this one is asserted at
    the runner, on the command that actually reaches the chassis.
    """
    config = RouteV2Config()
    chassis = _CommandLog()
    line = _SettableLine()                      # on the line: reverse first
    runner = RouteRunner(config, chassis, line)
    runner.machine._enter(RouteState.PICKUP_1_RETURN, 0.0)

    runner.tick(0.0)
    reversing = [c for c in chassis.sent if c[0] == "V"]
    assert reversing, "the return leg never moved off the line"
    assert reversing[0][1] < 0, "the first phase must reverse"
    assert (reversing[0][2], reversing[0][3]) == (0, 0),         "a creep is straight vx only -- a lateral term fights the alignment"

    line.mask, line.line_error = 0xFF, None     # the bar leaves the line
    runner.tick(0.3)
    runner.tick(0.6)

    assert not [c for c in chassis.sent if c[0] == "STOP"], (
        "the return leg stopped on a lost line -- it is routed through the "
        f"line-following PID: {chassis.sent}")
    forwards = [c for c in chassis.sent if c[0] == "V" and c[1] > 0]
    assert forwards, "having lost the line it must hunt forward for the landmark"
    assert all((c[2], c[3]) == (0, 0) for c in chassis.sent if c[0] == "V")
