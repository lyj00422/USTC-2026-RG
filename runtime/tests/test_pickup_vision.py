from pathlib import Path
from dataclasses import replace

from rg_runtime.models import BlockColor, BlockObservation
import route_v2.pickup_vision as pickup_vision
from route_v2.config import load_route_v2_config
from route_v2.pickup_vision import (
    PickupPhase,
    PickupReturnController,
    PickupVisionController,
)
from route_v2.vision_config import MotionGuard


ROOT = Path(__file__).parents[1]


def target(x, *, frame, color=BlockColor.PURPLE, y=300):
    return BlockObservation(
        color=color,
        bounding_box=(int(x - 30), int(y - 40), 60, 80),
        center_px=(float(x), float(y)),
        area=4800,
        confidence=0.9,
        frame_index=frame,
    )


def controller(area, *, initial_search="right"):
    cfg = load_route_v2_config(ROOT / "config" / "route_v2.yaml")
    return PickupVisionController(
        area,
        cfg.vision.pickup_areas[area],
        cfg.vision.block_profiles[cfg.vision.pickup_areas[area].profile],
        frame_size=(640, 480),
        initial_search=initial_search,
    )


def test_prescan_prefers_center_when_all_three_regions_have_blocks():
    scan = pickup_vision.PurplePrescanTracker(
        frame_budget=10, confirm_frames=3, center_left=0.35, center_right=0.65,
    )

    for frame in range(1, 4):
        result = scan.update(
            (target(100, frame=frame), target(500, frame=frame), target(900, frame=frame)),
            frame_width=1000,
        )

    assert result.present is True
    assert result.hint == "center"
    assert result.region_counts == {"left": 3, "center": 3, "right": 3}


def test_prescan_prefers_right_when_only_both_sides_have_blocks():
    scan = pickup_vision.PurplePrescanTracker(
        frame_budget=10, confirm_frames=3, center_left=0.35, center_right=0.65,
    )

    for frame in range(1, 11):
        result = scan.update(
            (target(100, frame=frame), target(900, frame=frame)),
            frame_width=1000,
        )

    assert result.present is True
    assert result.hint == "left"


def test_prescan_uses_the_only_stable_side():
    scan = pickup_vision.PurplePrescanTracker(
        frame_budget=10, confirm_frames=3, center_left=0.35, center_right=0.65,
    )

    for frame in range(1, 11):
        result = scan.update((target(100, frame=frame),), frame_width=1000)

    assert result.present is True
    assert result.hint == "left"


def test_prescan_accepts_left_seen_once_when_detection_is_intermittent():
    scan = pickup_vision.PurplePrescanTracker(
        frame_budget=10, confirm_frames=3, center_left=0.35, center_right=0.65,
    )

    # The live run saw the left block only intermittently; the two accepted
    # positions are too far apart to form a three-hit track.
    for frame, x in ((1, 100), (6, 250)):
        result = scan.update((target(x, frame=frame),), frame_width=1000)
    for frame in range(3, 11):
        result = scan.update((), frame_width=1000)

    assert result.present is True
    assert result.hint == "left"


def test_prescan_latches_the_first_terminal_decision():
    scan = pickup_vision.PurplePrescanTracker(
        frame_budget=3, confirm_frames=2, center_left=0.35, center_right=0.65,
    )
    for frame in range(1, 4):
        decided = scan.update((target(900, frame=frame),), frame_width=1000)

    later = scan.update((target(500, frame=4),), frame_width=1000)

    assert decided.present is True
    assert decided.hint == "right"
    assert later == decided


def test_prescan_ignores_scattered_noise_and_keeps_stable_left_hint():
    scan = pickup_vision.PurplePrescanTracker(
        frame_budget=3, confirm_frames=3, center_left=0.35, center_right=0.65,
    )

    for frame, noisy_center in enumerate((380, 620, 500), start=1):
        result = scan.update(
            (target(100, frame=frame), target(noisy_center, frame=frame)),
            frame_width=1000,
        )

    assert result.present is True
    assert result.hint == "left"


def left_bound_cm(area):
    """The DEPLOYED left search bound, read rather than pinned.

    These bounds are field-calibrated per area (purple went 20.0 -> 35.0 on
    2026-09-17).  Hard-coding the number here would turn every retune into a test
    failure that says nothing about the controller -- the behaviour under test is
    "the left strafe stops at whatever bound it was given", not the value itself.
    """
    return load_route_v2_config(
        ROOT / "config" / "route_v2.yaml"
    ).vision.pickup_areas[area].search_left.max_distance_cm


