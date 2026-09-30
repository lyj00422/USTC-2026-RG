"""One wheel at a time, open loop: does it turn, and does it STOP when told.

Run ON the Pi via _pi_run_file.py.  Wheels must be off the ground.

Why this and not another `D`: the `D` that wedges the route is a *closed* loop
on the wheel counters, so a single bad channel shows up only as "never
converges".  `M <wheel> <speed>` is open loop -- the firmware just drives that
one channel -- which makes two different faults separable:

  * the channel does not turn at all            -> drive channel dead
  * it turns but does not stop on STOP          -> channel latched (and that
                                                   alone would explain both the
                                                   missing DONE and the wheel
                                                   that keeps spinning on)
  * it turns, stops, and its ENC tracks the
    other wheels                               -> channel fine, look at feedback

ENC is read BEFORE the STOP is sent, and again after, because the interesting
question on this chassis (2026-09-30: "转完 90 度后左前轮一直转") is exactly
what happens between the command ending and the stop landing.  The earlier
probe could not see it: it always stopped first, then measured.
"""
import fcntl
import os
import re
import time

import serial

PORT = "/dev/robogame-chassis"
LOCK = "/run/lock/robogame-chassis.lock"
SPEED = 50
RUN_S = 3.0
WHEELS = ("LF", "RF")


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
    try:
        ser.reset_input_buffer()
        ser.write(b"ENC\r\n")
        ser.flush()
        time.sleep(0.35)
        raw = ser.read(256).decode("ascii", "replace").strip()
    except Exception as exc:                                  # noqa: BLE001
        return None, "ENC failed: %s" % exc
    values = {label: int(number) for label, number in
              re.findall(r"(LF|RF|LR|RR)\s+(-?\d+)", raw)}
    if len(values) == 4:
        return (values["LF"], values["RF"], values["LR"], values["RR"]), raw
    return None, raw


def stop(ser, times=12):
    for _ in range(times):
        try:
            ser.write(b"STOP\r\n")
            ser.flush()
        except Exception:                                     # noqa: BLE001
            return
        time.sleep(0.05)
        try:
            ser.read(256)
        except Exception:                                     # noqa: BLE001
            return


def test_wheel(label):
    print("=" * 64)
    print("=== %s at speed %d, wheels off the ground ===" % (label, SPEED))
    found = live_link()
    if found is None:
        print("   no live link -- skipped")
        return
    lock_fd, ser = found
    try:
        before, raw = read_enc(ser)
        print("ENC before        : %s" % raw)
        stop(ser, 4)
        print("--- send M %s %d" % (label, SPEED))
        ser.write(("M %s %d\r\n" % (label, SPEED)).encode("ascii"))
        ser.flush()
        t0 = time.monotonic()
        samples = []
        while time.monotonic() - t0 < RUN_S:
            time.sleep(0.4)
            enc, _raw = read_enc(ser)
            if enc:
                samples.append((time.monotonic() - t0, enc))
        for when, enc in samples:
            idx = {"LF": 0, "RF": 1, "LR": 2, "RR": 3}[label]
            delta = enc[idx] - before[idx] if before else 0
            print("   +%.1fs  %s=%+6d  (delta %+d)" % (when, label, enc[idx], delta))

        print("--- ENC read BEFORE any STOP (is it still turning?)")
        mid, raw_mid = read_enc(ser)
        print("ENC before stop   : %s" % raw_mid)
        time.sleep(1.0)
        mid2, raw_mid2 = read_enc(ser)
        print("ENC +1s later     : %s" % raw_mid2)

        print("--- now STOP x12")
        stop(ser, 12)
        time.sleep(0.5)
        after, raw_after = read_enc(ser)
        print("ENC after stop    : %s" % raw_after)
        time.sleep(1.0)
        after2, raw_after2 = read_enc(ser)
        print("ENC +1s later     : %s" % raw_after2)

        if before and after2:
            idx = {"LF": 0, "RF": 1, "LR": 2, "RR": 3}[label]
            print("--- verdict for %s ---" % label)
            print("   turned during M : %+d counts" % (mid[idx] - before[idx] if mid else 0))
            print("   moved AFTER stop: %+d counts  (0 = it stops; large = latched)"
                  % (after2[idx] - after[idx] if after else 0))
    finally:
        try:
            stop(ser, 8)
        except Exception:                                     # noqa: BLE001
            pass
        try:
            ser.close()
        except Exception:                                     # noqa: BLE001
            pass
        try:
            fcntl.flock(lock_fd, fcntl.LOCK_UN)
            os.close(lock_fd)
        except Exception:                                     # noqa: BLE001
            pass
        time.sleep(1.0)


for wheel in WHEELS:
    test_wheel(wheel)

print("=" * 64)
print("done; link released, final STOP was sent for each wheel")
