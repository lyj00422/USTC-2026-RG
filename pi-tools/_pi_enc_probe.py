"""Settle why the ENC reply never reached the route telemetry.

The route runner requests SPD and ENC back to back in the same tick, and only
SPD ever produced a value: actual_speed filled in, encoder stayed null for a
whole run.  Three things could explain it -- the firmware ignores a command that
arrives immediately after another, the reply is split across reads, or the reply
never comes at all.  This sends the two queries in each of those arrangements
and prints the raw bytes, the split lines, and the parsed kind for every line.

Read-only with respect to motion: the only command sent is STOP.  That one is
kept because the car must not be left running.

Usage: python3 _pi_enc_probe.py
"""

import atexit
import fcntl
import os
import sys
import time

import serial

sys.path.insert(0, "/home/pi/robogame-runtime")

from rg_runtime.protocols import parse_chassis_reply  # noqa: E402

LOCK = "/run/lock/robogame-chassis.lock"
DEV = "/dev/robogame-chassis"
BAUD = 9600

CASES = [
    ("SPD alone", b"SPD\r\n"),
    ("ENC alone", b"ENC\r\n"),
    ("ENC RESET alone", b"ENC RESET\r\n"),
    ("SPD then ENC, back to back", b"SPD\r\nENC\r\n"),
    ("ENC then SPD, back to back", b"ENC\r\nSPD\r\n"),
]


def open_chassis():
    last = None
    for attempt in range(1, 13):
        if not os.path.exists(DEV):
            print(f"  [{attempt}] {DEV} absent; waiting", flush=True)
            time.sleep(2.0)
            continue
        try:
            ser = serial.Serial(DEV, BAUD, timeout=0.05)
        except Exception as exc:
            last = exc
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
        except Exception as exc:
            last = exc
            try:
                ser.close()
            except Exception:
                pass
        time.sleep(2.0)
    raise RuntimeError(f"chassis link never stabilised: {last!r}")


def collect(ser, seconds):
    """Raw bytes plus the lines they split into, exactly as the transport does."""
    end = time.time() + seconds
    buf = bytearray()
    while time.time() < end:
        try:
            waiting = ser.in_waiting
            if waiting:
                buf.extend(ser.read(waiting))
        except Exception as exc:
            return bytes(buf), [], f"link error: {exc}"
    lines = [raw.rstrip(b"\r").decode("ascii", "replace") for raw in bytes(buf).split(b"\n") if raw]
    return bytes(buf), lines, None


def main():
    ser, lock = open_chassis()
    atexit.register(lambda: ser.is_open and (ser.write(b"STOP\r\n"), ser.flush()))
    print("chassis open, lock held\n", flush=True)

    try:
        for label, payload in CASES:
            ser.write(b"STOP\r\n")
            ser.flush()
            time.sleep(0.3)
            ser.read(256)
            ser.reset_input_buffer()

            print(f"=== {label}   {payload!r} ===", flush=True)
            ser.write(payload)
            ser.flush()
            raw, lines, error = collect(ser, 1.0)
            print(f"  raw  : {raw!r}", flush=True)
            if error:
                print(f"  ERROR: {error}", flush=True)
            for line in lines:
                reply = parse_chassis_reply(line)
                kind = getattr(reply, "kind", None)
                value = getattr(reply, "value", None)
                print(f"  line : {line!r}\n         -> kind={kind!r} value={value!r}", flush=True)
            if not lines:
                print("  (no complete line arrived within 1.0 s)", flush=True)
            print(flush=True)
            time.sleep(0.5)
    finally:
        try:
            ser.write(b"STOP\r\n")
            ser.flush()
            time.sleep(0.2)
        except Exception:
            pass
        try:
            ser.close()
        except Exception:
            pass
        try:
            fcntl.flock(lock.fileno(), fcntl.LOCK_UN)
            lock.close()
        except Exception:
            pass
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
