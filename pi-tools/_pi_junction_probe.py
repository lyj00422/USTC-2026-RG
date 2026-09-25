"""Measure how many centimetres of track an all-black (0x00) reading spans.

The junction question is a distance question, not a time question.  Whether the
car sees 0x00 for 60 ms or 300 ms depends only on how fast it is going; how many
millimetres of track produce 0x00 is a property of the course.  So this records
the line sensor and the chassis encoders together while the car is pushed by
hand -- no motor commands are ever sent -- and reports each 0x00 burst as a
distance in centimetres.

Multiply that width by whatever speed the route actually runs at and you get the
0x00 window in milliseconds, which is what the debounce has to fit inside.

Encoder signs (measured on this chassis):
    forward : LF -  RF +  LR -  RR +   (mirrored mounting on the left pair)
so forward travel is mean(-LF, +RF, -LR, +RR), at ~58 counts/cm.

Usage:
    python3 _pi_junction_probe.py [seconds]
"""

import atexit
import fcntl
import os
import re
import statistics
import sys
import threading
import time

import serial

sys.path.insert(0, "/home/pi/robogame-runtime")

from control_hub.services.line_service import LineSensorService  # noqa: E402
from rg_runtime.app_support import load_runtime_config  # noqa: E402

LOCK = "/run/lock/robogame-chassis.lock"
DEV = "/dev/robogame-chassis"
BAUD = 9600
RUNTIME_CONFIG = "/home/pi/robogame-runtime/config/runtime.yaml"

# Calibrated 2026-09-14 by commanding a known distance and reading ENC:
#   D 20 0 0 20 (forward 20 cm): LF -1165 RF +1173 LR -1182 RR +1184
#   D 0 10 0 20 (lateral 10 cm): LF  +564 RF  +573 LR  -567 RR  -568
# The two motion modes use different wheel combinations on a mecanum base, so
# each gets its own projection and its own counts-per-centimetre.
COUNTS_PER_CM = {"forward": 58.8, "lateral": 56.8}
AXIS = "forward"


def bits(mask):
    return "".join(str((mask >> (7 - i)) & 1) for i in range(8)) if mask is not None else "--------"


def black_runs(mask):
    """How many separate runs of black (bit==0) the reading contains.

    A single bar crossing one or two black lines gives one or two runs.  Three
    or more cannot come from the track: it is a bit error in the 115200 soft
    UART, and it parses as a well-formed frame, so `malformed_frames` never
    counts it.  Such a reading is dangerous because it can coincidentally equal
    the junction pattern and advance the route.
    """
    values = [(mask >> (7 - i)) & 1 for i in range(8)]
    runs = 0
    previous = 1
    for value in values:
        if value == 0 and previous == 1:
            runs += 1
        previous = value
    return runs


class LineWatcher(threading.Thread):
    def __init__(self):
        super().__init__(daemon=True)
        cfg = load_runtime_config(RUNTIME_CONFIG)
        self.service = LineSensorService(
            transport=cfg.line_transport, rx_gpio=cfg.line_rx_gpio, tx_gpio=cfg.line_tx_gpio,
            baudrate=cfg.line_baudrate, mode=cfg.line_frame_mode, active_level=cfg.line_active_level,
            reverse_order=cfg.line_reverse_order, enabled=cfg.line_enabled,
            request_command=cfg.line_request_command, startup_delay_s=0.0,
            request_retry_s=cfg.line_request_retry_s,
        )
        self.t0 = time.monotonic()
        self.events = []
        self._stop = False

    def run(self):
        self.service.start()
        atexit.register(self.service.close)
        if not self.service.snapshot.connected:
            print(f"line sensor NOT connected: {self.service.snapshot.error}", flush=True)
            return
        prev = None
        while not self._stop:
            st = self.service.poll_once()
            mask = st["sensor_mask"]
            if mask is not None and mask != prev:
                self.events.append((time.monotonic() - self.t0, mask))
                prev = mask
            time.sleep(0.003)
        self.service.close()

    def stop(self):
        self._stop = True
        self.join(timeout=3)


