"""Drive a lateral shift and stop when the line sensor reaches a target reading.

Used to measure the J1 -> J2 shift distance.  The car is placed at J1, where the
bar reads 10000000 (x8, the rightmost probe, sits on the branch).  The firmware
then strafes right while this watches the line sensor; when the reading reaches
00000001 the shift is done and the encoder gives the true distance travelled --
measured, not commanded, so acceleration ramps and wheel slip do not matter.

Signs measured on this chassis 2026-09-14:
    V vy>0  -> car moves LEFT          so a rightward shift needs vy < 0
    D right>0 -> car moves LEFT        (the D field is named "right" but is not)
    lateral encoder projection = (LF + RF - LR - RR) / 4, 56.8 counts/cm,
    positive = leftward.

Every exit path sends STOP.

Usage:
    python3 _pi_shift_search.py [--vy N] [--target MASK] [--max-cm N] [--max-s N]
"""

import atexit
import fcntl
import os
import re
import signal
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
LATERAL_COUNTS_PER_CM = 56.8   # from D 0 10 0 20
RESEND_V_S = 0.25              # keep the link alive and the command fresh
ENC_POLL_S = 0.25


def bits(mask):
    return "".join(str((mask >> (7 - i)) & 1) for i in range(8)) if mask is not None else "--------"


def black_runs(mask):
    values = [(mask >> (7 - i)) & 1 for i in range(8)]
    runs, previous = 0, 1
    for value in values:
        if value == 0 and previous == 1:
            runs += 1
        previous = value
    return runs


def arg(name, default, cast=float):
    return cast(sys.argv[sys.argv.index(name) + 1]) if name in sys.argv else default


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

    def latest(self):
        return self.events[-1][1] if self.events else None


def open_chassis():
    """Open the TTY, prove it answers, and take the lock LAST: the maintainer
    never reconnects while the lock is held, so locking before the device exists
    deadlocks the link."""
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


def drain(ser, seconds):
    end = time.time() + seconds
    out = b""
    while time.time() < end:
        data = ser.read(256)
        if data:
            out += data
    return out.decode("ascii", "replace")


def read_enc(ser):
    """Signed lateral wheel counts (positive = car moved left), or None."""
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
                    f = dict(re.findall(r"(LF|RF|LR|RR)\s+(-?\d+)", line))
                    if len(f) == 4:
                        return (int(f["LF"]) + int(f["RF"]) - int(f["LR"]) - int(f["RR"])) / 4.0
            time.sleep(0.005)
    return None


