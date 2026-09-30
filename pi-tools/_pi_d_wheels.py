"""ONE `D 0 0 90 80`, then read the four wheels before and after.

Run ON the Pi via _pi_run_file.py.

Answers exactly two questions:
  1. what the firmware replies (DONE, or ERR TIMEOUT POSITION, or nothing)
  2. did the four wheels each turn, and by how much

The counters are read through TWO separate link sessions.  That is not
decoration: on 2026-09-30 the rfcomm fd died ~8-11 s after the D was sent (the
maintainer rebuilt the tty), and a single-session script lost the "after"
reading every time.  Each session takes the port lock, because an unlocked open
measures the maintainer's teardown cycle rather than the chassis.

Per-wheel deltas are raw counts; the yaw projection and its degrees use the
route's own calibration:

    yaw = (lf - rf - lr + rr) / 4        (run_route_v2.py, unchanged)
    yaw_counts_per_deg = 37.1            (i.e. 3338 counts == 90 degrees)
"""
import fcntl
import os
import re
import time

import serial

PORT = "/dev/robogame-chassis"
LOCK = "/run/lock/robogame-chassis.lock"
COMMAND = os.environ.get("RG_D_CMD", "D 0 0 90 80")
WATCH_S = 14.0
YAW_COUNTS_PER_DEG = 37.1


def live_link(attempts=10):
    """Held lock + live port, or None."""
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
                if ser.read(256).decode("ascii", "replace").strip().startswith("SPD"):
                    return fd, ser
            ser.close()
        except Exception as exc:                              # noqa: BLE001
            print("   link attempt %d: %s" % (i, exc))
        finally:
            try:
                fcntl.flock(fd, fcntl.LOCK_UN)
                os.close(fd)
            except Exception:                                 # noqa: BLE001
                pass
        time.sleep(1.5)
    return None


def read_enc(ser):
    """(lf, rf, lr, rr) or None, via a labelled ENC reply."""
    try:
        ser.reset_input_buffer()
        ser.write(b"ENC\r\n")
        ser.flush()
        time.sleep(0.5)
        raw = ser.read(256).decode("ascii", "replace").strip()
    except Exception as exc:                                  # noqa: BLE001
        return None, "ENC failed: %s" % exc
    values = {label: int(number) for label, number in
              re.findall(r"(LF|RF|LR|RR)\s+(-?\d+)", raw)}
    if len(values) == 4:
        return (values["LF"], values["RF"], values["LR"], values["RR"]), raw
    return None, raw


def yaw(raw):
    lf, rf, lr, rr = raw
    return (lf - rf - lr + rr) / 4.0


print("=== session 1: before, the command, the replies ===")
found = live_link()
if found is None:
    raise SystemExit("ABORT: no live chassis link; nothing was sent")
lock_fd, ser = found
before = None
try:
    before, raw_before = read_enc(ser)
    print("ENC before : %s" % raw_before)
    if before:
        print("yaw before : %.1f counts" % yaw(before))
    t0 = time.monotonic()
    print("--- send at 0.000: %s" % COMMAND)
    ser.write((COMMAND + "\r\n").encode("ascii"))
    ser.flush()
    end = time.monotonic() + WATCH_S
    died = False
    while time.monotonic() < end and not died:
        try:
            raw = ser.read(256)
        except Exception as exc:                              # noqa: BLE001
            print("   %6.3f  fd died (%s)" % (time.monotonic() - t0, exc))
            died = True
            break
        if raw:
            print("   %6.3f  rx  %r" % (time.monotonic() - t0, raw))
        time.sleep(0.02)
    if not died:
        print("   (no reply for %.0fs)" % WATCH_S)
finally:
    try:
        ser.close()
    except Exception:                                         # noqa: BLE001
        pass
    try:
        fcntl.flock(lock_fd, fcntl.LOCK_UN)
        os.close(lock_fd)
    except Exception:                                         # noqa: BLE001
        pass

time.sleep(2.0)
print("=== session 2: after ===")
found = live_link()
if found is None:
    raise SystemExit("ABORT: could not reopen the link; the 'after' reading is missing")
lock_fd, ser = found
try:
    for _ in range(20):
        ser.write(b"STOP\r\n")
        ser.flush()
        time.sleep(0.05)
        try:
            ser.read(256)
        except Exception:                                     # noqa: BLE001
            break
    after, raw_after = read_enc(ser)
    print("ENC after  : %s" % raw_after)
    ser.reset_input_buffer()
    ser.write(b"SPD\r\n")
    ser.flush()
    time.sleep(0.5)
    print("SPD after  : %s" % ser.read(256).decode("ascii", "replace").strip())
    if before and after:
        deltas = [a - b for a, b in zip(after, before)]
        print("--- per wheel ---")
        for label, delta in zip(("LF", "RF", "LR", "RR"), deltas):
            print("   %s  %+6d counts" % (label, delta))
        dyaw = yaw(after) - yaw(before)
        print("--- rotation ---")
        print("   yaw delta  %+.1f counts  ==  %+.1f deg   (commanded +90 = left)"
              % (dyaw, dyaw / YAW_COUNTS_PER_DEG))
finally:
    try:
        ser.close()
    except Exception:                                         # noqa: BLE001
        pass
    try:
        fcntl.flock(lock_fd, fcntl.LOCK_UN)
        os.close(lock_fd)
    except Exception:                                         # noqa: BLE001
        pass
print("link released")
