"""Decisive test: does the RFCOMM chassis link survive while an app holds the lock?

The maintainer service re-creates /dev/rfcomm0 every ~13 s.  Two hypotheses:

  H1  the maintainer is tearing the link down because no app holds the lock;
      holding LOCK_EX should make it stop, and the link should then stay up.
  H2  the JDY-31 itself drops idle SPP sessions; holding the lock only stops
      the maintainer from repairing it, so the link still dies.

Phase A holds the lock with the port open but writes nothing (pure idle).
Phase B keeps the lock and adds a periodic safe STOP, i.e. real traffic.

Sends nothing but STOP, which is unconditionally safe.

Usage: python3 _pi_link_hold.py [idle_seconds] [traffic_seconds]
"""

import fcntl
import sys
import time

import serial

LOCK = "/run/lock/robogame-chassis.lock"
DEV = "/dev/robogame-chassis"
BAUD = 9600

IDLE_S = float(sys.argv[1]) if len(sys.argv) > 1 else 40.0
TRAFFIC_S = float(sys.argv[2]) if len(sys.argv) > 2 else 50.0


def stamp(t0):
    return f"[{time.time() - t0:6.1f}s]"


def main():
    t0 = time.time()
    lock = open(LOCK, "a+")
    fcntl.flock(lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    print(f"{stamp(t0)} LOCK_EX held on {LOCK}", flush=True)

    ser = serial.Serial(DEV, BAUD, timeout=0.1)
    print(f"{stamp(t0)} opened {DEV} @ {BAUD}", flush=True)

    errors = []
    rx_total = 0
    idle_deadline = t0 + IDLE_S
    end = t0 + IDLE_S + TRAFFIC_S
    phase = "A(idle,no-writes)"
    next_tx = idle_deadline
    last_tx = None

    print(f"{stamp(t0)} phase A: {IDLE_S:.0f}s idle, no writes", flush=True)

    while time.time() < end:
        now = time.time()
        if phase == "A(idle,no-writes)" and now >= idle_deadline:
            phase = "B(traffic,STOP-2s)"
            next_tx = now
            print(f"{stamp(t0)} phase B: {TRAFFIC_S:.0f}s with STOP every 2s", flush=True)

        if phase.startswith("B") and now >= next_tx:
            try:
                ser.write(b"STOP\r\n")
                ser.flush()
                last_tx = now
                next_tx = now + 2.0
            except Exception as exc:
                errors.append((now - t0, "write", repr(exc)))
                print(f"{stamp(t0)} WRITE FAIL: {exc!r}", flush=True)
                break

        try:
            data = ser.read(256)
            if data:
                rx_total += len(data)
                print(f"{stamp(t0)} RX {len(data)}B {data[:60]!r}", flush=True)
        except Exception as exc:
            errors.append((now - t0, "read", repr(exc)))
            print(f"{stamp(t0)} READ FAIL: {exc!r}", flush=True)
            break

        time.sleep(0.05)

    alive = time.time() - t0
    print(f"\n=== result ===", flush=True)
    print(f"survived {alive:.1f}s of {IDLE_S + TRAFFIC_S:.0f}s requested", flush=True)
    print(f"rx bytes total: {rx_total}", flush=True)
    print(f"errors: {errors if errors else 'NONE'}", flush=True)

    try:
        ser.write(b"STOP\r\n")
        ser.flush()
    except Exception:
        pass
    try:
        ser.close()
    except Exception:
        pass
    fcntl.flock(lock.fileno(), fcntl.LOCK_UN)
    lock.close()
    print("lock released, port closed", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
