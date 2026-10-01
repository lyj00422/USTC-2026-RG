from route_v2.config import RouteV2Config
from route_v2.loop_strategy import LoopContext
from route_v2.state_machine import RouteState, RouteV2StateMachine, VisionRouteInput


def test_build_return_line_follows_to_the_second_area_while_purple_is_carried():
    """This leg used to be a single blind `D 330` and was asserted as one.

    Operator, 2026-09-24: 「搭建区执行完动作 倒车30cm 旋转180后 没有直线巡线pid
    调整走直线」 -- so it line-follows now and ends on the same odometer gate the
    build-area leg uses, not on d_done.  Field-verified on run 20260924_203340
    (193 ticks, intent `v` throughout, line_error on 192 of them)."""
    config = RouteV2Config()
    machine = RouteV2StateMachine(config)
    machine.loop_context.record_purple()
    machine._enter(RouteState.BUILD_TURN_LEFT, 0.0)

    turn = machine.step(0.1)
    assert turn.rotate_deg == 180
    following = machine.step(0.2, d_done=True)
    assert following.state is RouteState.DIRECT_ORANGE_D330
    assert following.kind == "v"

    gate = config.direct_orange_distance_cm
    short = machine.step(0.3, travel_cm=gate - 1.0)
    assert short.state is RouteState.DIRECT_ORANGE_D330
    assert short.kind == "v"

    machine.step(0.4, travel_cm=gate)
    assert machine.state is not RouteState.DIRECT_ORANGE_D330


def test_build_return_routes_to_j3_without_purple():
    machine = RouteV2StateMachine(RouteV2Config())
    machine._enter(RouteState.BUILD_TURN_LEFT, 0.0)
    machine.step(0.1)

    result = machine.step(0.2, d_done=True)
    assert result.state is RouteState.JUNCTION_2_TO_JUNCTION_3


def test_orange_success_returns_directly_to_search_or_return_without_d20():
    machine = RouteV2StateMachine(RouteV2Config())
    machine._enter(RouteState.PICKUP_2_VISION_ONLY, 0.0)

    result = machine.step(0.1, vision=VisionRouteInput(action_done=True))
    assert result.state is RouteState.PICKUP_2_VISION_ONLY
    assert result.kind == "stop"
    assert not hasattr(RouteState, "PICKUP_2_ORANGE_PRESS")


def test_supply_exhaustion_is_the_only_empty_inventory_finish():
    machine = RouteV2StateMachine(RouteV2Config())
    machine.loop_context.purple_absent = True
    machine._enter(RouteState.PICKUP_2_VISION_ONLY, 0.0)

    stopped = machine.step(0.1, vision=VisionRouteInput(pickup_kind="no_target"))
    assert stopped.state is RouteState.FINISHED

    still_running = RouteV2StateMachine(RouteV2Config())
    still_running._enter(RouteState.PICKUP_2_VISION_ONLY, 0.0)
    still_running.step(0.1, vision=VisionRouteInput(pickup_kind="no_target"))
    assert still_running.state is RouteState.PICKUP_2_RETURN_TO_LINE


# ---------------------------------------------------------------------------
# BUILD_AREA positions itself BY ODOMETER since 2026-10-01.
#
# The N-th build position -- the N-th structure for a PLACEMENT, the N-th stack
# for a CAP -- is `build_right_step_cm * N` to the RIGHT of the pose the visit
# arrived at, where N is `skip + 1` and `skip` is the counter the strategy already
# keeps (building_count for a placement, cap_count for a cap).  Operator:
# 「把视觉替换成距离 策略不变 但是搭建的时候不用避开方块什么的 第一栋就到搭建区右移
# 5cm 第二栋右移10cm 第三栋15cm ... 要封顶第一层就到了搭建区右移5cm 要封顶第二层就
# 右移10cm」, and on the extra gap the vision path used to add: 「就是 5×N，不加」.
#
# Every test here drives the state through `_visit`, which passes NO vision.  That
# is the point: `VisionRouteInput()` leaves build_block_visible None, and the state
# used to hold forever on exactly that, so a visit that still consulted the camera
# could not satisfy any assertion below.  This file is the regression test for the
# removal, and the vision-driven tests it replaces are gone rather than adapted.
# ---------------------------------------------------------------------------


