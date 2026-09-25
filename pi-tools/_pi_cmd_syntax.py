"""Check exactly which D argument forms the chassis firmware accepts.

The lateral shift loop issued `D 0 -10.0 0 25` and the car never moved, while
`D 0 10 0 20` (integers) had worked earlier.  This sends one tiny move per form
and prints the raw reply, so the accepted syntax is established rather than
guessed.  Moves are 1 cm at low speed.

Usage: python3 _pi_cmd_syntax.py
"""

import atexit
import fcntl
import os
import sys
import time

import serial

LOCK = "/run/lock/robogame-chassis.lock"
DEV = "/dev/robogame-chassis"
BAUD = 9600

FORMS = [
    "D 0 -1 0 20",      # integer, rightward 1 cm
    "D 0 -1.0 0 20",    # float -- the form that appeared to be ignored
    "D 0 1 0 20",       # integer, leftward 1 cm
]


def open_chassis():
    for attempt in range(1, 13):
        if not os.path.exists(DEV):
            print(f"  [{attempt}] {DEV} absent; waiting", flush=True)
            time.sleep(2.0)
            continue
        try:
            ser = serial.Serial(DEV, BAUD, timeout=0.05)
        except Exception:
            time.sleep(2.0)
            continue
        try:
            ser.write(b"STOP\r\n")
            ser.flush()
            time.sleep(0.4)
            if b"OK" not in ser.read(256):
                raise RuntimeError("no reply to STOP probe")
            lock = open(LOCK, "a+")
            fcntl.flock(lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            ser.reset_input_buffer()
            return ser, lock
        except Exception:
            try:
                ser.close()
            except Exception:
                pass
        time.sleep(2.0)
    raise RuntimeError("chassis link never stabilised")


def drain(ser, seconds):
    end = time.time() + seconds
    out = b""
    while time.time() < end:
        data = ser.read(256)
        if data:
            out += data
    return out.decode("ascii", "replace")


def enc(ser):
    ser.reset_input_buffer()
    ser.write(b"ENC\r\n")
    ser.flush()
    time.sleep(0.3)
    for line in drain(ser, 0.2).splitlines():
        if line.startswith("ENC "):
            return line.strip()
    return "(no ENC reply)"


def main():
    ser, lock = open_chassis()
    atexit.register(lambda: ser.is_open and (ser.write(b"STOP\r\n"), ser.flush()))
    print(f"chassis open, lock held\n", flush=True)

    for form in FORMS:
        print(f"=== {form!r} ===", flush=True)
        ser.write(b"ENC RESET\r\n")
        ser.flush()
        time.sleep(0.3)
        drain(ser, 0.3)

        ser.write(form.encode("ascii") + b"\r\n")
        ser.flush()
        reply = ""
        deadline = time.time() + 8.0
        while time.time() < deadline:
            reply += drain(ser, 0.2)
            if "DONE" in reply or "ERR" in reply:
                break
        print(f"  reply : {' '.join(reply.split()) or '(none)'}", flush=True)
        time.sleep(0.4)
        print(f"  enc   : {enc(ser)}", flush=True)
        time.sleep(1.5)

    ser.write(b"STOP\r\n")
    ser.flush()
    time.sleep(0.3)
    ser.close()
    try:
        fcntl.flock(lock.fileno(), fcntl.LOCK_UN)
        lock.close()
    except Exception:
        pass
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
