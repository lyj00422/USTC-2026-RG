"""Settle the chassis sign conventions with short single-axis pulses.

Two independent signals, so the answer does not rest on eyeballing the robot:

  * ENC deltas -- the firmware's own encoder counts, one field per wheel.
  * line-sensor drift -- the car is parked on the line, so if it strafes right
    the line appears to move left across the probe array (toward x1), and the
    mask shifts accordingly.  That is precisely the mapping the PID needs.

Every pulse is short and slow, and the whole run is wrapped so STOP is sent
even on an exception or Ctrl-C.

Usage:
    python3 _pi_dir_test.py [--vy N] [--pulse S] [--mode vy|d|probe]
"""

import atexit
import fcntl
import os
import re
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


def arg(name, default, cast=float):
    if name in sys.argv:
        return cast(sys.argv[sys.argv.index(name) + 1])
    return default


def bits(mask):
    return "".join(str((mask >> (7 - i)) & 1) for i in range(8)) if mask is not None else "--------"


def projections(reply):
    """The three orthogonal encoder modes, from an `ENC LF a RF b LR c RR d` reply.

    The wheels are mounted mirrored, so the four counts and their mean say
    nothing on their own (route_v2.md section 5.1):

        forward  (-LF + RF - LR + RR) / 4      58.8 counts/cm
        lateral  ( LF + RF - LR - RR) / 4      56.8 counts/cm, positive = left
        yaw      ( LF + RF + LR + RR) / 4      the SUM; positive = left rotation

    The yaw term is the sum, and getting that wrong is easy.  The mirrored
    mounting means the raw counts are (-p_LF, +p_RF, -p_LR, +p_RR) for physical
    wheel speeds p, so substituting into the standard mecanum model gives
    yaw = (LF+RF+LR+RR)/4 in raw counts.  A previous version of this function
    used (-LF+RF+LR-RR)/4, which is orthogonal to all three real modes -- it is
    identically zero for any motion the chassis can actually make, so it read a
    few counts of encoder noise and that noise was reported as yaw.

    Confirmed 2026-09-14: V 0 0 +20 spins the car left (operator: "from the rear,
    first left then right") and the four raw counts were +746 +740 +747 +735, a
    sum of +742.
    """
    fields = dict(re.findall(r"(LF|RF|LR|RR)\s+(-?\d+)", reply))
    if len(fields) != 4:
        return "projections: ENC reply unreadable"
    lf, rf, lr, rr = (int(fields[key]) for key in ("LF", "RF", "LR", "RR"))
    return (f"projections: forward {(-lf + rf - lr + rr) / 4:8.1f}   "
            f"lateral {(lf + rf - lr - rr) / 4:8.1f}   "
            f"yaw {(lf + rf + lr + rr) / 4:8.1f}")


class LineWatcher(threading.Thread):
    """Samples the line sensor in the background and records every change."""

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
        self.t0 = None
        self.events = []
        self._stop = False

    def run(self):
        self.service.start()
        # Register before the first poll: a crash anywhere after start() would
        # otherwise leave the soft UART claimed inside pigpiod, and every later
        # run then fails with a misleading "already open in another program".
        atexit.register(self.service.close)
        if not self.service.snapshot.connected:
            print(f"line sensor NOT connected: {self.service.snapshot.error}", flush=True)
            return
        self.t0 = time.monotonic()
        prev = None
        while not self._stop:
            st = self.service.poll_once()
            mask = st["sensor_mask"]
            if mask is not None and mask != prev:
                self.events.append((time.monotonic() - self.t0, mask))
                prev = mask
            time.sleep(0.004)
        self.service.close()

    def stop(self):
        self._stop = True
        self.join(timeout=3)

    def now_mask(self):
        return self.events[-1] if self.events else (0.0, None)

    def mark(self, label, t_rel):
        """Snapshot the mask at a labelled moment."""
        mask = None
        for ts, m in self.events:
            if ts <= t_rel:
                mask = m
            else:
                break
        print(f"    [{label}] t={t_rel:6.3f}s  mask={mask}  {bits(mask)}", flush=True)
        return mask