# The pitch is CONFIG, not a constant in this file: `build_right_step_cm` is the
# one number the field tunes (它是「间距就是这个数」的那个数), so hard-coding it here
# would make every pitch change a test rewrite instead of a config change.
FIRST = RouteV2Config().build_first_right_cm   # 第 1 栋距到达位姿多远（起手）
PITCH = RouteV2Config().build_right_step_cm    # 相邻两栋的间隔（步进）


def _build_area_machine():
    machine = RouteV2StateMachine(RouteV2Config())
    machine._enter(RouteState.BUILD_AREA, 0.0)
    return machine


def _place_machine():
    """Two orange and no purple: the planner wants BUILD_2, a PLACEMENT."""
    machine = RouteV2StateMachine(RouteV2Config())
    machine.loop_context.record_orange()
    machine.loop_context.record_orange()
    machine._enter(RouteState.BUILD_AREA, 0.0)
    return machine


def _cap_machine(*, building_count, cap_count):
    """One purple and one orange on board, so the planner wants a CAP.

    `build_plan_for_inventory` reaches its capping branch because more structures
    stand than are capped, and the orange sitting in the RIGHT slot makes it
    TOP_RIGHT_ORANGE_PURPLE -- a member of CAP_ACTIONS, which is what tells
    BUILD_AREA this visit caps rather than places, and so which counter feeds
    `skip`.
    """
    machine = RouteV2StateMachine(RouteV2Config())
    machine.loop_context = LoopContext(purple_count=1, orange_count=1,
                                       building_count=building_count,
                                       cap_count=cap_count)
    machine._enter(RouteState.BUILD_AREA, 0.0)
    return machine


def _visit(machine, *, now, lateral_cm):
    """One BUILD_AREA tick, with no vision argument at all -- see the note above."""
    return machine.step(now, absolute_lateral_cm=lateral_cm)


def test_build_area_entry_tick_holds_before_trusting_its_inputs():
    """The tick BUILD_AREA is entered is a fall-through from the 330 cm leg, so on
    it the odometer still carries the leg's reading.  Acting on that made the old
    slide's ceiling fire on its second tick (origin captured at 330, then
    re-baselined to 0, so |0 - 330| >= 140)."""
    machine = _build_area_machine()

    entry = _visit(machine, now=0.0, lateral_cm=330.0)

    assert entry.state is RouteState.BUILD_AREA
    assert entry.kind == "wait"
    assert machine._build_visit_origin_cm is None, "nothing latched on the entry tick"


def test_build_area_first_structure_goes_one_pitch_right():
    """「第一栋就到搭建区右移5cm」.

    Right is NEGATIVE vy (positive strafes LEFT), the same convention every other
    lateral move on this route uses.
    """
    machine = _place_machine()
    _visit(machine, now=0.0, lateral_cm=0.0)

    moving = _visit(machine, now=0.1, lateral_cm=0.0)
    assert moving.state is RouteState.BUILD_AREA
    assert moving.kind == "strafe" and moving.speed < 0, "must strafe right"
    assert machine._build_target_cm == FIRST

    short = _visit(machine, now=0.2, lateral_cm=-(FIRST - 0.01))
    assert short.kind == "strafe", "one hundredth short is still short"

    committed = _visit(machine, now=0.3, lateral_cm=-FIRST)
    assert committed.state is RouteState.BUILD_ACTION


