"""Turn the arm's suction ON and leave it on.  NO MOTION.

Runs ON the Pi via `_pi_run_file.py` (which cannot forward argv -- see the note in
`_pi_arm_action1.py`), so this script has exactly one job.

The suction LATCHES in the firmware: it stays on until an explicit
`ARM,SUCTION,0` or an `ARM,STOP`, so this closes the port with the arm still
holding.  `_pi_arm_restore.py` is the way back -- its `ARM,STOP` is what releases
it.

Enabling the arm powers the servos and holds them where they physically are; that
is the same thing the console's auto-connect does and what the route's own start
does (`_prepare_route_arm`).  The pre-flight is copied from `_pi_arm_home.py` so
this cannot leave the arm in a state the route would refuse to start from: safe
probe, require CAL=1 and LOCKED, then enable.
"""
import sys
import time

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
        print(f"probe   : {runtime.arm_device} mode={state.mode} "
              f"calibrated={state.calibrated} suction={state.suction_commanded}")
        for line in session.last_operation_raw:
            print(f"          {line}")
        if state.calibrated is not True:
            print("REFUSED : arm must report CAL=1 -- nothing sent")
            return 2
        if state.mode is not ArmMode.LOCKED:
            print(f"REFUSED : expected LOCKED, got {state.mode} -- nothing sent")
            return 2

        session.enable()
        session.wait_for_mode(ArmMode.READY, timeout_s)
        print(f"enabled : mode={session.arm.state.mode}")

        session.suction(True)
        print("sent    : ARM,SUCTION,1")
        # The firmware's own STATE reply is the authority, not the ACK.
        deadline = time.monotonic() + 3.0
        while time.monotonic() < deadline:
            session.poll()
            session.status()
            if session.arm.state.suction_commanded:
                break
            time.sleep(0.05)
        print(f"suction : commanded={session.arm.state.suction_commanded} "
              f"mode={session.arm.state.mode}")
        for line in session.last_operation_raw[-6:]:
            print(f"          {line}")
        if not session.arm.state.suction_commanded:
            print("FAILED  : firmware does not report the suction as commanded")
            rc = 1
        else:
            print("done    : suction is ON and stays on after this script exits")
    except Exception as exc:
        rc = 1
        print(f"FAILED  : {type(exc).__name__}: {exc}")
    finally:
        # Deliberately NO ARM,STOP on the way out: STOP is what releases the
        # suction, and releasing it is the one thing this script must not do.
        transport.close()
    return rc


if __name__ == "__main__":
    raise SystemExit(main())
