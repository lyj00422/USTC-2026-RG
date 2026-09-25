"""Put the arm back to its resting state: ARM,STOP only.

Sends ARM,STOP -- which releases the suction and returns the arm to LOCKED --
then asks for a fresh STATE and verifies both from the firmware's own reply.
Sends no motion command: no ENABLE, no RUN, no SERVO.  The arm stays wherever it
physically is; this only clears the latched suction and the unlocked mode.

The inverse of _pi_arm_suction.py.  Use _pi_arm_home.py if the arm also has to be
driven back to the home pose -- that one moves servos and is a motion command.
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
    transport = SerialTransport(runtime.arm_device, runtime.arm_baudrate, timeout_s=0.0)
    session = ArmSession(ArmDevice(transport), transport)
    try:
        session.stop()
        deadline = time.monotonic() + 3.0
        state = session.arm.state
        while time.monotonic() < deadline:
            session.status()
            session.poll()
            state = session.arm.state
            if state.mode is ArmMode.LOCKED and not state.suction_commanded:
                break
            time.sleep(0.1)

        print(f"mode       : {state.mode}")
        print(f"calibrated : {state.calibrated}")
        print(f"suction    : {state.suction_commanded}")
        restored = state.mode is ArmMode.LOCKED and not state.suction_commanded
        print("RESTORED" if restored else "NOT RESTORED")
        return 0 if restored else 1
    except Exception as exc:
        print(f"FAILED     : {type(exc).__name__}: {exc}")
        return 1
    finally:
        transport.close()


if __name__ == "__main__":
    raise SystemExit(main())
