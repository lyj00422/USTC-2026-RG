"""Run ONE route action package on the hardware, once.  MOTION.

Runs ON the Pi via `_pi_run_file.py`, which cannot forward argv (see the note in
`_pi_arm_action1.py`), so the role is the constant below.

Why this exists: there is no way to ask the route for a single package.  The plan
comes from `build_plan_for_inventory(loop_context)` and the in-process inventory
is empty at every fresh start, whatever is physically on the car -- so a
"three oranges, three layers" build cannot be requested from the route at all.

What it drives is the route's own objects, not a re-implementation: the same
`_prepare_route_arm` preflight, the same `ChassisLink` (port lock + heartbeat),
the same compiled catalog and the same settle values `build_settles` gives the
route's build catalog.  The step sequence is the route's too -- RESET to
completion first, then the package.

Leaves the arm where the package put it and the suction at whatever the package's
last SUCTION step said: nothing here sends ARM,STOP, because STOP is what releases
the suction.  The chassis IS stopped on the way out.
"""
import sys
import time

sys.path.insert(0, "/home/pi/robogame-runtime")

from pathlib import Path  # noqa: E402

ROOT = Path("/home/pi/robogame-runtime")

# Catalog roles are in data/route_v2_actions/catalog.json.  build_three is
# 「橙=3 -> 吸盘已有 -> 右 -> 左」, layers one to three.
ROLE = "build_three"
TICK_S = 0.1
PACKAGE_TIMEOUT_S = 180.0
# How many link rebuilds one package may survive before the test gives up.  The
# route has no cap of its own -- a drop costs it a pause, not the run -- so this
# is deliberately generous.
MAX_RECOVERIES = 10
# The arm preflight's FIRST command is `ARM,STOP` (`ArmSession.safe_probe`), and
# STOP is what releases the suction -- so whatever the cup was holding is gone by
# the time this script's package starts.  `build_three` needs it back: its step 3
# releases the block already on the cup for layer one.  So the suction is turned
# back on here and this is how long the operator has to stick the orange to it.
#
# `reset` has no SUCTION step (checked: 5 servo steps, nothing else), so it does
# NOT drop the block -- only the preflight does.
OPERATOR_ATTACH_S = 25.0


def _compile(catalog):
    from route_v2.pickup_action import compile_action

    return {
        role: compile_action(
            package,
            forward_speed_limit=80,
            expected_suction=tuple(
                step.get("enabled") for step in package["action"]["steps"]
                if step.get("kind") == "SUCTION"
            ),
        )
        for role, package in catalog.items()
    }


