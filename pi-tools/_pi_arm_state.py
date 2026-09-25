"""READ-ONLY: what is the arm doing right now?

Opens the arm serial port and sends ONLY `ARM,STATUS`.  No STOP, no PING, no
MOVE, no routine -- so it cannot change the arm's pose or drop whatever the
suction is holding.  That matters after a pickup run dies mid-package: the
standard safe_probe leads with ARM,STOP, which would release the suction before
anyone had looked at it.

Opens no other device and sends nothing to the chassis.
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
    from rg_runtime.transports import SerialTransport

    runtime = load_runtime_config(str(ROOT / "config/runtime.yaml"))
    transport = SerialTransport(runtime.arm_device, runtime.arm_baudrate, timeout_s=0.0)
    session = ArmSession(ArmDevice(transport), transport)
    got_state = False
    try:
        session.last_operation_raw = []
        session.status()
        deadline = time.monotonic() + 2.0
        while time.monotonic() < deadline:
            batch = session.poll()
            if any(reply.__class__.__name__ == "ArmStateReply" for reply in batch):
                got_state = True
                break
            time.sleep(0.02)
        state = session.arm.state
        print(f"device              : {runtime.arm_device}")
        print(f"mode                : {state.mode}")
        print(f"calibrated          : {state.calibrated}")
        print(f"suction_commanded   : {state.suction_commanded}")
        print(f"routine / step      : {getattr(state, 'routine', None)} / "
              f"{getattr(state, 'step', None)}")
        print(f"state reply seen    : {got_state}")
        print("raw replies         :")
        for line in session.last_operation_raw:
            print(f"    {line}")
    except Exception as exc:
        print(f"arm status FAILED   : {type(exc).__name__}: {exc}")
    finally:
        transport.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
