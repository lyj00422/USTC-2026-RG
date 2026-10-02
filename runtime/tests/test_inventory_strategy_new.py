import pytest

from route_v2.loop_strategy import (
    LoopContext,
    all_orange_round,
    apply_build_action,
    build_plan_for_inventory,
    next_purple_slot,
    next_route_after_build,
    orange_capacity,
)


def test_the_purple_slots_are_taken_middle_right_left_by_visit():
    """「第一次中间 第二次右边」 -- the slot is counted, not decided.

    Operator, 2026-10-02: 「这个版本不必要在j3判定有没有紫色了 直接按照逻辑 ...」
    and 「一定有三个紫色」, then later 「紫色抓取 固定成 第一次中间 第二次右边」.
    Slot numbers are the prescan's own: 1 left, 2 centre, 3 right.
    """
    assert [next_purple_slot(v) for v in (1, 2, 3)] == [2, 3, 1]


def test_a_fourth_purple_visit_holds_on_the_last_slot_instead_of_raising():
    """「一定有三个紫色」 is the field promise.  A fourth visit means the earlier
    ones did not land -- not a reason to fault the run."""
    assert next_purple_slot(4) == 1
    assert next_purple_slot(9) == 1


def test_purple_visit_numbers_are_one_based():
    with pytest.raises(ValueError):
        next_purple_slot(0)


def test_capacity_is_three_total_and_purple_uses_one_slot():
    assert orange_capacity(purple_count=1, orange_count=0) == 2
    assert orange_capacity(purple_count=0, orange_count=1) == 2
    assert orange_capacity(purple_count=1, orange_count=2) == 0


def test_purple_inventory_builds_and_caps_the_structure_in_one_visit():
    """One purple and two orange is a WHOLE structure, not half of one.

    Operator, 2026-10-02: 「先用吸盘和右边的搭建两层 再直接使用左边紫色封顶」, and
    on the round it belongs to: 「到了J3 左旋去取物区2 取一个紫色 到取物区1取两个橙色
    到了搭建区 直接搭建三层 重复3次」.  So the base and the cap are ONE visit's plan --
    both packages already exist, and the car never goes back out for a fourth layer.
    """
    context = LoopContext(purple_count=1, orange_count=2)

    assert build_plan_for_inventory(context) == ("BUILD_BASE", "PLACE_PURPLE")


def test_purple_inventory_ignores_a_pending_cap_it_can_finish_in_one_visit():
    """The same plan whether the counters say a cap is pending or not.

    `building_count > cap_count` used to intercept this arrival with
    TOP_SUCTION_ORANGE_PURPLE -- 「搭三四层」, the 4-layer round this change replaces.
    Both counters advance by one either way (BUILD_BASE then PLACE_PURPLE), so a
    completed structure always leaves them equal.
    """
    uncapped = LoopContext(purple_count=1, orange_count=2,
                           building_count=1, cap_count=0)
    assert build_plan_for_inventory(uncapped) == ("BUILD_BASE", "PLACE_PURPLE")

    capped = LoopContext(purple_count=1, orange_count=2,
                         building_count=1, cap_count=1)
    assert build_plan_for_inventory(capped) == ("BUILD_BASE", "PLACE_PURPLE")


def test_finishing_the_one_visit_plan_leaves_both_counters_level():
    """The invariant the next arrival depends on: a run of the plan in one visit
    finishes the structure, so nothing is left waiting for a cap to come back for."""
    context = LoopContext(purple_count=1, orange_count=2)
    plan = build_plan_for_inventory(context)

    for action in plan:
        apply_build_action(context, action)

    assert context.building_count == 1
    assert context.cap_count == 1
    assert context.build_waiting_for_purple is False
    assert context.total_count == 0


def test_orange_only_inventory_uses_count_based_build_plan():
    assert build_plan_for_inventory(LoopContext(orange_count=3)) == ("BUILD_3",)
    assert build_plan_for_inventory(LoopContext(orange_count=2)) == ("BUILD_2",)
    assert build_plan_for_inventory(LoopContext(orange_count=1)) == ("PLACE_ORANGE",)


def test_purple_inventory_skips_j3_and_empty_purple_inventory_visits_j3():
    assert next_route_after_build(LoopContext(purple_count=1)) == "DIRECT_ORANGE_D330"
    assert next_route_after_build(LoopContext()) == "J3_PURPLE_CHECK"


def test_the_third_structure_round_also_skips_j3_and_the_purple_area():
    """「第三次从搭建区去取物 去取3个橙色 然后执行动作直接搭建三层」.

    Confirmed on 2026-10-02 as 「搭第3栋（最后一次）」 -- so this is the round that
    starts with TWO structures standing, not the one after them.
    """
    one_up = LoopContext(building_count=1)
    two_up = LoopContext(building_count=2)
    three_up = LoopContext(building_count=3)

    assert not all_orange_round(one_up)
    assert next_route_after_build(one_up) == "J3_PURPLE_CHECK"
    assert all_orange_round(two_up)
    assert next_route_after_build(two_up) == "DIRECT_ORANGE_D330"
    # Nothing stops the route after the third structure, so a fourth round is
    # fetched the same way rather than reaching for a purple the car never took.
    assert all_orange_round(three_up)


def test_build_consumption_clears_explicitly_consumed_inventory():
    context = LoopContext(purple_count=1, orange_count=2)
    context.consume(orange=2, purple=1)
    assert context.purple_count == 0
    assert context.orange_count == 0