def test_build_area_second_structure_goes_two_pitches_right():
    """「第二栋右移10cm」: one structure already standing, so this one goes to
    position 2 -- 2 x build_right_step_cm from the arrival pose, not 5 + 5 from
    wherever the last visit happened to stop."""
    machine = _place_machine()
    machine.loop_context.building_count = 1
    _visit(machine, now=0.0, lateral_cm=0.0)

    moving = _visit(machine, now=0.1, lateral_cm=0.0)
    assert moving.kind == "strafe" and moving.speed < 0
    assert machine._build_target_cm == FIRST + PITCH

    short = _visit(machine, now=0.2, lateral_cm=-(FIRST + PITCH - 0.01))
    assert short.kind == "strafe"

    committed = _visit(machine, now=0.3, lateral_cm=-(FIRST + PITCH))
    assert committed.state is RouteState.BUILD_ACTION


def test_build_area_third_structure_goes_three_pitches_right():
    """「第三栋15cm」 -- the top of the range the strategy can ask for, and still an
    order of magnitude inside build_slide_max_cm."""
    machine = _place_machine()
    machine.loop_context.building_count = 2
    _visit(machine, now=0.0, lateral_cm=0.0)

    assert _visit(machine, now=0.1, lateral_cm=0.0).kind == "strafe"
    assert machine._build_target_cm == FIRST + 2 * PITCH
    assert _visit(machine, now=0.2, lateral_cm=-(FIRST + 2 * PITCH - 1.0)).kind == "strafe"
    assert _visit(machine, now=0.3, lateral_cm=-(FIRST + 2 * PITCH)).state is RouteState.BUILD_ACTION


def test_build_area_cap_goes_to_the_same_position_as_the_stack_under_it():
    """「要封顶第一层就到了搭建区右移5cm 要封顶第二层就右移10cm」.

    A cap reads `cap_count` where a placement reads `building_count`, and both feed
    the same `step * (skip + 1)` -- which is what puts the cap back on the stack it
    belongs to with no vision at all.  First stack (nothing capped yet) is 5 cm; the
    second is 10 cm, because 「跳过已经封顶的」.
    """
    first = _cap_machine(building_count=1, cap_count=0)
    _visit(first, now=0.0, lateral_cm=0.0)
    assert _visit(first, now=0.1, lateral_cm=0.0).kind == "strafe"
    assert first._build_target_cm == FIRST
    assert _visit(first, now=0.2, lateral_cm=-FIRST).state is RouteState.BUILD_ACTION
    assert first.loop_context.build_plan == ("TOP_RIGHT_ORANGE_PURPLE",)

    second = _cap_machine(building_count=2, cap_count=1)
    _visit(second, now=0.0, lateral_cm=0.0)
    assert _visit(second, now=0.1, lateral_cm=0.0).kind == "strafe"
    assert second._build_target_cm == FIRST + PITCH
    assert _visit(second, now=0.2, lateral_cm=-(FIRST + PITCH - 1.0)).kind == "strafe"
    assert _visit(second, now=0.3, lateral_cm=-(FIRST + PITCH)).state is RouteState.BUILD_ACTION


def test_build_area_first_position_is_one_pitch_for_any_plan():
    """A fresh process has an empty memory, so its first structure -- whatever the
    plan is -- goes to position 1.  The old `_build_first_right_cm` special case
    existed for exactly this; the general formula covers it with no special case,
    which is why that key is no longer read."""
    one = RouteV2StateMachine(RouteV2Config())
    one.loop_context.record_orange()
    one._enter(RouteState.BUILD_AREA, 0.0)
    _visit(one, now=0.0, lateral_cm=0.0)
    assert _visit(one, now=0.1, lateral_cm=0.0).kind == "strafe"
    assert one._build_target_cm == FIRST
    assert _visit(one, now=0.2, lateral_cm=-FIRST).state is RouteState.BUILD_ACTION

    three = RouteV2StateMachine(RouteV2Config())
    three.loop_context.record_orange()
    three.loop_context.record_orange()
    three.loop_context.record_orange()
    three._enter(RouteState.BUILD_AREA, 0.0)
    _visit(three, now=0.0, lateral_cm=0.0)
    assert _visit(three, now=0.1, lateral_cm=0.0).kind == "strafe"
    assert three._build_target_cm == FIRST
    assert _visit(three, now=0.2, lateral_cm=-FIRST).state is RouteState.BUILD_ACTION


