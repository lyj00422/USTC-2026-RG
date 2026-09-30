"""READ-ONLY: can the catalog be loaded and every package compiled, on the Pi?

Opens no device and sends nothing -- it is the cheap half of `_pi_run_action.py`
without the hardware.  Answers the two questions that actually break a bench
action: is the role present, and does its package compile to steps?

Origins are printed so a wrong role is obvious before anything moves.
"""
import sys

sys.path.insert(0, "/home/pi/robogame-runtime")

from pathlib import Path  # noqa: E402

ROOT = Path("/home/pi/robogame-runtime")
ROLES = ("reset", "build_three")


def main() -> int:
    from route_v2.pickup_action import compile_action, load_action_catalog

    catalog = load_action_catalog(ROOT / "data" / "route_v2_actions")
    print("roles   : " + ", ".join(sorted(catalog)))
    rc = 0
    for role in ROLES:
        if role not in catalog:
            print(f"MISSING : {role}")
            rc = 1
            continue
        package = catalog[role]
        steps = compile_action(
            package,
            forward_speed_limit=80,
            expected_suction=tuple(
                step.get("enabled") for step in package["action"]["steps"]
                if step.get("kind") == "SUCTION"
            ),
        )
        print(f"\n{role}: {len(steps)} compiled steps  ({catalog[role].get('path')})")
        for index, step in enumerate(steps):
            print(f"  {index:3d}  {step.kind:16s} {_describe(step)}")
    return rc


# The console's own axis labels (control_hub/static/operate.html).
AXIS = {0: "底座", 1: "大臂", 2: "小臂", 3: "腕部", 4: "夹具"}


def _describe(step) -> str:
    if step.kind == "servo":
        return (f"id{step.servo_id} {AXIS.get(step.servo_id, '?')} -> "
                f"{step.position} ({step.time_ms} ms)")
    if step.kind == "suction":
        return "ON" if step.enabled else "OFF (release)"
    if step.kind == "chassis_velocity":
        return f"vx={step.vx} vy={step.vy} wz={step.wz} for {step.duration_s:.3f} s"
    if step.kind == "chassis_distance":
        return (f"D forward={step.forward_cm} right={step.right_cm} "
                f"rotate={step.rotate_deg} speed={step.speed}")
    return str(vars(step))


if __name__ == "__main__":
    raise SystemExit(main())
