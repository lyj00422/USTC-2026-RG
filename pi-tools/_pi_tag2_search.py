"""Strafe right from J1 looking for AprilTag 2, and report where it appears.

This is the measurement that decides the J1 -> J2 odometer window, and the one
that can invalidate the redesign outright.

Why it cannot be calculated: the camera looks along the car's nose (extrinsics
rotation 0) with roughly a 60 degree horizontal field implied by the calibration
(fx 1107.7 px over 1280 px).  Parked at J1 the tag is not in that field at all --
the operator reports the camera cannot see it until the car has strafed a while.
How far is field geometry, not a distance calibration, so it has to be measured.

Two other things this run answers, both of which change the plan if they go the
wrong way:

  * what the mask actually does during the strafe.  The redesign assumes the
    old mask sequence (10000000 -> 00000000 -> 00000001) is unusable because the
    all-black reading turns into 00000001/00000011 as soon as the car is
    crooked.  The trace here either supports that or refutes it.
  * the frame size the camera really opens at.  The runtime bug is that
    cv2.VideoCapture(0) never applies the 1280x720 calibration, and a 640x480
    frame against a 1280x720 intrinsic matrix puts any pose -- and therefore the
    distance at which the tag appears -- on the wrong scale.

Motion is `V 0 -<speed> 0`: a held lateral velocity, so STOP interrupts it (that
is measured; whether STOP interrupts a D is not) and no thread is needed because
V does not block.  V's lateral sign is measured too -- negative vy strafes right.

Safety: no motion at all if the line sensor is not delivering readings, an
odometer ceiling that stops and reports rather than guessing, and STOP on every
exit path.

Usage:
    python3 _pi_tag2_search.py
    python3 _pi_tag2_search.py --speed 20 --max-cm 150 --target 2
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
from rg_runtime.apriltag import AprilTagDetector  # noqa: E402
from rg_runtime.app_support import load_runtime_config  # noqa: E402
from rg_runtime.config import load_camera_config  # noqa: E402
from rg_runtime.tag_tracker import TagTracker  # noqa: E402

LOCK = "/run/lock/robogame-chassis.lock"
DEV = "/dev/robogame-chassis"
BAUD = 9600
RUNTIME_CONFIG = "/home/pi/robogame-runtime/config/runtime.yaml"
CAMERA_CONFIG = "/home/pi/robogame-runtime/config/camera_config.yaml"
# Same lateral projection as _pi_strafe.py: (LF + RF - LR - RR) / 4, positive =
# left.  Derived from commanded D distances, so it absorbs D's overshoot; used
# here on a V strafe, where it has never been verified.
LATERAL_COUNTS_PER_CM = 56.8
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


def bits(mask):
    return format(mask & 0xFF, "08b")


class Link:
    """Owns the chassis port and the flock that keeps the maintainer off it."""

    def __init__(self):
        self.lock_fd = None
        self.ser = None

    def open(self):
        self.lock_fd = open(LOCK, "w")
        fcntl.flock(self.lock_fd, fcntl.LOCK_EX)
        last = None
        for _ in range(20):
            try:
                self.ser = serial.Serial(DEV, BAUD, timeout=0.05)
                self.ser.reset_input_buffer()
                return
            except Exception as exc:      # noqa: BLE001 - retry any open failure
                last = exc
                time.sleep(1.0)
        raise RuntimeError(f"could not open {DEV}: {last}")

    def send(self, line):
        if self.ser is None:
            return
        self.ser.write(line.encode() + b"\r\n")
        self.ser.flush()

    def drain(self, seconds):
        deadline = time.monotonic() + seconds
        buf = b""
        while time.monotonic() < deadline:
            buf += self.ser.read(4096)
        return buf

    def reopen(self, *, attempts=30):
        """Rebuild the port after a drop.  True if it came back.

        This chassis drops the RFCOMM link every 10-20 s when its own power is
        unhappy (measured 2026-09-15), and the maintainer service rebuilds
        /dev/robogame-chassis each time.  The old fd goes stale and every read
        raises.  Nothing here reconnects for its own sake -- see the caller: a
        drop ABORTS the run, because it also means the car has been driving on a
        stale command for however long the link was down.
        """
        try:
            self.ser.close()
        except Exception:                 # noqa: BLE001 - already broken
            pass
        self.ser = None
        for _ in range(attempts):
            try:
                self.ser = serial.Serial(DEV, BAUD, timeout=0.05)
                self.ser.reset_input_buffer()
                return True
            except Exception:             # noqa: BLE001 - keep retrying
                time.sleep(1.0)
        return False

    def stop_hard(self):
        """STOP, repeated.  A single STOP is measurably not enough on this
        chassis -- one was lost and the car kept moving for 2.9 s."""
        for _ in range(3):
            try:
                self.send("STOP")
            except Exception:             # noqa: BLE001 - best effort on the way out
                pass
            time.sleep(0.03)


def abort_on_drop(link, exc, right_cm):
    """The link went away mid-strafe.  STOP as soon as it is back, then abort.

    Aborting rather than resuming is deliberate.  V is a held velocity, so for
    however long the link was down the chassis kept strafing on the last
    command -- the distance it actually travelled is unknown, and a measurement
    with an unknown gap in it is worth nothing.  The failure this exists to
    prevent is a run that crashes and leaves the car driving.
    """
    where = "an unknown distance" if right_cm is None else f"{right_cm:.1f} cm"
    print(f"\n  !! LINK DROPPED at {where}: {exc}", flush=True)
    print("  !! the chassis is still holding the last V command.  Reopening, then STOPping.", flush=True)
    if link.reopen():
        link.stop_hard()
        print("  !! STOP sent after reconnect.  ABORTING -- the distance is void.", flush=True)
    else:
        print("  !! could NOT reopen the port.  The car may still be moving: CUT POWER.", flush=True)
    return 2


def open_camera():
    import cv2

    config = load_camera_config(CAMERA_CONFIG)
    capture = cv2.VideoCapture(config.camera)
    if not capture.isOpened():
        raise RuntimeError(f"camera {config.camera} did not open")
    # Ask for the calibrated stream.  The runtime path does not, and a 640x480
    # frame against a 1280x720 matrix is the whole reason this probe reports the
    # frame size it actually got.
    capture.set(cv2.CAP_PROP_FOURCC, cv2.VideoWriter_fourcc(*config.pixel_format))
    capture.set(cv2.CAP_PROP_FRAME_WIDTH, config.width)
    capture.set(cv2.CAP_PROP_FRAME_HEIGHT, config.height)
    capture.set(cv2.CAP_PROP_FPS, config.fps)
    capture.set(cv2.CAP_PROP_BUFFERSIZE, 1)
    for _ in range(10):                   # first frames after opening are garbage
        capture.read()
        time.sleep(0.03)
    return cv2, capture, config


def main():
    signal.signal(signal.SIGTERM, _on_sigterm)

    speed = int(arg("--speed", 20, int))
    max_cm = arg("--max-cm", 150.0)
    min_cm = arg("--min-cm", 0.0)
    confirm = int(arg("--confirm", 3, int))
    target = int(arg("--target", 2, int))
    hold_s = arg("--hold-s", 6.0)

    cfg = load_runtime_config(RUNTIME_CONFIG)
    line = LineSensorService(
        transport=cfg.line_transport, rx_gpio=cfg.line_rx_gpio, tx_gpio=cfg.line_tx_gpio,
        baudrate=cfg.line_baudrate, mode=cfg.line_frame_mode, active_level=cfg.line_active_level,
        reverse_order=cfg.line_reverse_order, enabled=cfg.line_enabled,
        request_command=cfg.line_request_command, startup_delay_s=0.0,
        request_retry_s=cfg.line_request_retry_s,
    )
    link = Link()
    capture = None
    try:
        line.start()
        # Register before the first poll: a crash after start() leaves the soft
        # UART claimed inside pigpiod, and every later run then fails with a
        # misleading "already open in another program".
        atexit.register(line.close)
        if not line.snapshot.connected:
            print(f"line sensor NOT connected: {line.snapshot.error}")
            return 1

        link.open()
        print(f"chassis {DEV} open, lock held")

        cv2, capture, camera_config = open_camera()
        detector = AprilTagDetector(camera_config, allowed_ids=range(1, 7))
        tracker = TagTracker(target_id=target, confirm_frames=confirm)

        # Refuse to drive with no line data.  The strafe itself does not use the
        # line, but a dead sensor means the run is unobservable and the car is
        # being moved blind.
        for _ in range(25):
            if line.poll_once()["sensor_mask"] is not None:
                break
            time.sleep(0.05)
        else:
            print("line sensor is not delivering frames; not moving")
            return 1

        link.drain(0.15)
        link.send("ENC RESET")
        time.sleep(0.2)
        link.drain(0.2)

        print(f"\nstrafing RIGHT at vy=-{speed}, ceiling {max_cm:.0f} cm, "
              f"looking for tag {target} ({confirm} consecutive frames)\n")
        print(f"  {'t':>6} {'mask':>9} {'x1..x8':>9} {'right_cm':>9}  tag")
        print("  " + "-" * 56)

        t0 = time.monotonic()
        base = None
        last_v = -1e9
        last_query = -1e9
        query_enc = True
        first_seen = None
        stable_at = None
        frame_size = None
        mask_run = []
        result = tracker.update(())      # nothing seen yet, before the first frame

        while True:
            now = time.monotonic()
            if STOP_REQUESTED:
                print("\nSIGTERM: stopping")
                break

            state = line.poll_once()
            mask = state["sensor_mask"]

            ok, frame = capture.read()
            if ok:
                if frame_size is None:
                    height, width = frame.shape[:2]
                    frame_size = (width, height)
                    print(f"  frame size {width}x{height}; calibration is "
                          f"{camera_config.width}x{camera_config.height}")
                found = detector.detect(frame, timestamp_ns=time.monotonic_ns())
                result = tracker.update(found)

            # Odometer.  The lateral projection, because the car is moving
            # sideways and its forward projection stays at zero.
            right_cm = None
            try:
                while True:
                    chunk = link.ser.read(4096)
                    if not chunk:
                        break
                    text = chunk.decode("ascii", "replace")
                    match = ENC_RE.search(text)
                    if match:
                        lf, rf, lr, rr = (int(match.group(i)) for i in range(1, 5))
                        lateral = (lf + rf - lr - rr) / 4.0
                        if base is None:
                            base = lateral
                        right_cm = -(lateral - base) / LATERAL_COUNTS_PER_CM
            except serial.SerialException as exc:
                return abort_on_drop(link, exc, right_cm)

            if result.observation is not None and first_seen is None:
                first_seen = (now - t0, right_cm)

            if mask is not None and (not mask_run or mask_run[-1][1] != mask):
                mask_run.append((now - t0, mask))

            tag_note = ""
            if result.observation is not None and frame_size is not None:
                cx, cy = result.observation.center_px
                # Bearing off the optical axis, positive = tag is to the right of
                # where the camera is looking.  Valid only for the frame width it
                # is measured against, which is why the frame size is reported.
                bearing = (cx - frame_size[0] / 2.0) / camera_config.camera_matrix[0][0]
                tag_note = (f"{target} c=({cx:.0f},{cy:.0f}) "
                            f"b={math.degrees(math.atan(bearing)):+.1f}d "
                            f"n={result.consecutive_frames}")
            print(f"  {now - t0:6.2f} {'' if mask is None else mask:>9} "
                  f"{'' if mask is None else bits(mask):>9} "
                  f"{'' if right_cm is None else f'{right_cm:9.1f}'}  {tag_note}", flush=True)

            arrived = (result.stable and right_cm is not None and right_cm >= min_cm)
            overrun = right_cm is not None and right_cm >= max_cm

            if arrived or overrun:
                # Hold the velocity at zero and let the car settle, sampling the
                # tag and the mask, so the log shows the pose it actually
                # finished in rather than the one at the instant of decision.
                if arrived:
                    stable_at = (now - t0, right_cm)
                print(f"\n  >> {'TAG SEEN' if arrived else 'CEILING'}: "
                      f"stopping at {right_cm:.1f} cm (t={now - t0:.2f}s)")
                quiet_until = now + hold_s
                try:
                    link.send("V 0 0 0")
                except serial.SerialException as exc:
                    return abort_on_drop(link, exc, right_cm)
                link.stop_hard()
                while time.monotonic() < quiet_until:
                    ok, frame = capture.read()
                    if ok:
                        found = detector.detect(frame, timestamp_ns=time.monotonic_ns())
                        result = tracker.update(found)
                    state = line.poll_once()
                    mask = state["sensor_mask"]
                    print(f"  {time.monotonic() - t0:6.2f} "
                          f"{'' if mask is None else mask:>9} "
                          f"{'' if mask is None else bits(mask):>9} "
                          f"{'':>9}  settled  tag_stable={result.stable}", flush=True)
                    time.sleep(0.1)
                break

            # One command per tick: the firmware answers only the first of a
            # burst, so a V and an ENC on the same tick would lose one.
            try:
                if now - last_v >= 0.25:
                    link.send(f"V 0 {-abs(speed)} 0")
                    last_v = now
                elif now - last_query >= 0.15:
                    link.send("ENC" if query_enc else "SPD")
                    query_enc = not query_enc
                    last_query = now
            except serial.SerialException as exc:
                return abort_on_drop(link, exc, right_cm)

            time.sleep(0.05)

        # ---- summary -------------------------------------------------------
        print("\n=== summary ===")
        if frame_size is not None:
            agree = (frame_size == (camera_config.width, camera_config.height))
            print(f"frame {frame_size[0]}x{frame_size[1]} vs calibration "
                  f"{camera_config.width}x{camera_config.height}"
                  f"{'' if agree else '   <-- MISMATCH: tag range/bearing are wrong'}")
        if first_seen is None:
            print(f"tag {target} was NEVER detected in this run")
        else:
            when, where = first_seen
            location = "" if where is None else f", {where:.1f} cm right of the start"
            print(f"tag {target} first detected at {when:.2f}s{location}")
        if stable_at is None:
            print(f"tag {target} never reached {confirm} consecutive frames")
        else:
            print(f"tag {target} STABLE ({confirm} frames) at {stable_at[0]:.2f}s, "
                  f"{stable_at[1]:.1f} cm right of the start")
            print(f"  -> tag2_search_min_cm should be this minus the ~3 cm the "
                  f"odometer samples at; the ceiling wants the same margin again.")

        print("\nmask profile (only changes are listed):")
        for ts, mask in mask_run:
            print(f"  {ts:6.2f}s  {mask:3d}  {bits(mask)}")
        return 0
    finally:
        try:
            link.stop_hard()
        except Exception:                 # noqa: BLE001 - best effort on the way out
            pass
        if capture is not None:
            capture.release()
        try:
            line.close()
        except Exception:                 # noqa: BLE001 - best effort on the way out
            pass
        if link.ser is not None:
            link.ser.close()


if __name__ == "__main__":
    raise SystemExit(main())
