from rg_runtime.devices import ArmDevice, ChassisDevice
from rg_runtime.hardware_models import ArmMode
from rg_runtime.transports import MemoryTransport


def test_chassis_sends_velocity_and_stop():
    transport = MemoryTransport()
    chassis = ChassisDevice(transport)
    chassis.set_velocity(20, -5, 0)
    chassis.stop()
    assert transport.sent == ["V 20 -5 0\r\n", "STOP\r\n"]


def test_chassis_sends_named_motor_and_encoder_commands():
    transport = MemoryTransport()
    chassis = ChassisDevice(transport)
    chassis.motor_test("LF", -20)
    chassis.reset_encoder()
    assert transport.sent == ["M LF -20\r\n", "ENC RESET\r\n"]


def test_arm_enable_run_and_done_state():
    transport = MemoryTransport(["ACK,ENABLED\r\n"])
    arm = ArmDevice(transport)
    arm.state = arm.state.__class__(mode=ArmMode.LOCKED, calibrated=True)
    arm.enable()
    arm.poll()
    arm.run(2)
    transport.feed("ACK,RUN,2\r\n", "EVENT,DONE,2\r\n")
    events = arm.poll()
    assert [event.command for event in events[:1]] == ["RUN"]
    assert events[1].value == "2"
    assert arm.state.mode is ArmMode.READY


def test_arm_rejects_run_when_busy():
    transport = MemoryTransport()
    arm = ArmDevice(transport)
    arm.state = arm.state.__class__(mode=ArmMode.BUSY, routine=1)
    try:
        arm.run(2)
    except RuntimeError as exc:
        assert "READY" in str(exc)
    else:
        raise AssertionError("run must reject BUSY state")


def test_arm_servo_and_move_validate_and_format_positions():
    transport = MemoryTransport()
    arm = ArmDevice(transport)
    arm.state = arm.state.__class__(mode=ArmMode.READY, calibrated=True)

    arm.servo(2, 1650, 3000)
    arm.move([1500, 1500, 1650, 1500, 1500], 3000)

    assert transport.sent == [
        "ARM,SERVO,2,1650,3000\r\n",
        "ARM,MOVE,1500,1500,1650,1500,1500,3000\r\n",
    ]


def test_arm_servo_and_move_reject_not_ready_or_bad_values():
    arm = ArmDevice(MemoryTransport())
    for operation in (
        lambda: arm.servo(0, 1500, 3000),
        lambda: arm.move([1500] * 5, 3000),
    ):
        try:
            operation()
        except RuntimeError as exc:
            assert "READY" in str(exc)
        else:
            raise AssertionError("manual movement must require READY")


def test_arm_enable_requires_probed_calibrated_locked_state():
    arm = ArmDevice(MemoryTransport())
    for mode, calibrated in ((ArmMode.UNKNOWN, None), (ArmMode.FAULT, True), (ArmMode.LOCKED, False)):
        arm.state = arm.state.__class__(mode=mode, calibrated=calibrated)
        try:
            arm.enable()
        except RuntimeError:
            pass
        else:
            raise AssertionError("enable must require calibrated LOCKED state")


def test_arm_suction_tracks_command_and_repeated_run_clears_old_done():
    transport = MemoryTransport()
    arm = ArmDevice(transport)
    arm.state = arm.state.__class__(mode=ArmMode.READY, calibrated=True)
    arm.suction(True)
    assert arm.state.suction_commanded is True
    arm.last_done_routine = 2
    arm.run(2)
    assert arm.last_done_routine is None


def test_manual_move_rejects_keep_position_sentinel():
    arm = ArmDevice(MemoryTransport())
    arm.state = arm.state.__class__(mode=ArmMode.READY, calibrated=True)
    try:
        arm.move([1500, 1500, 65535, 1500, 1500], 3000)
    except ValueError:
        pass
    else:
        raise AssertionError("manual move must reject 65535")
