"""Wait for the chassis device node to come back, then probe it.

The JDY-31 drops an idle SPP session after ~13 s; the maintainer service repairs
it by re-running `rfcomm connect`, which DESTROYS /dev/rfcomm0 and the
/dev/robogame-chassis symlink for a few seconds.  A launch attempted inside that
window gets an empty reply from every command and, if it is the runner, a
permanent [Errno 5] and FAULT_SAFE.

So: wait for the node, then ask the chassis something read-only, and report
whether it actually answered.
"""
import os
import sys
import time

sys.path.insert(0, "/home/pi/robogame-runtime")

DEV = "/dev/robogame-chassis"


def main():
    deadline = time.time() + float(sys.argv[1] if len(sys.argv) > 1 else 60)
    waited = 0.0
    while time.time() < deadline:
        if os.path.exists(DEV):
            break
        time.sleep(0.5)
        waited += 0.5
    else:
        print(f"TIMEOUT  {DEV} still absent after {waited:.0f}s")
        return 1

    print(f"present after {waited:.0f}s")

    # Read-only probe.  STOP is a halt, never a motion command, so this is safe
    # to run without the operator's go-ahead.
    import serial
    try:
        port = serial.Serial(DEV, 115200, timeout=1.0)
    except Exception as exc:
        print(f"OPEN FAILED  {exc.__class__.__name__}: {exc}")
        return 1

    port.reset_input_buffer()
    for label, cmd in (("STOP", "STOP"), ("SPD", "SPD"), ("ENC", "ENC")):
        port.write((cmd + "\r\n").encode())
        port.flush()
        time.sleep(0.35)
        reply = port.readline().decode("utf-8", "replace").strip()
        print(f"  {label:<5} -> {reply!r}")
    port.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
