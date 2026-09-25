"""Measure which way `D ... rotate_deg ...` turns the car, and by how much.

`turn_deg: 90` has never been calibrated, and the rotation SIGN has never been
measured at all.  The tree disagrees with itself about it:

  * this route's plan calls TURN_TO_PURPLE / TURN_TO_BUILD a 左旋 90 at
    turn_deg: 90;
  * the older auto_route_v1 spec says a positive rotate_deg turns RIGHT.

Both cannot be true, so one of those two route states is turning the wrong way
today.  The route now also has two more turns (at tag 2 and at J2) whose
directions matter even more, because a right turn issued as a left is an
immediate loss of the track.

The answer comes from the encoders, not from watching: the yaw projection

    (LF + RF + LR + RR) / 4        the MEAN of the four counts

is positive for a LEFT rotation (verified 2026-09-15 with `V 0 0 +20`, which the
operator confirmed as 先左旋).  So this probe sends one D rotation, reads ENC
before and after, and prints the sign and the magnitude.

That divisor is the mean, not the sum, and the distinction is not cosmetic: the
route's own notes quote `V 0 0 +20` as "+742" for raw counts that add to 2968,
i.e. they are quoting the mean while calling it 和.  Treating it as a sum
inflates every angle by 4x.

Once the sign is known, set `turn_sign` in config/route_v2.yaml: +1 if a positive
rotate_deg turned the car left, -1 if it turned right.  That one value flips
every rotation on the route together.

Two things this deliberately does NOT do, because the first version did and was
wrong:

  * it does not guess the wheelbase.  An early run of this probe assumed (a+b)
    was 8-18 cm -- a small robot -- and reported 186-419 degrees for a turn the
    operator watched come out at exactly 90.  This is a large chassis:
    (a+b) is about 37 cm.  The probe now reports COUNTS PER DEGREE, which needs
    no wheelbase at all, and derives (a+b) from the commanded angle.
  * it does not treat the encoder as the authority on the angle.  The encoder
    says how far the wheels rolled; only the operator can say how far the
    CHASSIS turned.  Confirm by eye -- the two agreeing is the real result.

Sends exactly one motion command, at low speed, and STOPs on every exit path.
Have the car somewhere it cannot hit anything, and be ready to cut power.

Usage:
    python3 _pi_turn_probe.py                 # D 0 0 90 30, the route's own value
    python3 _pi_turn_probe.py --deg 90 --speed 20
    python3 _pi_turn_probe.py --deg -90       # the other direction
"""

import atexit
import fcntl
import math
import re
import signal
import sys
import time

import serial

sys.path.insert(0, "/home/pi/robogame-runtime")

from control_hub.services.line_service import LineSensorService  # noqa: E402
from rg_runtime.app_support import load_runtime_config  # noqa: E402

LOCK = "/run/lock/robogame-chassis.lock"
DEV = "/dev/robogame-chassis"
BAUD = 9600
RUNTIME_CONFIG = "/home/pi/robogame-runtime/config/runtime.yaml"
COUNTS_PER_CM = 57.0
# Anchored on "ENC " on purpose.  The unanchored pattern also matches the
# SPD reply -- "SPD LF 0 RF 0 LR 0 RR 0 OUT 0 0 0 0" -- so a probe that
# alternates ENC and SPD queries reads zeros as real encoder counts.
# Measured 2026-09-15: the odometer column alternated between the true
# distance and ~0, and the baseline could be taken from a zero.
ENC_RE = re.compile(r"ENC\s+LF (-?\d+) RF (-?\d+) LR (-?\d+) RR (-?\d+)")

STOP_REQUESTED = False


def _on_sigterm(_signum, _frame):
    global STOP_REQUESTED
    STOP_REQUESTED = True


def arg(name, default, cast=float):
    return cast(sys.argv[sys.argv.index(name) + 1]) if name in sys.argv else default


def open_link():
    lock_fd = open(LOCK, "w")
    fcntl.flock(lock_fd, fcntl.LOCK_EX)
    last = None
    for _ in range(20):
        try:
            ser = serial.Serial(DEV, BAUD, timeout=0.05)
            ser.reset_input_buffer()
            return lock_fd, ser
        except Exception as exc:          # noqa: BLE001 - retry any open failure
            last = exc
            time.sleep(1.0)
    raise RuntimeError(f"could not open {DEV}: {last}")


def stop_hard(ser):
    """STOP, repeated: a single STOP is measurably not enough on this chassis."""
    for _ in range(3):
        try:
            ser.write(b"STOP\r\n")
            ser.flush()
        except Exception:                 # noqa: BLE001 - best effort on the way out
            pass
        time.sleep(0.03)


def read_encoder(ser, *, timeout_s=2.0):
    """Latest `ENC LF a RF b LR c RR d` reply, or None."""
    ser.write(b"ENC\r\n")
    ser.flush()
    latest = None
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        chunk = ser.read(4096)
        if chunk:
            match = ENC_RE.search(chunk.decode("ascii", "replace"))
            if match:
                latest = tuple(int(match.group(i)) for i in range(1, 5))
        if latest is not None and not chunk:
            break
        time.sleep(0.02)
    return latest


