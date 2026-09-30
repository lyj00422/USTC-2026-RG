from route_v2.config import RouteV2Config
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


def _build_area_machine():
    machine = RouteV2StateMachine(RouteV2Config())
    machine._enter(RouteState.BUILD_AREA, 0.0)
    return machine


def _not_the_first_visit(machine):
    """Mark the first arrival as spent.

    Since 2026-09-29 the run's FIRST arrival at the build area builds where it
    stands and never slides (operator: 「第一次搭建 到了搭建区直接执行搭建动作」).
    Every test below is about what a LATER arrival does -- getting past buildings
    that are already standing -- so it has to say so.  Without this the first
    assertion in each of them sees BUILD_ACTION, because that is now the correct
    answer for a first visit.
    """
    machine._build_first_visit_done = True
    return machine


def test_build_area_first_visit_builds_where_it_stands():
    """Operator, 2026-09-29: 「第一次搭建 到了搭建区直接执行搭建动作」.

    There is nothing standing to line up with on the first arrival, so the slide
    has nothing to find -- and sliding would carry the car off the pose the 330 cm
    leg was tuned to leave it in.  A blob in view must NOT change that: on this
    visit there is no building to get past, whatever the detector reports.
    """
    machine = _build_area_machine()
    machine.loop_context.record_purple()
    machine.loop_context.record_orange()
    machine.loop_context.record_orange()
    occupied = VisionRouteInput(build_block_visible=True, build_center_error=-0.4)

    # The entry tick is still held -- it carries the 330 cm leg's readings.
    assert machine.step(0.0, vision=occupied).kind == "wait"

    committed = machine.step(0.1, vision=occupied, absolute_lateral_cm=0.0)

    assert committed.state is RouteState.BUILD_ACTION, "must build, not slide"
    assert machine._build_first_visit_done is True

    # NOT asserted: `_build_visit_origin_cm`.  The origin IS captured first (the
    # code above the shortcut), but `_enter(BUILD_ACTION)` clears it in the same
    # tick -- and that is true of the existing slide path too, not something this
    # change introduces.  The upshot is that the origin has never actually been
    # available to BUILD_BACK_TO_LINE; its line hunt has always run on
    # `_line_reference_cm`.  Left as found: making the fallback real changes where
    # the hunt starts, which is a field question, not a test one.


def test_build_area_with_nothing_to_build_goes_back_for_blocks():
    """An empty plan is a pickup round that came back empty, not a build.

    Field, run 20260929_203524: BUILD_AREA committed anyway, `run_route_v2` then
    substituted the string "RESET" for the missing action name, and when the reset
    finished `apply_build_action` raised `unknown build action: RESET` -- faulting
    the run after 77 states with nothing printed to its own stdout.
    """
    machine = _build_area_machine()          # nothing on board at all
    away = VisionRouteInput(build_block_visible=False)

    machine.step(0.0, vision=away)
    going_back = machine.step(0.1, vision=away, absolute_lateral_cm=0.0)

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
    away = VisionRouteInput(build_block_visible=False)

    machine.step(0.0, vision=away)
    done = machine.step(0.1, vision=away, absolute_lateral_cm=0.0)

    assert done.state is RouteState.FINISHED


def test_build_area_first_visit_is_taken_only_once():
    """The latch is the run's, not the visit's: leaving and coming back must take
    the normal sliding path again."""
    machine = _build_area_machine()
    machine.loop_context.record_orange()
    machine.loop_context.record_orange()
    machine.step(0.1, vision=VisionRouteInput(build_block_visible=False),
                 absolute_lateral_cm=0.0)
    assert machine.state is RouteState.BUILD_ACTION

    machine._enter(RouteState.BUILD_AREA, 1.0)
    machine.step(1.1, vision=VisionRouteInput(build_block_visible=False),
                 absolute_lateral_cm=0.0)

    assert machine.state is RouteState.BUILD_AREA, (
        "the second visit must plan against what is standing, not build blind")


