"""Inventory-driven pickup and build rules for the route loop."""

from __future__ import annotations

from dataclasses import dataclass


def choose_purple_slot(slots: tuple[bool, bool, bool]) -> int | None:
    if len(slots) != 3:
        raise ValueError("purple slots must contain exactly three booleans")
    present = tuple(index + 1 for index, exists in enumerate(slots) if exists)
    if not present:
        return None
    if 2 in present and len(present) >= 2:
        return 2
    return 3 if 3 in present else present[0]


@dataclass
class LoopContext:
    """Current physical slots: left, right, and the suction cup."""

    purple_count: int = 0
    orange_count: int = 0
    left_slot: str | None = None
    right_slot: str | None = None
    suction_slot: str | None = None
    build_waiting_for_purple: bool = False
    initial_build_done: bool = False
    # Two counts, because "get past the buildings" means different things for the
    # two things the car can do here.  Both stand to the left of the free space and
    # the car arrives from the left, so both are "how many orange blobs to pass".
    #
    #   building_count -- every structure standing.  A PLACEMENT must get past all
    #     of them to reach the empty space beyond.
    #   cap_count -- only the ones already CAPPED.  A CAP goes on the next uncapped
    #     stack, so it only has to get past the finished ones.
    #
    # Operator, 2026-09-24: 「有紫色才要封顶 跳过已经封顶的」 for the cap, and
    # 「下一次取回三个橙色 要跳过两个建筑 然后执行搭建」 for the placement.  Both
    # counters are the route's own memory, deliberately: BUILD_OCCUPANCY sees orange
    # and cannot tell a capped building from a stack still waiting for its cap --
    # the cap sits on the same two layers.
    building_count: int = 0
    cap_count: int = 0
    purple_absent: bool = False
    orange_absent: bool = False
    build_plan: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if self.left_slot is None and self.purple_count:
            self.left_slot = "purple"
        # Without purple, orange fills left, then right, then suction. With
        # purple, left is reserved and orange starts at the right.
        if self.left_slot is None and self.orange_count:
            self.left_slot = "orange"
            self.orange_count -= 1
        if self.right_slot is None and self.orange_count:
            self.right_slot = "orange"
            self.orange_count -= 1
        if self.suction_slot is None and self.orange_count:
            self.suction_slot = "orange"
            self.orange_count -= 1
        self._validate()

    @property
    def total_count(self) -> int:
        return sum(slot is not None for slot in (self.left_slot, self.right_slot, self.suction_slot))

    @property
    def has_purple(self) -> bool:
        return self.left_slot == "purple"

    @property
    def orange_count_actual(self) -> int:
        return sum(slot == "orange" for slot in (self.left_slot, self.right_slot, self.suction_slot))

    def _validate(self) -> None:
        slots = (self.left_slot, self.right_slot, self.suction_slot)
        if any(slot not in {None, "purple", "orange"} for slot in slots):
            raise ValueError("inventory slots must be empty, purple, or orange")
        if sum(slot == "purple" for slot in slots) > 1:
            raise ValueError("the chassis may carry at most one purple block")
        if len([slot for slot in slots if slot is not None]) > 3:
            raise ValueError("the chassis may carry at most three blocks")
        if self.left_slot == "purple" and self.purple_count not in (0, 1):
            raise ValueError("purple_count must be 0 or 1")
        self.purple_count = int(self.left_slot == "purple")
        self.orange_count = self.orange_count_actual

    def reset_inventory(self) -> None:
        self.left_slot = self.right_slot = self.suction_slot = None
        self.purple_count = self.orange_count = 0
        self._validate()

    def record_purple(self) -> None:
        if self.has_purple:
            raise ValueError("a purple block is already on the chassis")
        if self.total_count >= 3 or self.left_slot is not None:
            raise ValueError("purple requires the empty left slot")
        self.left_slot = "purple"
        self.purple_absent = False
        self._validate()

    def next_orange_slot(self) -> str | None:
        if self.total_count >= 3:
            return None
        if not self.has_purple and self.left_slot is None:
            return "left"
        if self.right_slot is None:
            return "right"
        if self.suction_slot is None:
            return "suction"
        if self.left_slot is None:
            return "left"
        return None

    def record_orange(self) -> str:
        slot = self.next_orange_slot()
        if slot is None:
            raise ValueError("no chassis slot remains for an orange block")
        setattr(self, f"{slot}_slot", "orange")
        self.orange_absent = False
        self._validate()
        return slot

    @property
    def supply_exhausted(self) -> bool:
        return self.purple_absent and self.orange_absent

    def consume(self, *, orange: int = 0, purple: int = 0) -> None:
        if orange < 0 or purple < 0:
            raise ValueError("consumption cannot be negative")
        if purple:
            if purple != 1 or not self.has_purple:
                raise ValueError("requested purple is not on the chassis")
            self.left_slot = None
        for slot_name in ("suction_slot", "right_slot", "left_slot"):
            if orange == 0:
                break
            if getattr(self, slot_name) == "orange":
                setattr(self, slot_name, None)
                orange -= 1
        if orange:
            raise ValueError("requested orange count is not on the chassis")
        self._validate()


def orange_capacity(*, purple_count: int, orange_count: int) -> int:
    if purple_count not in (0, 1) or not 0 <= orange_count <= 3:
        raise ValueError("invalid inventory counts")
    remaining = 3 - purple_count - orange_count
    if remaining < 0:
        raise ValueError("inventory exceeds the three-block chassis capacity")
    return remaining


def next_route_after_build(context: LoopContext) -> str:
    return "DIRECT_ORANGE_D330" if context.has_purple else "J3_PURPLE_CHECK"


