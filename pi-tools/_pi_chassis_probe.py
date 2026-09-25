"""Chassis link probe. Sends ONLY 'ENC' (read-only query). Sends NO motion command.

Deliberately does NOT send STOP either, since STOP is a motion command and the
user has not yet authorised touching the chassis.
"""
import time

from rg_runtime.transports import SerialTransport

DEVICE = "/dev/robogame-chassis"
BAUD = 9600

transport = SerialTransport(DEVICE, BAUD)
print(f"opened {DEVICE} @ {BAUD}")
try:
    transport.send_line("ENC\r\n")
    deadline = time.monotonic() + 3.0
    seen = []
    while time.monotonic() < deadline:
        for line in transport.read_lines():
            seen.append(line)
        time.sleep(0.05)
    if seen:
        for line in seen:
            print(f"  recv: {line.strip()!r}")
    else:
        print("  NO REPLY within 3s")
finally:
    transport.close()
print("closed")