def test_build_area_entry_tick_holds_before_trusting_its_inputs():
    """The tick BUILD_AREA is entered is a fall-through from the 330 cm leg, so
    both inputs on it belong to the leg: the lateral odometer still reads the
    leg's (330, measured) and the vision reading was sampled for the leg's task.
    Acting on either one made the slide's ceiling fire on its second tick --
    origin captured at 330, then re-baselined to 0, |0 - 330| >= 140."""
    machine = _build_area_machine()

    entry = machine.step(0.0, vision=VisionRouteInput(build_block_visible=True))

    assert entry.state is RouteState.BUILD_AREA
    assert entry.kind == "wait"


def _cap_machine():
    """One purple and one orange on board: the planner then wants PLACE_PURPLE,
    which is a CAP -- that is what tells BUILD_AREA to centre on a blob."""
    machine = RouteV2StateMachine(RouteV2Config())
    machine.loop_context.record_purple()
    machine.loop_context.record_orange()
    machine._enter(RouteState.BUILD_AREA, 0.0)
    return _not_the_first_visit(machine)


def _place_machine():
    """Two orange and no purple: the planner wants BUILD_2, a PLACEMENT, so the
    car has to get past every building before it acts."""
    machine = RouteV2StateMachine(RouteV2Config())
    machine.loop_context.record_orange()
    machine.loop_context.record_orange()
    machine._enter(RouteState.BUILD_AREA, 0.0)
    return _not_the_first_visit(machine)


def test_build_area_cap_centres_on_the_blob_and_does_not_slide():
    """Operator, 2026-09-24: 「有紫色才要封顶」and 「你应该用视觉找到方块正中 然后
    执行动作封顶」.  Nothing to get past here (cap_count 0), so the car must NOT
    move: it centres and commits."""
    machine = _cap_machine()
    centred = VisionRouteInput(build_block_visible=True, build_center_error=0.0)

    first = machine.step(0.1, vision=centred, absolute_lateral_cm=0.0)
    second = machine.step(0.2, vision=centred, absolute_lateral_cm=0.0)

    assert first.kind == "wait", "confirming, not sliding"
    assert second.state is RouteState.BUILD_ACTION


def test_build_area_cap_strafes_toward_a_blob_that_is_off_centre():
    """The centring move itself: a blob left of centre drives the car LEFT, which
    is POSITIVE vy -- the same sign convention the pickup's alignment uses."""
    machine = _cap_machine()
    left = VisionRouteInput(build_block_visible=True, build_center_error=-0.20)
    right = VisionRouteInput(build_block_visible=True, build_center_error=+0.20)

    toward_left = machine.step(0.1, vision=left, absolute_lateral_cm=0.0)
    assert toward_left.kind == "strafe" and toward_left.speed > 0

    machine._build_align_frames = 0
    toward_right = machine.step(0.2, vision=right, absolute_lateral_cm=0.5)
    assert toward_right.kind == "strafe" and toward_right.speed < 0


def test_build_area_cap_skips_only_the_finished_buildings():
    """「有紫色才要封顶 跳过已经封顶的」: with cap_count 1 the car gets past one
    blob (the finished building), then centres on the NEXT one -- the stack that
    is still waiting for its cap."""
    machine = _cap_machine()
    machine.loop_context.cap_count = 1
    away = VisionRouteInput(build_block_visible=False)

    first = machine.step(0.1, vision=VisionRouteInput(build_block_visible=True),
                         absolute_lateral_cm=0.0)
    assert first.kind == "strafe" and first.speed < 0, "must slide right past it"

    # The blob leaves the view (3 confirmed frames) = one building passed.
    for index in range(3):
        machine.step(0.2 + index * 0.1, vision=away,
                     absolute_lateral_cm=5.0 + index)

    centred = VisionRouteInput(build_block_visible=True, build_center_error=0.0)
    machine.step(0.6, vision=centred, absolute_lateral_cm=9.0)
    committed = machine.step(0.7, vision=centred, absolute_lateral_cm=9.0)

    assert committed.state is RouteState.BUILD_ACTION


