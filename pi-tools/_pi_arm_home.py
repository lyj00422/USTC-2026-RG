"""Run the console's 动作 1 (ARM,RUN,1) to bring the arm back to its home pose.

MOTION -- this sends ARM,ENABLE and ARM,RUN,1.  It exists because the arm has to
be out of the camera's view before the purple pickup test: routine 1 is the home
pose the operator uses on the console's arm page (operate.html:91,
data-routine="1"), and it is NOT the saved package on disk that happens to share
the name.

The pre-flight mirrors run_route_v2.py:1286 so this cannot leave the arm somewhere
the route would refuse: safe probe, require CAL=1 and LOCKED, then enable and wait
for READY.  Any failure stops on the spot and sends nothing further.

The transport is closed without a final ARM,STOP -- the arm simply stays where
routine 1 left it, which is the point of running it.
"""
import sys

sys.path.insert(0, "/home/pi/robogame-runtime")

from pathlib import Path  # noqa: E402

ROOT = Path("/home/pi/robogame-runtime")


def main():
    from rg_runtime.app_support import load_runtime_config
    from rg_runtime.arm_tools import ArmSession
    from rg_runtime.devices import ArmDevice
    from rg_runtime.hardware_models import ArmMode
    from rg_runtime.transports import SerialTransport

    runtime = load_runtime_config(str(ROOT / "config/runtime.yaml"))
    timeout_s = runtime.arm_probe_timeout_ms / 1000.0
    transport = SerialTransport(runtime.arm_device, runtime.arm_baudrate, timeout_s=0.0)
    session = ArmSession(ArmDevice(transport), transport)
    rc = 0
    try:
        session.safe_probe(timeout_s)
        state = session.arm.state
        print(f"probe   : device={runtime.arm_device} mode={state.mode} "
              f"calibrated={state.calibrated} suction={state.suction_commanded}")
        for line in session.last_operation_raw:
            print(f"          {line}")
        if state.calibrated is not True:
            print("REFUSED : arm must report CAL=1 before enable -- nothing sent")
            return 2
        if state.mode is not ArmMode.LOCKED:
            print(f"REFUSED : expected LOCKED before enable, got {state.mode} -- nothing sent")
            return 2

        session.enable()
        session.wait_for_mode(ArmMode.READY, timeout_s)
        print(f"enabled : mode={session.arm.state.mode}")

        session.run(1)
        print("sent    : ARM,RUN,1  (console 动作 1 -> home pose)")
        session.wait_for_action(1, 60.0)
        print(f"done    : routine 1 finished, mode={session.arm.state.mode}")
    except Exception as exc:
        rc = 1
        print(f"FAILED  : {type(exc).__name__}: {exc}")
        try:
            session.stop()
            print("      : ARM,STOP sent after failure")
        except Exception as stop_exc:
            print(f"      : ARM,STOP also failed: {stop_exc}")
    finally:
        transport.close()
    return rc


if __name__ == "__main__":
    raise SystemExit(main())
