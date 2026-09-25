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
    return machine


def _place_machine():
    """Two orange and no purple: the planner wants BUILD_2, a PLACEMENT, so the
    car has to get past every building before it acts."""
    machine = RouteV2StateMachine(RouteV2Config())
    machine.loop_context.record_orange()
    machine.loop_context.record_orange()
    machine._enter(RouteState.BUILD_AREA, 0.0)
    return machine


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
    committed = machine.step(0.4, vision=away, absolute_lateral_cm=7.0)

    assert committed.state is RouteState.BUILD_ACTION


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