def right_bound_cm(area):
    return load_route_v2_config(
        ROOT / "config" / "route_v2.yaml"
    ).vision.pickup_areas[area].search_right.max_distance_cm


def test_purple_no_target_searches_right_then_baseline_then_bounded_left():
    pickup = controller("purple")
    assert pickup.step(now=0, lateral_cm=0, target=None).kind == "strafe_right"
    right_endpoint = -(right_bound_cm("purple") + 1.0)
    assert pickup.step(now=1, lateral_cm=right_endpoint, target=None).kind == "stop"
    assert pickup.step(now=1.1, lateral_cm=right_endpoint, target=None, frame_id=1).kind == "stop"
    assert pickup.step(now=1.2, lateral_cm=right_endpoint, target=None, frame_id=2).kind == "stop"
    assert pickup.step(now=1.3, lateral_cm=right_endpoint, target=None, frame_id=3).kind == "return_baseline"
    assert pickup.step(now=2, lateral_cm=0, target=None).kind == "strafe_left"
    past_the_bound = left_bound_cm("purple") + 1.0
    assert pickup.step(now=3, lateral_cm=past_the_bound, target=None).kind == "stop"
    pickup.step(now=3.1, lateral_cm=past_the_bound, target=None, frame_id=4)
    pickup.step(now=3.2, lateral_cm=past_the_bound, target=None, frame_id=5)
    result = pickup.step(now=3.3, lateral_cm=past_the_bound, target=None, frame_id=6)
    assert result.kind == "no_target"
    assert result.result == "bypass_to_pickup_1"


def test_left_prescan_hint_searches_left_first_then_checks_right():
    pickup = controller("purple", initial_search="left")
    cfg = load_route_v2_config(ROOT / "config" / "route_v2.yaml").vision.pickup_areas["purple"]

    first = pickup.step(now=0, lateral_cm=0, target=None)
    assert first.kind == "strafe_left"
    assert first.speed == cfg.search_speed

    left_bound = cfg.search_left.max_distance_cm + 1
    pickup.step(now=1, lateral_cm=left_bound, target=None)
    for frame in (1, 2, 3):
        result = pickup.step(now=1 + frame / 10, lateral_cm=left_bound,
                             target=None, frame_id=frame)
    assert result.kind == "return_baseline"
    assert result.speed < 0

    result = pickup.step(now=2, lateral_cm=0, target=None)
    assert result.kind == "strafe_right"
    assert result.speed == -cfg.search_speed


def test_persistent_forward_contact_does_not_cancel_left_search():
    pickup = controller("purple", initial_search="left")

    first = pickup.step(now=0, lateral_cm=0, target=None, contact=True)
    continued = pickup.step(now=.1, lateral_cm=1.79, target=None, contact=True)

    assert first.kind == "strafe_left"
    assert continued.kind == "strafe_left"
    assert continued.phase is pickup_vision.PickupPhase.SEARCH_LEFT


def test_return_to_baseline_faults_when_odometry_does_not_change():
    cfg = load_route_v2_config(ROOT / "config" / "route_v2.yaml")
    area = replace(
        cfg.vision.pickup_areas["purple"],
        search_right=replace(
            cfg.vision.pickup_areas["purple"].search_right,
            timeout_s=.5,
        ),
    )
    pickup = PickupVisionController(
        "purple", area, cfg.vision.block_profiles[area.profile],
        frame_size=(640, 480), initial_search="right",
    )
    pickup.step(now=0, lateral_cm=0, target=None)
    right_endpoint = -(area.search_right.max_distance_cm + 1.0)
    pickup.step(now=.1, lateral_cm=right_endpoint, target=None)
    for frame in (1, 2, 3):
        returning = pickup.step(
            now=.1 + frame / 10, lateral_cm=right_endpoint,
            target=None, frame_id=frame,
        )

    assert returning.kind == "return_baseline"
    fault = pickup.step(now=1.0, lateral_cm=right_endpoint, target=None)
    assert fault.kind == "fault"
    assert fault.reason == "return_baseline_guard_exhausted"