def test_build_area_with_nothing_to_build_goes_back_for_blocks():
    """An empty plan is a pickup round that came back empty, not a build.

    It is decided BEFORE any move, so the visit leaves the car exactly where the
    330 cm leg put it.  Field, run 20260929_203524: BUILD_AREA committed anyway,
    `run_route_v2` then substituted the string "RESET" for the missing action name,
    and when the reset finished `apply_build_action` raised `unknown build action:
    RESET` -- faulting the run after 77 states with nothing printed to its stdout.
    """
    machine = _build_area_machine()          # nothing on board at all

    _visit(machine, now=0.0, lateral_cm=0.0)
    going_back = _visit(machine, now=0.1, lateral_cm=0.0)

    assert going_back.state is RouteState.BUILD_BACK_TO_LINE, (
        "nothing to build with means fetch, not build")
    assert going_back.kind == "wait"


def test_build_area_with_nothing_to_build_and_nothing_left_finishes():
    """The same terminal test BUILD_ACTION's completion uses: nothing out there
    AND nothing on board means the job is done.  Going back to hunt for blocks
    that do not exist would loop forever."""
    machine = _build_area_machine()
    machine.loop_context.purple_absent = True
    machine.loop_context.orange_absent = True

    _visit(machine, now=0.0, lateral_cm=0.0)
    done = _visit(machine, now=0.1, lateral_cm=0.0)

    assert done.state is RouteState.FINISHED


def test_build_area_does_not_move_without_an_odometer():
    """The position is a distance, so it needs an odometer.  A plan with no reading
    must hold, not slide blind."""
    machine = _place_machine()

    held = _visit(machine, now=0.1, lateral_cm=None)

    assert held.state is RouteState.BUILD_AREA
    assert held.kind == "stop"


def test_build_area_holds_at_the_slide_ceiling_instead_of_acting():
    """A reading far outside every possible target means the odometer is not
    describing this car's motion.  That is a fault to look at on the field, not a
    place to drop blocks: the state holds, and it STAYS held even once the reading
    comes back to something that looks like a valid target.

    The guard has to sit before the commit as well as inside the slide, because the
    car commits as soon as it is PAST its target -- one bogus jump past the ceiling
    would otherwise land straight in a build.
    """
    machine = _place_machine()
    _visit(machine, now=0.0, lateral_cm=0.0)
    assert _visit(machine, now=0.1, lateral_cm=0.0).kind == "strafe"

    held = _visit(machine, now=0.2, lateral_cm=-300.0)

    assert held.state is RouteState.BUILD_AREA
    assert held.kind == "stop"
    assert machine._build_slide_exhausted is True

    after = _visit(machine, now=0.3, lateral_cm=-5.0)
    assert after.state is RouteState.BUILD_AREA
    assert after.kind == "stop", "a latched ceiling never turns into a build"


def test_build_area_re_arms_its_ceiling_on_the_next_visit():
    """The latch is the VISIT's, not the run's: a new arrival must be able to move
    again, or one bad reading would strand the car for the rest of the run."""
    machine = _place_machine()
    _visit(machine, now=0.0, lateral_cm=0.0)          # entry tick: the origin is
    _visit(machine, now=0.1, lateral_cm=0.0)          # latched on the one AFTER it
    _visit(machine, now=0.2, lateral_cm=-300.0)
    assert machine._build_slide_exhausted is True

    machine._enter(RouteState.BUILD_AREA, 1.0)
    _visit(machine, now=1.0, lateral_cm=500.0)
    moving = _visit(machine, now=1.1, lateral_cm=500.0)

    assert machine._build_slide_exhausted is False
    assert moving.kind == "strafe", "the next visit positions itself normally"