def open_chassis():
    """Open the TTY, prove it answers, and only then take the lock (see notes
    in _pi_dir_test.py: locking before the device exists deadlocks the
    maintainer, which refuses to reconnect while a consumer holds the lock)."""
    last = None
    for attempt in range(1, 13):
        if not os.path.exists(DEV):
            print(f"  [{attempt}] {DEV} absent; waiting for the maintainer", flush=True)
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
            print(f"  chassis link stable + lock held (attempt {attempt})", flush=True)
            return ser, lock
        except Exception as exc:
            last = exc
            try:
                ser.close()
            except Exception:
                pass
        time.sleep(2.0)
    raise RuntimeError(f"chassis link never stabilised: {last!r}")


def read_enc(ser):
    """One ENC round trip -> signed axis-projected wheel counts, or None.

    Signals are the measured ones.  The left pair reads inverted, so a forward
    push is mean(-LF, +RF, -LR, +RR).  A mecanum strafe drives the wheels in a
    different combination -- measured LF+/RF+/LR-/RR- -- so it needs its own
    projection; using the forward one for a sideways push reads ~0.
    """
    for _ in range(3):
        ser.reset_input_buffer()
        ser.write(b"ENC\r\n")
        ser.flush()
        buf = b""
        deadline = time.time() + 0.4
        while time.time() < deadline:
            buf += ser.read(256)
            for line in buf.decode("ascii", "replace").splitlines():
                if line.startswith("ENC "):
                    fields = dict(re.findall(r"(LF|RF|LR|RR)\s+(-?\d+)", line))
                    if len(fields) == 4:
                        lf, rf = int(fields["LF"]), int(fields["RF"])
                        lr, rr = int(fields["LR"]), int(fields["RR"])
                        if AXIS == "lateral":
                            # Positive = the car moved LEFT (measured with vy>0).
                            return (lf + rf - lr - rr) / 4.0
                        return (-lf + rf - lr + rr) / 4.0
            time.sleep(0.005)
    return None


def interp(samples, t):
    """Linear interpolation of cumulative centimetres at time t."""
    if not samples:
        return None
    if t <= samples[0][0]:
        return samples[0][1]
    if t >= samples[-1][0]:
        return samples[-1][1]
    for (t0, v0), (t1, v1) in zip(samples, samples[1:]):
        if t0 <= t <= t1:
            if t1 == t0:
                return v1
            return v0 + (v1 - v0) * (t - t0) / (t1 - t0)
    return samples[-1][1]


def interp_path(samples, t):
    """Interpolate cumulative PATH LENGTH (sum of |step|), not net displacement."""
    if not samples:
        return None
    if t <= samples[0][0]:
        return samples[0][2]
    if t >= samples[-1][0]:
        return samples[-1][2]
    for (t0, _, p0), (t1, _, p1) in zip(samples, samples[1:]):
        if t0 <= t <= t1:
            if t1 == t0:
                return p1
            return p0 + (p1 - p0) * (t - t0) / (t1 - t0)
    return samples[-1][2]