def test_search_speed_is_faster_than_alignment_and_candidate_stops_it():
    pickup = controller("purple", initial_search="right")
    cfg = load_route_v2_config(ROOT / "config" / "route_v2.yaml").vision.pickup_areas["purple"]

    searching = pickup.step(now=0, lateral_cm=0, target=None)
    candidate = pickup.step(now=.1, lateral_cm=-1, target=target(500, frame=1))

    assert abs(searching.speed) == cfg.search_speed
    assert cfg.search_speed > abs(cfg.coarse_speed) > abs(cfg.fine_speed)
    assert candidate.kind == "stop"
    assert candidate.reason == "candidate_requires_stopped_confirmation"


def test_orange_search_exhaustion_is_a_fault():
    pickup = controller("orange")
    pickup.step(now=0, lateral_cm=0, target=None)
    right_endpoint = -(right_bound_cm("orange") + 1.0)
    pickup.step(now=1, lateral_cm=right_endpoint, target=None)
    for frame in (1, 2, 3):
        pickup.step(now=1 + frame / 10, lateral_cm=right_endpoint,
                    target=None, frame_id=frame)
    pickup.step(now=2, lateral_cm=0, target=None)
    left_endpoint = left_bound_cm("orange") + 1.0
    pickup.step(now=3, lateral_cm=left_endpoint, target=None)
    for frame in (4, 5):
        pickup.step(now=3 + frame / 10, lateral_cm=left_endpoint,
                    target=None, frame_id=frame)
    result = pickup.step(now=3.6, lateral_cm=left_endpoint, target=None, frame_id=6)
    assert result.kind == "no_target"
    assert result.result == "orange_exhausted"


def test_candidate_stops_before_confirmation_then_uses_two_alignment_speeds():
    pickup = controller("purple")
    assert pickup.step(now=0, lateral_cm=0, target=target(180, frame=1)).kind == "stop"
    assert pickup.step(now=.1, lateral_cm=0, target=target(182, frame=2)).kind == "stop"
    assert pickup.step(now=.2, lateral_cm=0, target=target(181, frame=3)).kind == "stop"

    coarse = pickup.step(now=.3, lateral_cm=0, target=target(180, frame=4))
    fine = pickup.step(now=.4, lateral_cm=1, target=target(260, frame=5))
    assert coarse.kind == "align_left"
    assert abs(coarse.speed) > abs(fine.speed)
    assert fine.kind == "align_left"


def test_capture_window_requires_three_stopped_new_frames():
    pickup = controller("purple")
    for frame in (1, 2, 3):
        pickup.step(now=frame / 10, lateral_cm=0, target=target(323, y=253, frame=frame))

    assert pickup.step(now=.4, lateral_cm=0, target=target(323, y=253, frame=4), stopped=True).kind == "stop"
    assert pickup.step(now=.5, lateral_cm=0, target=target(323, y=253, frame=4), stopped=True).kind == "stop"
    assert pickup.step(now=.6, lateral_cm=0, target=target(323, y=253, frame=5), stopped=True).kind == "stop"
    ready = pickup.step(now=.7, lateral_cm=0, target=target(323, y=253, frame=6), stopped=True)
    assert ready.kind == "pickup_ready"


def test_aligned_x_with_depth_outside_window_faults_without_more_strafing():
    pickup = controller("purple")
    for frame in (1, 2, 3):
        pickup.step(
            now=frame / 10,
            lateral_cm=0,
            target=target(323, y=310, frame=frame),
        )

    result = pickup.step(
        now=.4,
        lateral_cm=0,
        target=target(323, y=310, frame=4),
    )

    assert result.kind == "fault"
    assert result.speed == 0
    assert result.reason == "pickup_depth_out_of_range"
    assert result.result == "pickup_depth_out_of_range"


def test_depth_leaving_window_during_verification_faults_immediately():
    pickup = controller("purple")
    for frame in (1, 2, 3):
        pickup.step(
            now=frame / 10,
            lateral_cm=0,
            target=target(323, y=253, frame=frame),
        )
    assert pickup.step(
        now=.4,
        lateral_cm=0,
        target=target(323, y=253, frame=4),
    ).kind == "stop"

    result = pickup.step(
        now=.5,
        lateral_cm=0,
        target=target(323, y=310, frame=5),
    )

    assert result.kind == "fault"
    assert result.speed == 0
    assert result.reason == "pickup_depth_out_of_range"
    assert result.result == "pickup_depth_out_of_range"


