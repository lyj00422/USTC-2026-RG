"""Drive the arm to the recorded `reset` package's pose.  MOTION.

Run ON the Pi via `_pi_run_file.py`.  This is the reset action package the route
itself runs -- `data/route_v2_actions/reset_final/action.json`, compiled by the
route's own compiler and driven by the route's own executor -- not the firmware's
built-in routine 1 that `_pi_arm_home.py` uses.  The two are different poses and
the names look alike, which is why this is its own tool.

Why not `_pi_run_action.py`: it is hardcoded to `build_three` and, as of the
2026-10-01 wire swap, it no longer constructs at all -- it still passes
`heartbeat_enabled` / `heartbeat_s` / `heartbeat_quiet_s` to `ChassisLink`,
which no longer accepts them (the heartbeat went with the Bluetooth link).  It
also re-arms the suction and waits 25 s for a block, which a reset must not do:
`reset` has no SUCTION step, so that cup would stay energised afterwards.

Deliberately chassis-free.  The reset package is five SERVO steps and nothing
else (checked), so opening the chassis would take the port lock and put a STOP
on the wire for no reason.  `stop_acknowledged=True` is handed to the executor
every tick, which is the condition it is waiting for.

Leaves the arm where the reset put it and sends no final `ARM,STOP`: STOP returns
the arm to LOCKED and would let it sag off the pose we just drove it to.
"""
import sys
import time

sys.path.insert(0, "/home/pi/robogame-runtime")

from pathlib import Path  # noqa: E402

ROOT = Path("/home/pi/robogame-runtime")
ROLE = "reset"
TICK_S = 0.1
TIMEOUT_S = 180.0


def main() -> int:
    from rg_runtime.app_support import load_runtime_config
    from route_v2.config import load_route_v2_config
    from route_v2.pickup_action import (ActionCatalogExecutor, compile_action,
                                        load_action_catalog)
    from run_route_v2 import _prepare_route_arm

    runtime = load_runtime_config(str(ROOT / "config" / "runtime.yaml"))
    config = load_route_v2_config(ROOT / "config" / "route_v2.yaml")

    catalog = load_action_catalog(ROOT / "data" / "route_v2_actions")
    if ROLE not in catalog:
        print(f"REFUSED : no role {ROLE!r}; have {sorted(catalog)}")
        return 2
    package = catalog[ROLE]
    compiled = compile_action(
        package,
        forward_speed_limit=80,
        expected_suction=tuple(
            step.get("enabled") for step in package["action"]["steps"]
            if step.get("kind") == "SUCTION"
        ),
    )

    print(f"package : {package['action'].get('name')}  ({ROLE})")
    print(f"steps   : {len(compiled)}")
    for step in compiled:
        if step.kind == "servo":
            print(f"          SERVO id={step.servo_id} -> {step.position} "
                  f"in {step.time_ms} ms")
        else:
            print(f"          {step.kind}")
    print()

    arm = _prepare_route_arm(runtime)
    state = arm.arm.state
    print(f"arm     : mode={state.mode} calibrated={state.calibrated} "
          f"suction={state.suction_commanded}\n")

    executor = ActionCatalogExecutor(
        {ROLE: compiled},
        arm,
        action_ref_prefix="arm",
        arm_move_settle_s=config.build_arm_move_settle_s,
        arm_lift_servo_id=config.arm_lift_servo_id,
    )
    executor.set_action(ROLE)

    started = time.monotonic()
    try:
        while True:
            now = time.monotonic()
            result = executor.step(now=now, stop_acknowledged=True,
                                   chassis_done=False)
            if result.fault:
                print(f"\nFAULT   : {result.fault}", flush=True)
                return 1
            if result.done:
                print(f"\ndone    : reset in {now - started:.1f}s", flush=True)
                break
            if now - started > TIMEOUT_S:
                print(f"\nFAULT   : reset did not finish within {TIMEOUT_S:.0f}s",
                      flush=True)
                return 1
            time.sleep(TICK_S)
    finally:
        try:
            arm.poll()
        except Exception:
            pass
        final = arm.arm.state
        print(f"arm     : mode={final.mode} routine={final.routine} "
              f"step={final.step} suction={final.suction_commanded}")
        print("          (no ARM,STOP sent: the arm stays on the reset pose)")
        try:
            arm.transport.close()
        except Exception:
            pass
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
