"""Read-only check that /dev/robogame-chassis actually carries data.

Sends only encoder/speed queries and STOP. No motion commands.
"""
import time

import serial

PORT = "/dev/robogame-chassis"

with serial.Serial(PORT, 9600, timeout=0.3) as ser:
    print(f"opened {PORT} @9600 8N1")

    for cmd in ("ENC", "SPD"):
        ser.reset_input_buffer()
        ser.write(f"{cmd}\r\n".encode("ascii"))
        ser.flush()
        time.sleep(0.6)
        raw = ser.read(256)
        print(f"{cmd:4s} -> {raw!r}")

    # leave the chassis in a defined stopped state
    ser.write(b"STOP\r\n")
    ser.flush()
    time.sleep(0.3)
    print(f"STOP -> {ser.read(128)!r}")