def main():
    signal.signal(signal.SIGTERM, _on_sigterm)

    deg = int(arg("--deg", 90, int))
    speed = int(arg("--speed", 30, int))
    settle_s = arg("--settle-s", 1.0)

    cfg = load_runtime_config(RUNTIME_CONFIG)
    line = LineSensorService(
        transport=cfg.line_transport, rx_gpio=cfg.line_rx_gpio, tx_gpio=cfg.line_tx_gpio,
        baudrate=cfg.line_baudrate, mode=cfg.line_frame_mode, active_level=cfg.line_active_level,
        reverse_order=cfg.line_reverse_order, enabled=cfg.line_enabled,
        request_command=cfg.line_request_command, startup_delay_s=0.0,
        request_retry_s=cfg.line_request_retry_s,
    )
    lock_fd = None
    ser = None
    try:
        line.start()
        atexit.register(line.close)
        if not line.snapshot.connected:
            print(f"line sensor NOT connected: {line.snapshot.error}")
            return 1

        lock_fd, ser = open_link()
        print(f"chassis {DEV} open, lock held")

        for _ in range(25):
            if line.poll_once()["sensor_mask"] is not None:
                break
            time.sleep(0.05)
        else:
            print("line sensor is not delivering frames; not moving")
            return 1

        stop_hard(ser)
        time.sleep(0.2)
        before = read_encoder(ser)
        if before is None:
            print("no ENC reply; cannot measure anything, not moving")
            return 1
        before_mask = line.poll_once()["sensor_mask"]
        print(f"before: ENC LF {before[0]} RF {before[1]} LR {before[2]} RR {before[3]}"
              f"   mask {before_mask}")

        print(f"\nissuing: D 0 0 {deg} {speed}")
        ser.write(f"D 0 0 {deg} {speed}\r\n".encode())
        ser.flush()

        # Wait for the DONE, but not for ever: a D that never reports means the
        # link or the firmware is gone, and the state machine has the same
        # ceiling for the same reason.
        deadline = time.monotonic() + 20.0
        saw_done = False
        buf = b""
        while time.monotonic() < deadline and not STOP_REQUESTED:
            chunk = ser.read(4096)
            if chunk:
                buf += chunk
                if b"DONE" in buf:
                    saw_done = True
                    break
            time.sleep(0.02)
        print(f"DONE {'seen' if saw_done else 'NOT SEEN (timed out)'}")
        stop_hard(ser)

        time.sleep(settle_s)
        after = read_encoder(ser)
        after_mask = line.poll_once()["sensor_mask"]
        if after is None:
            print("no ENC reply after the turn; the counts are lost")
            return 1
        print(f"after : ENC LF {after[0]} RF {after[1]} LR {after[2]} RR {after[3]}"
              f"   mask {after_mask}")

        delta = tuple(a - b for a, b in zip(after, before))
        total = sum(delta)
        # The projection is the MEAN of the four, not their sum: the docs call it
        # "和后取平均" and quote V 0 0 +20 as "+742" for raw counts that add to
        # 2968.  Getting this wrong inflates every angle by 4x.
        yaw = total / 4.0
        print(f"\ndelta : LF {delta[0]:+d} RF {delta[1]:+d} LR {delta[2]:+d} RR {delta[3]:+d}")
        print(f"yaw projection = mean of the four = {yaw:+.1f} counts  (positive = LEFT)")
        print(f"forward projection = {(-delta[0] + delta[1] - delta[2] + delta[3]) / 4.0:+.1f} counts"
              f"   (a clean in-place turn should be near zero)")
        print(f"lateral projection = {(delta[0] + delta[1] - delta[2] - delta[3]) / 4.0:+.1f} counts")

        if total == 0:
            print("\nno rotation measured at all -- the command did not move the car")
            return 1
        direction = "LEFT (逆时针俯视)" if total > 0 else "RIGHT (顺时针俯视)"
        print(f"\n>>> D 0 0 {deg} {speed} turns the car {direction}")
        print(f">>> so a POSITIVE rotate_deg means "
              f"{'LEFT' if (deg > 0) == (total > 0) else 'RIGHT'}")
        print(f">>> set turn_sign: {'+1' if (deg > 0) == (total > 0) else '-1'}"
              f" in config/route_v2.yaml")

        # Counts per degree needs no wheelbase, so it is the honest number to
        # report.  Do NOT guess (a+b) from a plausible size range -- a first
        # version of this probe assumed 8-18 cm and reported 186-419 degrees for
        # a turn the operator watched come out at exactly 90.  Derive (a+b) FROM
        # the commanded angle instead, and say plainly that this assumes the
        # firmware honours it.
        if deg:
            per_deg = abs(yaw) / abs(deg)
            print(f"\ncounts per degree = {per_deg:.1f}"
                  f"   (yaw projection per commanded degree)")
            half_diagonal = per_deg / (COUNTS_PER_CM * math.pi / 180.0)
            print(f"IF the firmware honoured the request exactly, then")
            print(f"  (a + b) = {half_diagonal:.1f} cm"
                  f"   [= counts_per_deg / (k * pi/180), k = {COUNTS_PER_CM:.0f} counts/cm]")
            print(f"\n*** Confirm the angle BY EYE.  Everything above assumes the car")
            print(f"*** really turned {deg} degrees; the encoder can only tell you how")
            print(f"*** far the wheels rolled, not what the chassis did with it.")
        return 0
    finally:
        if ser is not None:
            stop_hard(ser)
        try:
            line.close()
        except Exception:                 # noqa: BLE001 - best effort on the way out
            pass
        if ser is not None:
            ser.close()


if __name__ == "__main__":
    raise SystemExit(main())
