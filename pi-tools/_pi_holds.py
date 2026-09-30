"""Every compiled step of every build package in the newest run, next to the
dwell it actually took on hardware.

Run ON the Pi via _pi_run_file.py.

The point is to stop guessing from a dwell threshold.  The compiled steps say
which index is a 大臂 (servo id 1) move, which is a suction step and which is a
chassis move; the telemetry says how long each index was held.  Put side by side,
"the hold fires on every 大臂 move and nowhere else" is a readable fact rather
than an inference from numbers that happen to look big.

Note the dwell of index N is (step N's own travel) + (the hold that follows it),
because the executor advances the index BEFORE holding.
"""
import glob
import json
import os
import sys

sys.path.insert(0, "/home/pi/robogame-runtime")

from route_v2.pickup_action import compile_action, load_action_catalog  # noqa: E402

CATALOG = "/home/pi/robogame-runtime/data/route_v2_actions"
TAG = "logs/route_v2_*.jsonl"

# route action name -> catalog role
ROLE_OF = {
    "arm:BUILD_BASE": "build_base",
    "arm:BUILD_2": "build_two",
    "arm:BUILD_3": "build_three",
    "arm:PLACE_ORANGE": "place_orange",
    "arm:PLACE_PURPLE": "place_purple",
    "arm:TOP_SUCTION_ORANGE_PURPLE": "top_suction_purple",
    "arm:TOP_RIGHT_ORANGE_PURPLE": "top_right_purple",
    "arm:RESET": "reset",
}


def describe(step) -> str:
    if step.kind == "servo":
        mark = "  <-- 大臂" if step.servo_id == 1 else ""
        return f"servo id{step.servo_id} -> {step.position}{mark}"
    if step.kind == "suction":
        return f"suction {'ON' if step.enabled else 'OFF'}"
    if step.kind == "chassis_velocity":
        return f"chassis v({step.vx},{step.vy},{step.wz}) {step.duration_s:.2f}s"
    if step.kind == "chassis_distance":
        return f"chassis D({step.forward_cm},{step.right_cm},{step.rotate_deg})@{step.speed}"
    return step.kind


def own_seconds(step) -> float:
    """How long this step takes on its own, before any hold it inherits.

    A servo step asks the arm for `time_ms` of travel.  A suction step only waits
    for the arm's ack, which comes back in a tick or two -- measured at ~0.04 s.
    A chassis move runs for its recorded duration.
    """
    if step.kind == "servo":
        return step.time_ms / 1000.0
    if step.kind == "suction":
        return 0.04
    if step.kind == "chassis_velocity":
        return step.duration_s
    return 0.0


def main() -> None:
    hits = [p for p in glob.glob(TAG) if not p.endswith(".out")]
    path = max(hits, key=os.path.getmtime)
    print(f"log: {path}\n")

    catalog = load_action_catalog(CATALOG)
    compiled = {}
    for ref, role in ROLE_OF.items():
        if role not in catalog:
            continue
        package = catalog[role]
        compiled[ref] = compile_action(
            package, forward_speed_limit=80,
            expected_suction=tuple(
                s.get("enabled") for s in package["action"]["steps"]
                if s.get("kind") == "SUCTION"),
        )

    # (state, ref) -> {index: [first_t, last_t]}
    seen = {}
    order = []
    with open(path, encoding="utf-8") as handle:
        for line in handle:
            if not line.strip():
                continue
            d = json.loads(line)
            action = d.get("pickup_action")
            if not action or not action.get("executed"):
                continue
            key = (d.get("state"), action.get("action_ref"))
            if key not in seen:
                seen[key] = {}
                order.append(key)
            index = action.get("step_index")
            row = seen[key].setdefault(index, [d["t"], d["t"]])
            row[1] = d["t"]

    for state, ref in order:
        steps = compiled.get(ref)
        if steps is None:
            continue
        rows = seen[state, ref]
        print(f"=== {state}  {ref}   ({len(steps)} compiled steps)")
        for index, step in enumerate(steps):
            row = rows.get(index)
            dwell = f"{row[1] - row[0]:6.2f} s" if row else "   --   "
            # The executor advances the index BEFORE it holds, so a hold decided
            # by step k lands in the dwell of step k+1.  Attribute it to the step
            # that EARNED it, or the table reads as "the hold is on the wrong
            # step" -- which is exactly the misreading this tool exists to stop.
            flag = ""
            previous = steps[index - 1] if index > 0 else None
            if previous is not None and previous.kind == "servo" \
                    and previous.servo_id == 1 and row:
                # Subtract THIS step's own time, not the previous step's: the
                # previous servo's 0.5 s was already spent inside the previous
                # index's dwell.  What is left over is the hold it earned.
                own = own_seconds(step)
                flag = (f"   <== step {index - 1} (大臂) 的 hold:"
                        f" {row[1] - row[0]:.2f} - {own:.2f} ="
                        f" **{row[1] - row[0] - own:.2f} s**")
            print(f"   {index:>3}  {dwell}  {describe(step)}{flag}")
        print()


main()
