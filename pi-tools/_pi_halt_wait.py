"""Wait for the RFCOMM node to come back, then hammer STOP.

The maintainer drops and rebuilds /dev/robogame-chassis every 13-35 s whenever
the link idles out, so a halt issued at the wrong moment finds no port at all
and cannot brake anything.  This one waits for the node instead of assuming it.
"""
import os
import time

import serial

PORT = "/dev/robogame-chassis"
deadline = time.time() + 90.0
while time.time() < deadline:
    if os.path.exists(PORT):
        break
    time.sleep(0.5)
else:
    print("node never came back within 90s")
    raise SystemExit(1)

print(f"node present after {90.0 - (deadline - time.time()):.1f}s")
time.sleep(1.0)  # let the maintainer finish wiring the pty up

with serial.Serial(PORT, 9600, timeout=0.3) as ser:
    for i in range(60):
        ser.reset_input_buffer()
        ser.write(b"STOP\r\n")
        ser.flush()
        time.sleep(0.05)
        raw = ser.read(128)
        if raw and i < 3:
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