def main() -> int:
    from rg_runtime.app_support import load_runtime_config
    from rg_runtime.chassis_link import ChassisLink
    from route_v2.config import load_route_v2_config
    from route_v2.pickup_action import ActionCatalogExecutor, load_action_catalog
    from run_route_v2 import _prepare_route_arm

    runtime = load_runtime_config(str(ROOT / "config" / "runtime.yaml"))
    config = load_route_v2_config(ROOT / "config" / "route_v2.yaml")

    print(f"role    : {ROLE}")
    arm = _prepare_route_arm(runtime)
    state = arm.arm.state
    print(f"arm     : ready mode={state.mode} calibrated={state.calibrated} "
          f"suction={state.suction_commanded}")

    catalog = load_action_catalog(ROOT / "data" / "route_v2_actions")
    if ROLE not in catalog:
        print(f"REFUSED : no role {ROLE!r} in the catalog; have {sorted(catalog)}")
        return 2
    compiled = _compile(catalog)
    executor = ActionCatalogExecutor(
        {"RESET": compiled["reset"], ROLE: compiled[ROLE]},
        arm,
        action_ref_prefix="arm",
        arm_move_settle_s=config.build_arm_move_settle_s,
        arm_lift_servo_id=config.arm_lift_servo_id,
    )

    chassis = ChassisLink(
        runtime.chassis_device,
        runtime.chassis_baudrate,
        heartbeat_enabled=config.chassis_heartbeat_enabled,
        heartbeat_s=config.chassis_heartbeat_s,
        heartbeat_quiet_s=config.chassis_heartbeat_quiet_s,
        log=lambda message: print(f"link    : {message}", flush=True),
    )
    chassis.open()
    chassis.stop()
    print(f"chassis : {runtime.chassis_device} open and stopped\n")

    latch = bool(state.suction_commanded)
    try:
        for phase in ("RESET", ROLE):
            if phase == ROLE:
                # Give the cup its block back before the package that places it.
                arm.suction(True)
                latch = True
                print(f"\nsuction : ARM,SUCTION,1 -- stick the orange onto the cup NOW; "
                      f"{OPERATOR_ATTACH_S:.0f}s", flush=True)
                deadline = time.monotonic() + OPERATOR_ATTACH_S
                while time.monotonic() < deadline:
                    remaining = deadline - time.monotonic()
                    print(f"          {remaining:4.1f}s", flush=True)
                    time.sleep(min(5.0, max(0.1, remaining)))
                # Drain the replies that queued while nobody was reading, then
                # leave a clear gap before the executor speaks.  The firmware
                # answers only the FIRST command of a back-to-back burst -- the
                # same rule the console's keepalive quiet window exists for -- and
                # an `ARM,STATUS` landing 1 ms before the package's first
                # `ARM,SERVO` swallows the SERVO, so the step times out with
                # `arm SERVO acknowledgement timed out` (bench, 2026-09-30).
                # Draining first also stops a stale `ACK,SERVO` from the RESET
                # phase from being read as the next step's acknowledgement.
                for _ in range(10):
                    arm.poll()
                    time.sleep(0.1)
                arm.status()
                time.sleep(1.5)
                arm.poll()
                print(f"suction : firmware reports commanded="
                      f"{arm.arm.state.suction_commanded}\n", flush=True)
            executor.set_action(phase)
            print(f"=== {phase} ===", flush=True)
            d_done = False
            chassis_stopped = True
            recoveries = 0
            started = time.monotonic()
            while True:
                now = time.monotonic()
                try:
                    result = executor.step(now=now, stop_acknowledged=chassis_stopped,
                                           chassis_done=d_done)
                    replies = chassis.poll()
                    d_done = any(getattr(reply, "kind", None) == "done"
                                 for reply in replies)
                    stamp = f"  {now - started:6.1f}s"

                    if result.suction is not None:
                        latch = bool(result.suction)
                        print(f"{stamp}  SUCTION {latch}", flush=True)
                    if result.distance_timeout is not None:
                        fwd, right, rot, speed = result.distance_timeout
                        print(f"{stamp}  D {fwd} {right} {rot} {speed} reported no DONE "
                              f"-- stopping the move and continuing", flush=True)
                        chassis.stop()
                        chassis_stopped = True
                    if result.chassis_distance is not None:
                        fwd, right, rot, speed = result.chassis_distance
                        chassis.run_distance(fwd, right, rot, speed)
                        d_done = False
                        chassis_stopped = False
                        print(f"{stamp}  D {fwd} {right} {rot} {speed}", flush=True)
                    elif result.chassis_velocity is not None:
                        vx, vy, wz = result.chassis_velocity
                        chassis.set_velocity(vx, vy, wz)
                        chassis_stopped = False
                        print(f"{stamp}  V {vx} {vy} {wz}", flush=True)
                    elif result.chassis_stop and not chassis_stopped:
                        chassis.stop()
                        chassis_stopped = True
                        print(f"{stamp}  STOP", flush=True)
                except OSError as exc:
                    # The RFCOMM handle dies on this hardware -- the JDY-31 drops
                    # an idle session and the maintainer service rebuilds the tty
                    # underneath whoever is holding it.  The route survives this
                    # through `ChassisLink.recover` (run_route_v2.py:1670); this
                    # runner has to do the same or the first drop ends the test
                    # (bench, 2026-09-30: `[Errno 5]` 4 s into RESET).
                    #
                    # `recover` comes back only once it has a fresh device it has
                    # itself confirmed stopped, and it prints the idle time that
                    # says whether this was an idle drop or a power problem.
                    recoveries += 1
                    if recoveries > MAX_RECOVERIES:
                        print(f"\nFAULT   : chassis link lost {recoveries} times, "
                              f"giving up: {exc}", flush=True)
                        return 1
                    chassis = chassis.recover(exc)
                    chassis_stopped = True
                    d_done = False
                    continue

                if result.fault:
                    print(f"\nFAULT   : {result.fault}  (phase {phase})", flush=True)
                    chassis.stop()
                    return 1
                if result.done:
                    print(f"done    : {phase} in {now - started:.1f}s", flush=True)
                    break
                if now - started > PACKAGE_TIMEOUT_S:
                    print(f"\nFAULT   : {phase} did not finish within "
                          f"{PACKAGE_TIMEOUT_S:.0f}s", flush=True)
                    chassis.stop()
                    return 1
                time.sleep(TICK_S)
    finally:
        try:
            chassis.stop()
            chassis.close()
        except Exception as exc:                              # noqa: BLE001
            print(f"link    : close failed: {exc}")
        print(f"suction : left at {latch} (no ARM,STOP: STOP releases it)")
        try:
            arm.transport.close()
        except Exception:
            pass
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
