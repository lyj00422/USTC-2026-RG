"""Emergency halt: hammer STOP until the wheels actually report zero.

route_v2.md records that a SINGLE STOP does not stop this chassis -- the
route re-sends it every 200 ms for exactly that reason.  A route that dies on a
timeout or a fault leaves the last velocity command live, so one STOP from a
diagnostic script is not enough.  This sends it repeatedly and then reads SPD
back, so "stopped" is a measurement rather than an assumption.
"""
import os
import time

import serial

PORT = "/dev/robogame-chassis"

# Wait for the node before opening it.  The RFCOMM maintainer tears the TTY down
# and rebuilds it on every reconnect cycle (release-on-hup), so a halt issued
# moments after anything closed the port lands in a window where the device does
# not exist -- exactly when a STOP matters most.  Observed 2026-09-18: the route
# died with `chassis I/O failure: [Errno 5]` while the car was turning at 46.
deadline = time.monotonic() + float(os.environ.get("RG_HALT_WAIT_S", "45"))
while not os.path.exists(PORT):
    if time.monotonic() >= deadline:
        print(f"ABORT: {PORT} never appeared within {deadline:.0f}s -- "
              f"check chassis power / the robogame-chassis-rfcomm service")
        raise SystemExit(1)
    time.sleep(0.5)

with serial.Serial(PORT, 9600, timeout=0.3) as ser:
    for i in range(60):
        ser.reset_input_buffer()
        ser.write(b"STOP\r\n")
        ser.flush()
        time.sleep(0.05)
        raw = ser.read(128)
        if raw:
            print(f"  {i:02d}  {raw!r}")

    print("readback:")
    for _ in range(3):
        for cmd in ("SPD", "ENC"):
            ser.write(b"STOP\r\n")
            ser.flush()
            time.sleep(0.05)
            ser.reset_input_buffer()
            ser.write(f"{cmd}\r\n".encode("ascii"))
            ser.flush()
            time.sleep(0.4)
            print(f"  {cmd:4s} -> {ser.read(128)!r}")
        time.sleep(0.3)
