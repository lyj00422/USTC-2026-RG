"""READ-ONLY pre-flight for the purple pickup bench test.

Loads and compiles the deployed 0004 action package, prints the vision
configuration that will decide the run, and probes the arm for CAL/mode.

Sends only ARM,STOP / ARM,PING / ARM,STATUS -- the arm's safe_probe.  No servo,
no routine, no chassis command of any kind.
"""
import sys

sys.path.insert(0, "/home/pi/robogame-runtime")

from pathlib import Path  # noqa: E402

ROOT = Path("/home/pi/robogame-runtime")


def main():
    from rg_runtime.app_support import load_runtime_config
    from rg_runtime.arm_tools import ArmSession
    from rg_runtime.devices import ArmDevice
    from rg_runtime.transports import SerialTransport
    from route_v2.config import load_route_v2_config
    from route_v2.pickup_action import compile_action, load_action_package

    print("=== config ===")
    config = load_route_v2_config(str(ROOT / "config/route_v2.yaml"))
    vision = config.vision
    print(f"calibrated              : {vision.calibrated}")
    print(f"purple_action_package   : {vision.purple_action_package}")
    print(f"purple_action_fwd_speed : {vision.purple_action_forward_speed}")
    print(f"purple_action_ack_s     : {vision.purple_action_ack_timeout_s}")
    profile = vision.block_profiles["purple_pickup_close"]
    win = profile.capture_window
    print(f"purple capture_window   : {[round(v, 4) for v in (win.left, win.top, win.right, win.bottom)]}")
    print(f"centroid_hsv_bands      : {[(b.lower, b.upper) for b in profile.centroid_hsv_bands]}")
    print(f"centroid_lab_bands      : {[(b.lower, b.upper) for b in profile.centroid_lab_bands]}")
    print(f"centroid_min_area_px    : {profile.centroid_min_area_px}")
    print(f"area_px / height_px     : {profile.area_px.minimum}..{profile.area_px.maximum} / "
          f"{profile.height_px.minimum}..{profile.height_px.maximum}")
    print(f"bottom_y / near_field   : {profile.bottom_y} / {profile.near_field} fill>={profile.min_near_field_fill}")
    purple = vision.pickup_areas["purple"]
    print(f"coarse/fine speed       : {purple.coarse_speed} / {purple.fine_speed}")
    print(f"confirm_frames/settle_s : {purple.confirm_frames} / {purple.settle_s}")
    print(f"search_right            : {purple.search_right.max_distance_cm} cm / {purple.search_right.timeout_s} s")
    print(f"search_left             : {purple.search_left.max_distance_cm} cm / {purple.search_left.timeout_s} s")
    print(f"action_wait_s           : {vision.action_wait_s}")

    print("\n=== 0004 action package ===")
    package = load_action_package(vision.purple_action_package)
    compiled = compile_action(package, forward_speed_limit=vision.purple_action_forward_speed)
    servos = [s for s in compiled if s.kind == "servo"]
    moves = [s for s in compiled if s.kind == "chassis_velocity"]
    suctions = [s for s in compiled if s.kind == "suction"]
    print(f"steps                   : {len(compiled)}")
    print(f"servos                  : {[(s.servo_id, s.position, s.time_ms) for s in servos]}")
    print(f"chassis pulses          : {[(s.vx, round(s.duration_s, 6)) for s in moves]}")
    print(f"suction                 : {[s.enabled for s in suctions]}")
    back = sum(s.duration_s for s in moves if s.vx < 0)
    fwd = sum(s.duration_s for s in moves if s.vx > 0)
    print(f"back/fwd total seconds  : {back:.3f} / {fwd:.3f}")

    print("\n=== arm (safe probe: STOP -> PING -> STATUS) ===")
    runtime = load_runtime_config(str(ROOT / "config/runtime.yaml"))
    transport = SerialTransport(runtime.arm_device, runtime.arm_baudrate, timeout_s=0.0)
    session = ArmSession(ArmDevice(transport), transport)
    try:
        session.safe_probe(runtime.arm_probe_timeout_ms / 1000.0)
        state = session.arm.state
        print(f"device                  : {runtime.arm_device}")
        print(f"mode                    : {state.mode}")
        print(f"calibrated              : {state.calibrated}")
        print(f"suction_commanded       : {state.suction_commanded}")
        print(f"CAL=1 gate              : {'PASS -> RUN 1 / route start allowed' if state.calibrated is True else 'FAIL -> cannot enable, cannot run'}")
        print("raw replies             :")
        for line in session.last_operation_raw:
            print(f"    {line}")
    except Exception as exc:
        print(f"arm probe FAILED        : {type(exc).__name__}: {exc}")
    finally:
        transport.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