def test_candidate_selection_prefers_capture_window_and_locked_identity():
    pickup = controller("purple")
    near = target(330, frame=1)
    far_large = BlockObservation(
        color=BlockColor.PURPLE, bounding_box=(470, 240, 120, 120),
        center_px=(530.0, 300.0), area=14400, confidence=.95, frame_index=1,
    )
    assert pickup.select_target((far_large, near)) is near
    pickup.step(now=0, lateral_cm=0, target=near)
    assert pickup.select_target((far_large, target(332, frame=2))).center_px[0] == 332


def test_alignment_distance_guard_looks_at_the_other_side_instead_of_faulting():
    """A candidate the car cannot REACH is not a dead end.

    Operator, 2026-09-24: 「明明有一点 但是左移不过去 直接右移找下一个」 -- there is
    a weak or partly-occluded candidate on one side, the alignment guard runs out
    trying to get to it, and the pickup must go and look for the next block on the
    OTHER side rather than ending the run.

    The guard still bounds the attempt: the car does not chase it forever.
    """
    cfg = load_route_v2_config(ROOT / "config" / "route_v2.yaml")
    area = replace(cfg.vision.pickup_areas["purple"], alignment_max_distance_cm=1,
                   search_right=MotionGuard(5.0, 60.0))
    pickup = PickupVisionController(
        "purple", area, cfg.vision.block_profiles[area.profile], frame_size=(640, 480),
        initial_search="left",
    )
    for frame in (1, 2, 3):
        pickup.step(now=frame / 10, lateral_cm=0, target=target(180, frame=frame))

    # Past the 1 cm alignment guard: the candidate is unreachable from here.
    result = pickup.step(now=.4, lateral_cm=2, target=target(180, frame=4))

    assert result.kind == "strafe_right", "the left candidate failed, so go right"
    assert pickup.phase is PickupPhase.SEARCH_RIGHT

    # And it switches only once: a second unreachable candidate ends the pickup as
    # a normal no_target instead of handing the car another fresh sweep.
    for frame in (5, 6, 7):
        pickup.step(now=0.5 + frame / 10, lateral_cm=2,
                    target=target(380, frame=frame))
    second = pickup.step(now=1.4, lateral_cm=4, target=target(380, frame=8))
    assert second.kind == "no_target"
    assert pickup.phase is PickupPhase.NO_TARGET


def test_locked_target_loss_restarts_the_search_instead_of_faulting():
    """A block that is GONE is not a dead end -- the car goes looking for another.

    Operator, 2026-09-24: 「方块消失了 就应该去搜寻别的方块 而不是停止」.

    Field case, run 20260924_215333: the orange pickup locked a block, the detector
    stopped reporting it while the car stood still, and 2.0 s later the whole route
    went to FAULT with the car parked in front of nothing.

    The short re-acquire window is kept -- a dropped frame or two should not
    abandon a good lock -- but its expiry now resumes the sweep instead of ending
    the run.
    """
    cfg = load_route_v2_config(ROOT / "config" / "route_v2.yaml")
    area = replace(cfg.vision.pickup_areas["purple"], locked_reacquire_timeout_s=.5,
                   search_left=MotionGuard(5.0, 60.0))
    pickup = PickupVisionController(
        "purple", area, cfg.vision.block_profiles[area.profile], frame_size=(640, 480),
        initial_search="left",
    )
    for frame in (1, 2, 3):
        pickup.step(now=frame / 10, lateral_cm=0, target=target(180, frame=frame))

    # Inside the window it holds still and keeps hoping.
    assert pickup.step(now=1, lateral_cm=0, target=None).kind == "stop"

    # Past it, it sweeps again -- and from the state it was in, not from FAULT.
    resumed = pickup.step(now=1.6, lateral_cm=0, target=None)
    assert resumed.kind == "strafe_left"
    assert pickup.phase is PickupPhase.SEARCH_LEFT


def test_return_controller_uses_saved_absolute_baseline_then_bounded_reacquire():
    returning = PickupReturnController(
        baseline_cm=12, tolerance_cm=2, speed=12, seek_max_cm=10, timeout_s=5,
        confirm_frames=2,
    )
    assert returning.step(now=0, absolute_lateral_cm=30, line_found=False).speed < 0
    assert returning.step(now=1, absolute_lateral_cm=13, line_found=False).kind == "seek_line"
    assert returning.step(now=2, absolute_lateral_cm=18, line_found=True).kind == "seek_line"
    assert returning.step(now=2.1, absolute_lateral_cm=19, line_found=True).kind == "return_line_done"