# The plan actions that put a CAP on a stack that is already standing, as opposed
# to placing a new structure.  build_plan_for_inventory only ever returns these
# three from its capping branch (see the `build_waiting_for_purple` block).
#
# The distinction is what tells BUILD_AREA to CENTRE on a blob rather than slide
# past buildings: a cap goes on the stack the car is looking at, so it has to line
# up with it first (operator, 2026-09-24: 「你应该用视觉找到方块正中 然后执行动作
# 封顶」), while a placement happens in the empty space beyond the buildings.
CAP_ACTIONS = frozenset({
    "PLACE_PURPLE",
    "TOP_SUCTION_ORANGE_PURPLE",
    "TOP_RIGHT_ORANGE_PURPLE",
})


def build_plan_for_inventory(context: LoopContext) -> tuple[str, ...]:
    """Return action names in their physical execution order."""
    # A pending cap is a property of the build area, not of this process's
    # last action.  `build_waiting_for_purple` is still kept for diagnostics and
    # backwards compatibility, but it is lost when the route is restarted and
    # it was never set by the orange-only BUILD_2/BUILD_3 actions.  The durable
    # invariant is: there are more standing buildings than finished (capped)
    # buildings.  That covers both cases:
    #
    #   * BUILD_2 followed by a later purple arrival; and
    #   * a process started with an already standing, uncapped building.
    pending_cap = context.building_count > context.cap_count
    if context.has_purple and context.orange_count_actual >= 2:
        # One purple and two orange is a WHOLE structure, start to finish: the base
        # package lays the two orange layers and the car caps the stack on the very
        # same visit, WITHOUT going back out for a fourth layer.
        #
        # Operator, 2026-10-02: 「到了第二栋建筑 直接执行吸盘加右边搭建两层 然后直接
        # 执行用左边的紫色封第三层 ... 保证完成两层建筑就行」, and on whether this is
        # the first structure only: 「每栋都这样」.  So the pair is unconditional --
        # 2 layers (`build_base`, 「从吸盘和右侧搭两层」) plus the left purple as the
        # third (`cap_purple`, 「从左吸搭第三层」, the same package PLACE_PURPLE maps
        # to).
        #
        # Order matters twice over here:
        #
        #   * It sits ABOVE the pending-cap branch.  `build_waiting_for_purple` and
        #     `pending_cap` are both true on the arrival AFTER a build_base, and that
        #     branch used to answer with TOP_*_ORANGE_PURPLE -- the 3rd-and-4th-layer
        #     package.  That is the round this change replaces.
        #   * The base must come first: `consume(orange=2)` takes from
        #     suction, then right; PLACE_PURPLE then takes the left slot, and it
        #     raises unless the left block is the purple.
        #
        # The two actions are one visit.  `run_route_v2` already drives a multi-action
        # `build_plan` (reset -> action -> reset -> action) and calls `apply_build_action`
        # on each completion, so BUILD_BASE then PLACE_PURPLE lands as
        # building_count +1 / cap_count +1 -- a finished structure, and no
        # `build_waiting_for_purple` left over for the next arrival to trip on.
        #
        # This branch used to return BUILD_BASE alone (gated, earlier still, on `not
        # initial_build_done`).  Operator, 2026-09-24: 「搭建完一个封顶的四个后 下一次
        # 回到搭建区 应该是先放下吸盘的和取右边的搭建两层 再回去补橙色 回来封顶！」
        return ("BUILD_BASE", "PLACE_PURPLE")
    if context.has_purple and (context.build_waiting_for_purple or pending_cap):
        if context.suction_slot == "orange":
            return ("TOP_SUCTION_ORANGE_PURPLE",)
        if context.right_slot == "orange":
            return ("TOP_RIGHT_ORANGE_PURPLE",)
        return ("PLACE_PURPLE",)
    if context.has_purple and not context.initial_build_done:
        return ("PLACE_PURPLE",)
    orange = context.orange_count_actual
    if orange == 3:
        return ("BUILD_3",)
    if orange == 2:
        return ("BUILD_2",)
    if orange == 1:
        return ("PLACE_ORANGE",)
    return ()


def apply_build_action(context: LoopContext, action: str) -> None:
    if action == "BUILD_BASE":
        context.consume(orange=2)
        # A new structure is standing now, so the next arrival must get past it.
        context.building_count += 1
        # Every base stack is left waiting for its cap -- not only the first one.
        # The operator's round is 「先放下吸盘的和取右边的搭建两层 再回去补橙色 回来
        # 封顶」: the cap is always a LATER visit, so it always has to be requested.
        context.initial_build_done = True
        context.build_waiting_for_purple = True
    elif action == "BUILD_2":
        context.consume(orange=2)
        context.building_count += 1
    elif action in {"TOP_SUCTION_ORANGE_PURPLE", "TOP_RIGHT_ORANGE_PURPLE"}:
        context.consume(orange=1, purple=1)
        context.build_waiting_for_purple = False
        # A cap went on: that structure is finished now, and the next cap arrival
        # has one more finished building to get past (「跳过已经封顶的」).
        context.cap_count += 1
    elif action == "PLACE_PURPLE":
        context.consume(purple=1)
        context.build_waiting_for_purple = False
        context.cap_count += 1
    elif action == "BUILD_3":
        context.consume(orange=3)
        # Three more layers, still a structure of its own: the next arrival must
        # get past it too.
        context.building_count += 1
    elif action == "PLACE_ORANGE":
        context.consume(orange=1)
        context.building_count += 1
    else:
        raise ValueError(f"unknown build action: {action}")


def prepare_build_plan(context: LoopContext) -> tuple[str, ...]:
    context.build_plan = build_plan_for_inventory(context)
    return context.build_plan


def finish_build_plan(context: LoopContext) -> None:
    for action in context.build_plan:
        apply_build_action(context, action)
    context.build_plan = ()
