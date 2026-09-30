"""ONE raw `D 0 0 90 80`, with the port lock held, watched at the byte level.

Run ON the Pi via _pi_run_file.py.

The route can only say "no DONE arrived"; it never records the reply stream.
This sends the exact command the wedging state sends, once, and prints every
byte the chassis answers with a millisecond stamp.  No SPD, no ENC and no
heartbeat go out during the watch: the point is what the firmware does on its
own, and whether the route's own polling is what disturbs it.

The link is taken the way the route takes it -- LOCK_EX on
/run/lock/robogame-chassis.lock -- because the RFCOMM maintainer cycles the tty
every ~12-25 s while nothing holds it, and a probe that opens without the lock
measures the maintainer's teardown instead of the chassis.  Attempts release the
lock between tries: a lock held across a teardown window pins a dead link.

Verdict it prints:
  * `DONE` and how long after the command it arrived
  * the ENC delta across the move, so "it turned halfway" is a number
"""
import fcntl
import os
import time

import serial

PORT = "/dev/robogame-chassis"
LOCK = "/run/lock/robogame-chassis.lock"
COMMAND = os.environ.get("RG_D_CMD", "D 0 0 90 80")
WATCH_S = float(os.environ.get("RG_D_WATCH_S", "20"))


def live_link(attempts=8):
    """A held lock + live port, or None.  Releasing between attempts on purpose."""
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
                ser.read(256)
            for _ in range(6):
                ser.reset_input_buffer()
                ser.write(b"SPD\r\n")
                ser.flush()
                time.sleep(0.4)
                raw = ser.read(256).decode("ascii", "replace").strip()
                if raw.startswith("SPD"):
                    print("attempt %d: link live -> %s" % (i, raw))
                    return fd, ser
            ser.close()
        except Exception as exc:                              # noqa: BLE001
            print("attempt %d: %s" % (i, exc))
        finally:
            fcntl.flock(fd, fcntl.LOCK_UN)
            os.close(fd)
        time.sleep(1.5)
    return None


def ask(ser, cmd, wait=0.5):
    ser.reset_input_buffer()
    ser.write((cmd + "\r\n").encode("ascii"))
    ser.flush()
    time.sleep(wait)
    return ser.read(256).decode("ascii", "replace").strip()


found = live_link()
if found is None:
    raise SystemExit("ABORT: could not get a live chassis link; nothing was sent")

lock_fd, ser = found
t0 = time.monotonic()
try:
    def stamp():
        return "%.3f" % (time.monotonic() - t0)

    before = ask(ser, "ENC", 0.6)
    print("ENC before : %s" % before)
    print("--- ONE command at %s: %s" % (stamp(), COMMAND))
    ser.write((COMMAND + "\r\n").encode("ascii"))
    ser.flush()

    end = time.monotonic() + WATCH_S
    while time.monotonic() < end:
        raw = ser.read(256)
        if raw:
            print("  %8s  rx  %r" % (stamp(), raw))
        time.sleep(0.02)
    print("--- %ss elapsed, nothing else was sent ---" % WATCH_S)

    for _ in range(20):
        ser.write(b"STOP\r\n")
        ser.flush()
        time.sleep(0.05)
        ser.read(256)
    print("ENC after  : %s" % ask(ser, "ENC", 0.6))
    print("SPD after  : %s" % ask(ser, "SPD", 0.6))
finally:
    try:
        ser.close()
    finally:
        fcntl.flock(lock_fd, fcntl.LOCK_UN)
        os.close(lock_fd)
print("link released")
