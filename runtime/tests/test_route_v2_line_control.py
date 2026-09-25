from route_v2.line_control import JunctionDebouncer, LinePidController

# Measured readings.  ON_LINE is the centred reading (middle six probes lit);
# JUNCTION is a branch leaving to the right (x8 joins them).
ON_LINE = 0x81
JUNCTION = 0x80
LOST = 0xFF


def test_junction_does_not_count_until_the_car_has_tracked_the_line():
    # Real incident: a detector that arms immediately fires on whatever it is
    # already sitting on.  The start position is itself a 0x80 whenever the car
    # is a little off-centre, and the route skipped two junctions in the first
    # second.  Nothing counts before the line has been tracked normally.
    detector = JunctionDebouncer(confirm_frames=3, leave_frames=2)
    assert [detector.update(JUNCTION) for _ in range(5)] == [False] * 5


def test_junction_requires_debounce_and_rearms_after_leaving():
    detector = JunctionDebouncer(confirm_frames=3, leave_frames=2)
    assert [detector.update(ON_LINE) for _ in range(2)] == [False, False]
    assert [detector.update(JUNCTION) for _ in range(2)] == [False, False]
    assert detector.update(JUNCTION) is True
    assert detector.update(JUNCTION) is False
    assert detector.update(ON_LINE) is False
    assert detector.update(ON_LINE) is False
    assert detector.update(JUNCTION) is False
    assert detector.update(JUNCTION) is False
    assert detector.update(JUNCTION) is True


def test_all_black_and_lost_readings_are_not_junctions():
    # 0x00 (every probe on black) is any black area wider than the bar -- there
    # is a 5 cm one just after the start.  0xFF is every probe off the line: the
    # car has left the track entirely, and the route runner reports a missing
    # sensor reading as 0xFF precisely so it can never be mistaken for a
    # junction.  Neither may advance the route.
    detector = JunctionDebouncer(confirm_frames=2, leave_frames=2)
    for _ in range(2):
        detector.update(ON_LINE)
    assert [detector.update(0x00) for _ in range(6)] == [False] * 6
    assert [detector.update(LOST) for _ in range(6)] == [False] * 6


def test_pid_is_bounded_and_resets_integral():
    pid = LinePidController(kp=8, ki=0.3, kd=1.2, lateral_limit=8, yaw_limit=4)
    output = pid.update(1.0, 0.05, vx=15)
    assert -8 <= output.vy <= 8
    assert -4 <= output.wz <= 4
    pid.reset()
    assert pid.update(0.0, 0.05, vx=15) == pid.update(0.0, 0.05, vx=15)


def test_reverse_uses_separate_correction_sign():
    pid = LinePidController(kp=8, ki=0, kd=0, reverse_sign=-1)
    forward = pid.update(0.5, 0.1, vx=15)
    pid.reset()
    reverse = pid.update(0.5, 0.1, vx=-12)
    assert forward.vy == -reverse.vy