def test_build_area_place_skips_every_building():
    """「只有橙色 要跳过所有建筑」: a placement gets past every one of them, so it
    needs building_count blobs passed before it acts."""
    machine = _place_machine()
    machine.loop_context.building_count = 1
    away = VisionRouteInput(build_block_visible=False)

    first = machine.step(0.1, vision=VisionRouteInput(build_block_visible=True),
                         absolute_lateral_cm=0.0)
    assert first.kind == "strafe" and first.speed < 0

    machine.step(0.2, vision=away, absolute_lateral_cm=5.0)
    machine.step(0.3, vision=away, absolute_lateral_cm=6.0)
    machine.step(0.4, vision=away, absolute_lateral_cm=7.0)

    # Clear of the building at 7.0, but the visit is NOT over: operator,
    # 2026-09-29, 「右移到没有方块的地方 长一点 目前两栋建筑之间有点近」.
    committed = machine.step(0.5, vision=away, absolute_lateral_cm=20.0)

    assert committed.state is RouteState.BUILD_ACTION


def test_build_area_place_keeps_going_after_the_view_clears():
    """The gap between two structures is set by build_place_extra_right_cm, not by
    where the detector happened to lose the last blob.

    The car must not act on the tick the view clears, and it must still be sliding
    one centimetre short of the configured distance.
    """
    machine = _place_machine()
    machine.loop_context.building_count = 1
    away = VisionRouteInput(build_block_visible=False)
    extra = RouteV2Config().build_place_extra_right_cm

    machine.step(0.1, vision=VisionRouteInput(build_block_visible=True),
                 absolute_lateral_cm=0.0)
    machine.step(0.2, vision=away, absolute_lateral_cm=5.0)
    machine.step(0.3, vision=away, absolute_lateral_cm=6.0)

    # View clears here: 7.0 is the clear pose, so 7.0 + extra is the commit pose.
    cleared = machine.step(0.4, vision=away, absolute_lateral_cm=7.0)
    assert cleared.state is RouteState.BUILD_AREA, "must not act the instant it clears"
    assert cleared.kind == "strafe" and cleared.speed < 0, "must keep going right"

    short = machine.step(0.5, vision=away, absolute_lateral_cm=7.0 + extra - 1.0)
    assert short.state is RouteState.BUILD_AREA, "one cm short is still short"
    assert short.kind == "strafe" and short.speed < 0

    committed = machine.step(0.6, vision=away, absolute_lateral_cm=7.0 + extra)
    assert committed.state is RouteState.BUILD_ACTION


def test_build_area_place_does_not_move_without_an_odometer():
    """The extra gap is a distance, so it needs an odometer like every other move
    here.  A cleared view with no reading must hold, not slide blind."""
    machine = _place_machine()
    machine.loop_context.building_count = 1
    away = VisionRouteInput(build_block_visible=False)

    machine.step(0.1, vision=VisionRouteInput(build_block_visible=True),
                 absolute_lateral_cm=0.0)
    for index in range(3):
        machine.step(0.2 + index * 0.1, vision=away, absolute_lateral_cm=5.0 + index)

    held = machine.step(0.6, vision=away, absolute_lateral_cm=None)

    assert held.state is RouteState.BUILD_AREA
    assert held.kind == "stop"


def test_build_area_holds_at_the_slide_ceiling_instead_of_acting():
    """Reaching the ceiling means the detector never let the car past.  That is a
    fault to look at on the field, not a place to drop blocks, so the state holds
    -- and it stays held, even if the view clears afterwards."""
    machine = _place_machine()
    machine.loop_context.building_count = 1
    blocked = VisionRouteInput(build_block_visible=True)

    machine.step(0.1, vision=blocked, absolute_lateral_cm=0.0)
    held = machine.step(0.2, vision=blocked, absolute_lateral_cm=-140.0)

    assert held.state is RouteState.BUILD_AREA
    assert held.kind == "stop"

    after = machine.step(0.3, vision=VisionRouteInput(build_block_visible=False),
                         absolute_lateral_cm=-141.0)
    assert after.state is RouteState.BUILD_AREA
    assert after.kind == "stop"


def test_build_area_does_not_slide_without_an_odometer():
    """The absolute lateral projection is what bounds the slide.  A slide bounded
    only by vision is the runaway the ceiling exists to prevent, so no reading
    means no motion."""
    machine = _place_machine()
    machine.loop_context.building_count = 1

    held = machine.step(0.1, vision=VisionRouteInput(build_block_visible=True),
                        absolute_lateral_cm=None)

    assert held.state is RouteState.BUILD_AREA
    assert held.kind == "stop"
