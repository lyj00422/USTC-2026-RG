"""End-to-end acceptance test for the chassis link.

Uses exactly the path the runtime hub and run_route_v2.py use: take the shared
port lock, open /dev/robogame-chassis, talk to the firmware, close, release.

Only sends STOP (safe) plus read-only SPD/ENC queries.  No motion commands.
"""

import sys
import time

sys.path.insert(0, "/home/pi/robogame-runtime")

from rg_runtime.chassis_lock import ChassisPortLock
from rg_runtime.transports import SerialTransport

DEVICE = "/dev/robogame-chassis"

lock = ChassisPortLock()
lock.acquire()
print(f"1. port lock acquired (degraded={lock.degraded})", flush=True)

try:
    transport = SerialTransport(DEVICE, 9600, timeout_s=0.0)
    print(f"2. opened {DEVICE}", flush=True)
    try:
        for label in ("STOP", "SPD", "ENC", "SPD", "ENC"):
            transport.send_line(f"{label}\r\n")
            time.sleep(0.35)
            replies = transport.read_lines()
            print(f"3. {label:<4} -> {replies}", flush=True)
    finally:
        transport.close()
        print("4. serial closed", flush=True)
finally:
    lock.release()
    print("5. port lock released", flush=True)

# A second holder must be refused while the first still holds it.
first = ChassisPortLock()
first.acquire()
second = ChassisPortLock()
try:
    second.acquire()
    print("6. CONTENTION CHECK FAILED: second holder was allowed in", flush=True)
except Exception as exc:
    print(f"6. contention check OK: second holder refused ({type(exc).__name__}: {exc})", flush=True)
finally:
    first.release()
    second.release()
print("done", flush=True)
