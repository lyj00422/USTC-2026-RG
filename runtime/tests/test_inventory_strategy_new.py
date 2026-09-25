from route_v2.loop_strategy import (
    LoopContext,
    build_plan_for_inventory,
    next_route_after_build,
    orange_capacity,
)


def test_capacity_is_three_total_and_purple_uses_one_slot():
    assert orange_capacity(purple_count=1, orange_count=0) == 2
    assert orange_capacity(purple_count=0, orange_count=1) == 2
    assert orange_capacity(purple_count=1, orange_count=2) == 0


def test_purple_inventory_uses_two_orange_base_then_purple_top_plan():
    context = LoopContext(purple_count=1, orange_count=2)

    assert build_plan_for_inventory(context) == ("BUILD_BASE",)


def test_orange_only_inventory_uses_count_based_build_plan():
    assert build_plan_for_inventory(LoopContext(orange_count=3)) == ("BUILD_3",)
    assert build_plan_for_inventory(LoopContext(orange_count=2)) == ("BUILD_2",)
    assert build_plan_for_inventory(LoopContext(orange_count=1)) == ("PLACE_ORANGE",)


def test_purple_inventory_skips_j3_and_empty_purple_inventory_visits_j3():
    assert next_route_after_build(LoopContext(purple_count=1)) == "DIRECT_ORANGE_D330"
    assert next_route_after_build(LoopContext()) == "J3_PURPLE_CHECK"


def test_build_consumption_clears_explicitly_consumed_inventory():
    context = LoopContext(purple_count=1, orange_count=2)
    context.consume(orange=2, purple=1)
    assert context.purple_count == 0
    assert context.orange_count == 0