def test_orange_pickup_looks_left_first_and_that_ends_the_search_order():
    """Operator, 2026-09-24: 「现在改成优先往左边找 左边没有 回到正中 往右边找
    并且之后的搜寻只往右边找」.

    The controller has exactly two sweeps and they are opposites, so asking for
    LEFT first is what makes the right sweep the LAST one.
    """
    cfg = load_route_v2_config(ROOT / "config" / "route_v2.yaml")
    area = cfg.vision.pickup_areas["orange"]
    pickup = PickupVisionController(
        "orange", area, cfg.vision.block_profiles[area.profile],
        frame_size=(1280, 720), initial_search="left",
    )

    assert pickup._first_search is PickupPhase.SEARCH_LEFT
    assert pickup._second_search is PickupPhase.SEARCH_RIGHT
    assert pickup.step(now=0.0, lateral_cm=0.0, target=None).kind == "strafe_left"


def test_return_baseline_survives_the_sweep_overshoot_that_used_to_fault_it():
    """Run 20260924_213236: the right sweep reached 77.49 cm against its 75 cm
    guard, and the return to the baseline -- bounded by that SAME guard -- faulted
    2.5 cm short of it.  The car stopped mid-return and never searched the other
    side, which is the operator's 「回到正中 停止 没有往左边找」.
    """
    cfg = load_route_v2_config(ROOT / "config" / "route_v2.yaml")
    area = replace(cfg.vision.pickup_areas["orange"],
                   search_right=MotionGuard(5.0, 60.0),
                   return_guard_margin_cm=2.0)
    pickup = PickupVisionController(
        "orange", area, cfg.vision.block_profiles[area.profile],
        frame_size=(1280, 720), initial_search="right",
    )
    pickup._baseline_cm = 0.0
    # The return starts 7.5 cm out -- past the 5 cm sweep guard, which is the shape
    # of the fault: a sweep always stops a little beyond its own bound.
    pickup._enter_motion_phase(PickupPhase.RETURN_BASELINE, 0.0, 7.5)

    # 5 cm of travel back would have tripped the sweep's own guard.  It must not.
    assert pickup.step(now=1.0, lateral_cm=2.5, target=None).kind == "return_baseline"

    # Past the distance it has to cover plus the margin it still gives up, so the
    # guard has not been made meaningless.  -3.0 is 10.5 cm of travel from the
    # return's origin (budget 7.5 + 2.0) and 3 cm outside the baseline tolerance,
    # so it cannot be satisfied by arriving instead.
    assert pickup.step(now=2.0, lateral_cm=-3.0, target=None).kind == "fault"


def test_return_controller_one_way_hunts_one_way_until_its_own_bound():
    """Operator, 2026-09-24: the orange hunt is ONE-WAY now -- its direction is
    the opposite of the last lateral command the pickup issued, so the car goes
    back the way it came instead of sweeping both ways and guessing.  Positive
    strafes LEFT, so a -1 direction must drive RIGHT at the return speed."""
    returning = PickupReturnController(
        baseline_cm=0, tolerance_cm=1, speed=30, seek_max_cm=75, timeout_s=45,
        confirm_frames=2, one_way_direction=-1,
        swing_cm=(10.0, 20.0, 30.0),
    )
    first = returning.step(now=0, absolute_lateral_cm=0, line_found=False)
    assert first.kind == "seek_line"
    assert first.speed == -30

    # It must never turn around, however far it gets: reversing is exactly what
    # the swing did, and what left the car 110 cm out on run 20260924_205505.
    for step in range(1, 20):
        again = returning.step(now=step * 0.1, absolute_lateral_cm=-step * 3.0,
                               line_found=False)
        assert again.kind == "seek_line"
        assert again.speed < 0

    # The bound is the area's own measured safe strafe distance, in that direction.
    exhausted = returning.step(now=20, absolute_lateral_cm=-75.0, line_found=False)
    assert exhausted.kind == "fault"
    assert exhausted.reason == "one_way_return_line_exhausted"


def test_return_controller_faults_after_both_bounded_sweeps():
    returning = PickupReturnController(
        baseline_cm=0, tolerance_cm=1, speed=10, seek_max_cm=5, timeout_s=10,
        confirm_frames=2,
    )
    returning.step(now=0, absolute_lateral_cm=0, line_found=False)
    assert returning.step(now=1, absolute_lateral_cm=6, line_found=False).speed < 0
    assert returning.step(now=2, absolute_lateral_cm=-6, line_found=False).kind == "fault"