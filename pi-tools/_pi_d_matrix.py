"""Three commands, each with an ENC delta: which moves still complete, and why.

Run ON the Pi via _pi_run_file.py.

`ERR TIMEOUT POSITION` says the firmware gave up on a position move: it did not
reach the target inside its own timeout.  That has two very different causes and
they are separable with the encoder:

  * the car cannot turn fast enough (supply / torque / drag) -> all four wheels
    turn, just too slowly, and the move times out whatever it is;
  * one wheel is not driving (dead motor, driver, or a blocked wheel) -> that
    wheel's delta collapses next to the others, and an in-place rotation, which
    needs both sides pulling opposite ways, crawls.

So each row here is: ENC before, the command, every reply byte, ENC after, and
the per-wheel delta.  Three commands, because a small rotation and a straight
line fail differently from each other:

  A  D 0 0 90 80   the command that wedges the route
  B  D 0 0 20 80   a rotation small enough to finish even when slow
  C  D 10 0 0 20   straight: does any D finish, or only rotations

Each command gets its own lock/open/close, because the tty can go away under the
script (seen 2026-09-30: the fd died 8.5 s after the D) and one dead fd must not
cost the other two measurements.
"""
import fcntl
import os
import time

import serial

PORT = "/dev/robogame-chassis"
LOCK = "/run/lock/robogame-chassis.lock"
WATCH_S = 12.0
COMMANDS = [
    ("A", "D 0 0 90 80", "the command that wedges the route"),
    ("B", "D 0 0 20 80", "small rotation"),
    ("C", "D 10 0 0 20", "straight 10 cm"),
]
WHEELS = ["LF", "RF", "LR", "RR"]


def live_link(attempts=10):
    for i in range(attempts):
        if not os.path.exists(PORT):
            time.sleep(1.0)
            continue
        fd = os.open(LOCK, os.O_RDWR | os.O_CREAT, 0o666)
        fcntl.flock(fd, fcntl.LOCK_EX)
        try:
            if not os.path.exists(PORT):
                continue
            ser = serial.Serial(PORT, 9600, timeout=0.4)
            for _ in range(6):
                ser.write(b"STOP\r\n")
                ser.flush()
                time.sleep(0.05)
                try:
                    ser.read(256)
                except Exception:                             # noqa: BLE001
                    break
            for _ in range(6):
                ser.reset_input_buffer()
                ser.write(b"SPD\r\n")
                ser.flush()
                time.sleep(0.4)
                raw = ser.read(256).decode("ascii", "replace").strip()
                if raw.startswith("SPD"):
                    return fd, ser
            ser.close()
        except Exception as exc:                              # noqa: BLE001
            print("   attempt %d: %s" % (i, exc))
        finally:
            try:
                fcntl.flock(fd, fcntl.LOCK_UN)
                os.close(fd)
            except Exception:                                 # noqa: BLE001
                pass
        time.sleep(1.5)
    return None


def enc(ser):
    """Four raw counts, or None.  Never raises on a dead fd."""
    try:
        ser.reset_input_buffer()
        ser.write(b"ENC\r\n")
        ser.flush()
        time.sleep(0.5)
        raw = ser.read(256).decode("ascii", "replace").strip()
    except Exception as exc:                                  # noqa: BLE001
        return None, "ENC failed: %s" % exc
    numbers = []
    for part in raw.replace(",", " ").split():
        try:
            numbers.append(int(part))
        except ValueError:
            pass
    numbers = numbers[-4:] if len(numbers) >= 4 else []
    return (numbers or None), raw


for tag, command, note in COMMANDS:
    print("=" * 68)
    print("%s  %-14s  (%s)" % (tag, command, note))
    found = live_link()
    if found is None:
        print("   no live link -- skipped")
        continue
    lock_fd, ser = found
    t0 = time.monotonic()
    try:
        before, raw_before = enc(ser)
        print("   ENC before : %s" % raw_before)
        print("   --- send at 0.000")
        try:
            ser.write((command + "\r\n").encode("ascii"))
            ser.flush()
        except Exception as exc:                              # noqa: BLE001
            print("   write failed: %s" % exc)
        end = time.monotonic() + WATCH_S
        died = False
        while time.monotonic() < end and not died:
            try:
                raw = ser.read(256)
            except Exception as exc:                          # noqa: BLE001
                print("   %6.3f  fd died: %s" % (time.monotonic() - t0, exc))
                died = True
                break
            if raw:
                print("   %6.3f  rx  %r" % (time.monotonic() - t0, raw))
            time.sleep(0.02)
        if not died:
            for _ in range(20):
                try:
                    ser.write(b"STOP\r\n")
                    ser.flush()
                except Exception:                             # noqa: BLE001
                    break
                time.sleep(0.05)
                try:
                    ser.read(256)
                except Exception:                             # noqa: BLE001
                    break
            after, raw_after = enc(ser)
            print("   ENC after  : %s" % raw_after)
            if before and after:
                print("   deltas     : %s" % "  ".join(
                    "%s%+d" % (w, a - b) for w, a, b in zip(WHEELS, after, before)))
    finally:
        try:
            ser.close()
        except Exception:                                     # noqa: BLE001
            pass
        try:
            fcntl.flock(lock_fd, fcntl.LOCK_UN)
            os.close(lock_fd)
        except Exception:                                     # noqa: BLE001
            pass

print("=" * 68)
print("all commands finished; the lock is released")