def main():
    global AXIS
    if "--axis" in sys.argv:
        AXIS = sys.argv[sys.argv.index("--axis") + 1]
        if AXIS not in COUNTS_PER_CM:
            print(f"unknown axis {AXIS!r}; use forward or lateral", flush=True)
            return 2
    positional = [a for a in sys.argv[1:] if not a.startswith("--") and a != AXIS]
    seconds = float(positional[0]) if positional else 120.0

    watcher = LineWatcher()
    watcher.start()
    time.sleep(1.5)
    ser, lock = open_chassis()

    def _safe_stop():
        try:
            if ser.is_open:
                ser.write(b"STOP\r\n")
                ser.flush()
        except Exception:
            pass

    atexit.register(_safe_stop)

    print(f"\nRECORDING for {seconds:.0f}s, axis={AXIS} "
          f"({COUNTS_PER_CM[AXIS]} counts/cm) -- push the car by hand now.\n"
          f"Go slowly and steadily; no motor commands will be sent.\n", flush=True)

    t0 = watcher.t0
    samples = []          # (t_s, net_cm, path_cm)
    origin = read_enc(ser)
    if origin is None:
        print("WARNING: no ENC readings; distance will be unavailable", flush=True)
    last_report = 0.0
    prev_cm = 0.0
    path_cm = 0.0

    while time.monotonic() - t0 < seconds:
        raw = read_enc(ser)
        if raw is not None and origin is not None:
            net_cm = (raw - origin) / COUNTS_PER_CM[AXIS]
            if samples:
                path_cm += abs(net_cm - prev_cm)
            prev_cm = net_cm
            samples.append((time.monotonic() - t0, net_cm, path_cm))
        now = time.monotonic() - t0
        # Report immediately as well, so the starting position is visible before
        # anything is pushed -- a run started from the wrong pose is wasted.
        if last_report == 0.0 or now - last_report >= 5.0:
            last_report = now
            net_cm = samples[-1][1] if samples else 0.0
            mask = watcher.events[-1][1] if watcher.events else None
            print(f"  t={now:6.1f}s  net={net_cm:7.1f} cm  path={path_cm:7.1f} cm  mask={mask} {bits(mask)}", flush=True)

    print("\n=== recording finished ===", flush=True)
    print(f"net displacement: {samples[-1][1]:.1f} cm   path length: {path_cm:.1f} cm   "
          f"enc samples: {len(samples)}   line events: {len(watcher.events)}", flush=True)

    events = watcher.events
    runs = [(events[i][0], events[i + 1][0], events[i][1]) for i in range(len(events) - 1)]
    if events:
        runs.append((events[-1][0], time.monotonic() - t0, events[-1][1]))

    def width_of(a, b):
        pa, pb = interp_path(samples, a), interp_path(samples, b)
        if pa is None or pb is None:
            return "     ?    "
        return f"{abs(pb - pa):6.2f} cm"

    print("\n--- every 0x00 (all eight probes on black) burst, as a PATH DISTANCE ---", flush=True)
    blacks = [r for r in runs if r[2] == 0]
    if not blacks:
        print("  (none seen)", flush=True)
    for a, b, _ in blacks:
        print(f"  t {a:8.3f} -> {b:8.3f}s   lasted {1000 * (b - a):7.1f} ms   path {width_of(a, b)}", flush=True)

    print("\n--- every 0xFF (all probes off the line) burst ---", flush=True)
    losts = [r for r in runs if r[2] == 0xFF]
    if not losts:
        print("  (none seen)", flush=True)
    for a, b, _ in losts:
        print(f"  t {a:8.3f} -> {b:8.3f}s   lasted {1000 * (b - a):7.1f} ms   path {width_of(a, b)}", flush=True)

    print("\n--- readings that cannot come from the track (>=3 black runs) ---", flush=True)
    junk = [(ts, m) for ts, m in events if black_runs(m) >= 3]
    print(f"  {len(junk)} of {len(events)} readings", flush=True)
    for ts, m in junk[:40]:
        print(f"  {ts:8.3f}s  {m:3d}  {bits(m)}  ({black_runs(m)} runs)", flush=True)
    if len(junk) > 40:
        print(f"  ... and {len(junk) - 40} more", flush=True)

    print("\n--- every mask change (t, mask, bits, path cm) ---", flush=True)
    for ts, mask in events:
        pc = interp_path(samples, ts)
        flag = "  <-- GARBAGE" if black_runs(mask) >= 3 else ""
        print(f"  {ts:8.3f}s  {mask:3d}  {bits(mask)}  {'' if pc is None else f'{pc:7.2f} cm'}{flag}", flush=True)

    try:
        ser.write(b"STOP\r\n")
        ser.flush()
        ser.close()
    except Exception:
        pass
    try:
        fcntl.flock(lock.fileno(), fcntl.LOCK_UN)
        lock.close()
    except Exception:
        pass
    watcher.stop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