def main():
    # The firmware parses integers: "D 0 -10.0 0 25" is rejected outright and
    # the car never moves.  Keep these ints.
    step_cm = int(arg("--step", 10))
    speed = int(arg("--speed", 25))
    target = int(arg("--target", 0x01))
    max_cm = arg("--max-cm", 320.0)
    max_s = arg("--max-s", 400.0)
    max_steps = int(arg("--max-steps", 60))

    if step_cm <= 0 or speed <= 0:
        print("step and speed must be positive", flush=True)
        return 2

    watcher = LineWatcher()
    watcher.start()
    time.sleep(1.5)
    ser, lock = open_chassis()

    def safe_stop():
        try:
            if ser.is_open:
                ser.write(b"STOP\r\n")
                ser.flush()
        except Exception:
            pass

    atexit.register(safe_stop)

    # atexit does not run on a signal, and a killed run leaves the soft UART
    # claimed inside pigpiod -- every later run then fails with a misleading
    # "already open in another program".  Release properly on the way out.
    def _on_signal(signum, _frame):
        safe_stop()
        watcher.stop()
        raise SystemExit(128 + signum)

    for _sig in (signal.SIGHUP, signal.SIGTERM, signal.SIGINT):
        try:
            signal.signal(_sig, _on_signal)
        except Exception:
            pass

    if not watcher.service.snapshot.connected or watcher.latest() is None:
        # Never drive without a working sensor.  A previous version reached the
        # motion loop with a dead line sensor and issued a D move anyway; with
        # no mask there is nothing to stop it and the car just goes.
        print(f"\nABORT: line sensor is not delivering readings "
              f"({watcher.service.snapshot.state} / {watcher.service.snapshot.error}); "
              f"no motion command sent", flush=True)
        safe_stop()
        watcher.stop()
        try:
            ser.close()
        except Exception:
            pass
        try:
            fcntl.flock(lock.fileno(), fcntl.LOCK_UN)
            lock.close()
        except Exception:
            pass
        return 3

    start_mask = watcher.latest()
    print(f"\nstart mask = {start_mask} {bits(start_mask)}", flush=True)
    print(f"target      = {target} {bits(target)}   "
          f"steps of {step_cm:.0f} cm right at D speed {speed}   "
          f"guards: {max_cm:.0f} cm / {max_s:.0f} s / {max_steps} steps\n", flush=True)
    if start_mask != 0x80:
        print("WARNING: expected 10000000 (0x80) at J1; proceeding anyway\n", flush=True)

    ser.write(b"ENC RESET\r\n")
    ser.flush()
    time.sleep(0.4)
    ser.reset_input_buffer()

    t0 = time.monotonic()
    lateral_cm = 0.0
    reached_at = None
    trace = []
    prev_mask = start_mask

    try:
        for step in range(1, max_steps + 1):
            elapsed = time.monotonic() - t0
            if elapsed > max_s:
                print(f"\n[TIMEOUT] {max_s:.0f}s elapsed without reaching the target", flush=True)
                break
            if abs(lateral_cm) > max_cm:
                print(f"\n[GUARD] travelled {lateral_cm:.1f} cm without reaching the target", flush=True)
                break

            before = read_enc(ser)
            # Negative D lateral value = move RIGHT (the field is named "right"
            # but a positive number moves the car LEFT).
            command = f"D 0 {-step_cm} 0 {speed}\r\n"
            print(f"  step {step:3d}  t={elapsed:6.2f}s  lateral={lateral_cm:7.2f} cm  "
                  f"-> {command.strip()!r}", flush=True)
            ser.write(command.encode("ascii"))
            ser.flush()

            # D is a self-terminating distance move: the firmware stops the car
            # itself.  Never interrupt it with STOP -- that would measure the
            # interruption instead of the move.  The whole design leans on this,
            # because the chassis firmware has no reliable comms-timeout stop.
            collected = ""
            deadline = time.time() + 10.0
            while time.time() < deadline:
                collected += drain(ser, 0.15)
                if "DONE" in collected or "ERR" in collected:
                    break
            reply = " ".join(collected.split())
            # Print the reply: a rejected command is otherwise invisible, and a
            # silently-ignored D looks exactly like a car that will not move.
            print(f"           reply: {reply or '(none)'}", flush=True)
            if "ERR" in collected:
                print(f"ABORT: the chassis rejected the move ({reply})", flush=True)
                break
            time.sleep(0.3)

            after = read_enc(ser)
            if before is not None and after is not None:
                lateral_cm += (after - before) / LATERAL_COUNTS_PER_CM

            mask = watcher.latest()
            if mask is not None and mask != prev_mask:
                trace.append((time.monotonic() - t0, mask, lateral_cm))
                prev_mask = mask
            print(f"           mask={'-' if mask is None else mask:>3} {bits(mask)}   "
                  f"lateral={lateral_cm:7.2f} cm"
                  f"{'   <-- GARBAGE' if mask is not None and black_runs(mask) >= 3 else ''}",
                  flush=True)

            if mask == target:
                reached_at = (time.monotonic() - t0, lateral_cm)
                break
    finally:
        safe_stop()
        time.sleep(0.2)

    print("\n=== result ===", flush=True)
    print(f"start mask      : {start_mask} {bits(start_mask)}", flush=True)
    print(f"final mask      : {prev_mask} {bits(prev_mask)}", flush=True)
    print(f"lateral travelled: {lateral_cm:.2f} cm  (positive = leftward)", flush=True)
    if reached_at:
        print(f"REACHED {bits(target)} at t={reached_at[0]:.2f}s, "
              f"lateral={reached_at[1]:.2f} cm", flush=True)
        print(f"\nJ1 shift distance = {abs(reached_at[1]):.1f} cm to the RIGHT", flush=True)
        print(f"  -> junction_1_right_cm: {-abs(reached_at[1]):.0f}  "
              f"(negative: the D field named 'right' moves the car LEFT when positive)", flush=True)
    else:
        print("target NOT reached", flush=True)

    print("\n--- full mask trace (t, mask, bits, lateral cm) ---", flush=True)
    for elapsed, mask, cm in trace:
        print(f"  {elapsed:7.2f}s  {mask:3d}  {bits(mask)}  {cm:7.2f} cm", flush=True)

    try:
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