class Chassis:
    def __init__(self):
        self.lock = None
        self.ser = self._open_stable()

    @staticmethod
    def _probe(ser, acks_needed=2, budget=6.0):
        acks = 0
        deadline = time.time() + budget
        while time.time() < deadline and acks < acks_needed:
            ser.write(b"STOP\r\n")
            ser.flush()
            time.sleep(0.4)
            if b"OK" in ser.read(256):
                acks += 1
        return acks >= acks_needed

    def _open_stable(self, attempts=12):
        """Open the chassis TTY, prove it works, and only then take the lock.

        Order matters.  The maintainer only tests the lock at the top of its
        loop: while the lock is held it will never run `rfcomm connect`.  Taking
        the lock before the device exists therefore deadlocks -- the link is
        gone, and the one service that could rebuild it is holding off because
        we asked it to.  So: wait for the device, open it, prove it answers,
        and take the lock last.
        """
        last = None
        for attempt in range(1, attempts + 1):
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
                if not self._probe(ser):
                    raise RuntimeError("no reply to STOP probe")
                lock = open(LOCK, "a+")
                fcntl.flock(lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                if not self._probe(ser, acks_needed=1, budget=3.0):
                    raise RuntimeError("link died just after taking the lock")
                self.lock = lock
                print(f"  chassis link stable + lock held (attempt {attempt})", flush=True)
                ser.reset_input_buffer()
                return ser
            except Exception as exc:
                last = exc
                if self.lock is not None:
                    fcntl.flock(self.lock.fileno(), fcntl.LOCK_UN)
                    self.lock.close()
                    self.lock = None
                try:
                    ser.close()
                except Exception:
                    pass
            time.sleep(2.0)
        raise RuntimeError(f"chassis link never stabilised: {last!r}")

    def send(self, cmd, settle=0.0):
        self.ser.write(cmd.encode("ascii") + b"\r\n")
        self.ser.flush()
        time.sleep(settle)
        return self.drain()

    def drain(self, seconds=0.35):
        end = time.time() + seconds
        out = b""
        while time.time() < end:
            data = self.ser.read(256)
            if data:
                out += data
        return out.decode("ascii", "replace").strip()

    def close(self):
        try:
            self.send("STOP", 0.2)
        except Exception:
            pass
        try:
            self.ser.close()
        except Exception:
            pass
        try:
            fcntl.flock(self.lock.fileno(), fcntl.LOCK_UN)
            self.lock.close()
        except Exception:
            pass


def main():
    mode = "vy"
    if "--mode" in sys.argv:
        mode = sys.argv[sys.argv.index("--mode") + 1]
    vy = int(arg("--vy", 8))
    pulse = arg("--pulse", 0.25)

    watcher = LineWatcher()
    watcher.start()
    time.sleep(1.5)

    chassis = Chassis()
    atexit.register(chassis.close)

    t0 = watcher.t0 or time.monotonic()
    rel = lambda: time.monotonic() - t0

    print("\n=== probe: does the chassis answer, and what does ENC look like? ===", flush=True)
    print(f"  STOP  -> {chassis.send('STOP', 0.3)!r}", flush=True)
    print(f"  ENC   -> {chassis.send('ENC', 0.3)!r}", flush=True)
    print(f"  SPD   -> {chassis.send('SPD', 0.3)!r}", flush=True)
    print(f"  ENC RESET -> {chassis.send('ENC RESET', 0.3)!r}", flush=True)
    print(f"  ENC   -> {chassis.send('ENC', 0.3)!r}", flush=True)

    if mode == "probe":
        chassis.close()
        watcher.stop()
        return 0

    if mode == "dfwd":
        # Forward distance move: confirms the D sign convention for the first
        # field AND calibrates encoder counts per centimetre of travel.
        trials = [("D +forward", f"D {vy} 0 0 20")]
    elif mode == "vy":
        trials = [
            (f"V 0 +{vy} 0", f"V 0 {vy} 0"),
            (f"V 0 -{vy} 0", f"V 0 {-vy} 0"),
        ]
    elif mode == "axes":
        # Decisive comparison, and it does not need anyone to watch the car.
        #
        # V 20 0 0 is known forward: a run on 2026-09-14 drove the car up the
        # line and the operator confirmed it.  If V 0 0 20 produces the same raw
        # wheel pattern, it is the same motion.  The three projections cannot
        # answer this -- an all-four-equal pattern is orthogonal to all three, so
        # forward, lateral and yaw all read ~0 no matter how far the car went.
        trials = [
            (f"V +{vy} 0 0   (known forward)", f"V {vy} 0 0"),
            (f"V 0 0 +{vy}   (wz?)", f"V 0 0 {vy}"),
            (f"V 0 +{vy} 0   (known left)", f"V 0 {vy} 0"),
        ]
    elif mode == "wz":
        # The one axis never measured.  line_control.LinePidController pairs
        # wz = -0.5 * correction with the lateral term, purely by inference; and
        # wz is the only actuator that can straighten a car that was placed
        # crooked, which is the case the operator keeps hitting ("it looks
        # straight but it is crooked").  Spin in place, no forward motion.
        trials = [
            (f"V 0 0 +{vy}", f"V 0 0 {vy}"),
            (f"V 0 0 -{vy}", f"V 0 0 {-vy}"),
        ]
    else:  # mode == "d"
        trials = [
            ("D +right", f"D 0 {vy} 0 20"),
            ("D -right", f"D 0 {-vy} 0 20"),
        ]

    for label, cmd in trials:
        print(f"\n=== {label}   ({cmd!r}) ===", flush=True)
        chassis.send("ENC RESET", 0.3)
        before = watcher.mark("before", rel())

        if mode in ("d", "dfwd"):
            # D is a closed-loop distance move: interrupting it with STOP after
            # a fixed pulse would measure the interruption, not the move.  Let
            # the firmware finish and report DONE.
            print(f"  >> {cmd}   (waiting for DONE)", flush=True)
            chassis.send(cmd, settle=0.1)
            collected = ""
            deadline = time.time() + 10.0
            while time.time() < deadline:
                collected += chassis.drain(0.2)
                if "DONE" in collected or "ERR" in collected:
                    break
            print(f"  << {collected.strip()!r}", flush=True)
        else:
            print(f"  >> {cmd}   (pulse {pulse}s)", flush=True)
            chassis.send(cmd, settle=pulse)
            print(f"  >> STOP", flush=True)
            chassis.send("STOP", 0.3)

        time.sleep(0.6)
        after = watcher.mark("after", rel())
        enc = chassis.send("ENC", 0.4)
        print(f"  ENC -> {enc!r}", flush=True)
        print(f"  {projections(enc)}", flush=True)
        print(f"  mask {before} -> {after}", flush=True)

        time.sleep(2.0)

    chassis.close()
    watcher.stop()

    print("\n=== line-sensor change timeline (t, mask, bits) ===", flush=True)
    for ts, m in watcher.events:
        print(f"  {ts:8.3f}s  {m:3d}  {bits(m)}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
