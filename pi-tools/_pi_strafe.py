"""Strafe the chassis laterally with D, in chunks, reporting what actually happened.

Two hard constraints shape this, both measured on 2026-09-14:

1. `D` is open loop -- the firmware computes a run time and never reads the
   encoders -- and it reports only once, at the end.  A 280 cm strafe at speed
   35 therefore produces 19.85 s of total silence on the RFCOMM link, which is
   the JDY-31's idle-out window: the link died the moment the car stopped and
   the ENC reply was lost.  Splitting the move into chunks shorter than that
   window keeps the link alive and yields a distance reading per chunk.

2. A single 280 cm strafe measured -16524.5 lateral counts = 290.9 cm by the
   nominal 56.8 counts/cm, i.e. D overshoots its request by about 4%.  Reported
   per chunk so the drift is visible rather than discovered at the far end.

Signs, measured and confirmed against the operator's own eyes:
    D right field > 0  ->  car moves LEFT
    D right field < 0  ->  car moves RIGHT
    lateral projection (LF + RF - LR - RR) / 4, positive = LEFT
so --right 280 sends `D 0 -280 <rot> <speed>`.

No motion is sent at all if the line sensor is not delivering readings, and
every exit path sends STOP.

Usage:
    python3 _pi_strafe.py --right 280 [--speed 35] [--chunk 140] [--rotate 0]
    python3 _pi_strafe.py --right 280 --no-stop-on-line
    python3 _pi_strafe.py --right 360 --stop-on-mask 1
    python3 _pi_strafe.py --right 320 --speed 100 --chunk 320 --stop-on-black-edge

`--stop-on-mask M` halts on the first frame reading exactly M, with no
"the line must have been lost first" gate.  `--target M` cannot express that:
it only reports the first reading after an 0xFF, so a mask that appears while
the car is still crossing the black area is invisible to it.
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
NOMINAL_LATERAL_COUNTS_PER_CM = 56.8   # hypothesis: derived from a commanded D
# Speed 35 strafes at ~14.1 cm/s (280 cm in 19.85 s), so 140 cm is ~10 s: well
# inside the JDY-31 idle-out window, with room for the reply round trip.  40 cm
# keeps any overshoot past a discovered line small enough to back up.
DEFAULT_CHUNK_CM = 40
D_REPLY_TIMEOUT_S = 45.0
LOST_MASK = 0xFF                       # every probe off the line

# The sensor thread and the main thread both write to the same serial port, and
# the sensor thread's whole job is to write STOP at an arbitrary instant.  A
# single lock keeps a STOP from landing in the middle of a command or an ENC
# request, which would corrupt the reply parsing.
CHASSIS_WRITE_LOCK = threading.Lock()


def bits(mask):
    return "".join(str((mask >> (7 - i)) & 1) for i in range(8)) if mask is not None else "--------"


def left_the_black_area(mask):
    """The 00000001 / 00000011 / 00000111 family, as the operator described it.

    Bit 7 is printed first and is x1, the leftmost probe.  A 0 there means x1
    is still on black; at least one white probe is what makes the reading a
    transition rather than more of the same.  All-black (0x00) is excluded
    because the car sits on it for the first couple of metres.
    """
    return mask != 0 and (mask & 0x80) == 0


def arg(name, default, cast=float):
    return cast(sys.argv[sys.argv.index(name) + 1]) if name in sys.argv else default


class LineWatcher(threading.Thread):
    """Samples the line sensor in the background and records every change.

    With a stop_predicate it also *acts*: the instant a reading matches, it
    writes STOP to the chassis itself.  That has to happen on this thread.  A
    D move blocks the calling thread for its whole duration, so a check made
    there can only ever run after the car has already finished travelling --
    which is precisely the case this exists to avoid.
    """

    def __init__(self, stop_predicate=None):
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
        self.stop_predicate = stop_predicate
        self.ser = None
        self.armed = False
        self.seen_all_black = False
        self.tripped = None

    def run(self):
        self.service.start()
        # Register before the first poll: a crash after start() would otherwise
        # leave the soft UART claimed inside pigpiod, and every later run then
        # fails with a misleading "already open in another program".
        atexit.register(self.service.close)
        if not self.service.snapshot.connected:
            print(f"line sensor NOT connected: {self.service.snapshot.error}", flush=True)
            return
        prev = None
        while not self._stop:
            st = self.service.poll_once()
            mask = st["sensor_mask"]
            if mask is not None and mask != prev:
                ts = time.monotonic() - self.t0
                self.events.append((ts, mask))
                prev = mask
                self._check_stop(mask, ts)
            time.sleep(0.004)
        self.service.close()

    def arm(self, ser):
        """Begin watching for the criterion.  Deliberately not armed at start-up.

        The predicate is broad -- any reading that is not all-black, with the
        leftmost probe on black -- and the car passes through readings like
        0x01 while the operator is positioning it by hand.  Arming is a
        separate act, done once the car is in place and about to move.

        seen_all_black starts from the current reading so that a car which is
        already sitting on the black area does not have to re-enter it: the
        criterion is "has left the black", and being on it already counts.
        """
        self.ser = ser
        self.armed = True
        self.tripped = None
        self.seen_all_black = self.latest() == 0x00

    def disarm(self):
        self.armed = False

    def _check_stop(self, mask, ts):
        if self.stop_predicate is None or self.tripped is not None:
            return
        if mask == 0x00:
            self.seen_all_black = True
            return
        if not (self.armed and self.seen_all_black and self.stop_predicate(mask)):
            return
        self.tripped = (ts, mask)
        if self.ser is None:
            return
        with CHASSIS_WRITE_LOCK:
            # Repeated, not once: a single STOP is not enough on this chassis
            # (the route runner re-sends it every 200 ms for the same reason),
            # and the firmware may be mid-move when the first one lands.
            for _ in range(3):
                try:
                    self.ser.write(b"STOP\r\n")
                    self.ser.flush()
                except Exception:
                    pass
                time.sleep(0.03)

    def stop(self):
        self._stop = True
        self.disarm()
        self.join(timeout=3)

    def latest(self):
        return self.events[-1][1] if self.events else None

    def since(self, index):
        return self.events[index:]


def open_chassis():
    """Open the TTY, prove it answers, and take the lock LAST.

    The maintainer only tests the lock at the top of its loop, so while the lock
    is held it never runs `rfcomm connect`.  Taking the lock before the device
    exists deadlocks the link.
    """
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
            return ser, lock
        except Exception as exc:
            last = exc
            try:
                ser.close()
            except Exception:
                pass
        time.sleep(2.0)
    raise RuntimeError(f"chassis link never stabilised: {last!r}")


def link_alive(ser):
    """One cheap round trip.  A dropped RFCOMM link otherwise looks exactly
    like a car that will not move."""
    try:
        ser.reset_input_buffer()
        ser.write(b"SPD\r\n")
        ser.flush()
        time.sleep(0.3)
        return "SPD" in ser.read(256).decode("ascii", "replace")
    except Exception:
        return False


def drain(ser, seconds):
    try:
        end = time.time() + seconds
        out = b""
        while time.time() < end:
            data = ser.read(256)
            if data:
                out += data
        return out.decode("ascii", "replace")
    except Exception as exc:
        # The link can die mid-read; that must not abort the run, or the mask
        # trace that explains where the car went is lost with it.
        return f"\n[link error: {exc}]"


def read_enc(ser, attempts=4):
    """The four signed wheel counts, or None if the link is not answering."""
    for _ in range(attempts):
        try:
            with CHASSIS_WRITE_LOCK:
                ser.reset_input_buffer()
                ser.write(b"ENC\r\n")
                ser.flush()
            buf = b""
            deadline = time.time() + 0.5
            while time.time() < deadline:
                buf += ser.read(256)
                for line in buf.decode("ascii", "replace").splitlines():
                    if line.startswith("ENC "):
                        f = dict(re.findall(r"(LF|RF|LR|RR)\s+(-?\d+)", line))
                        if len(f) == 4:
                            return {k: int(v) for k, v in f.items()}
                time.sleep(0.005)
        except Exception:
            return None
    return None


def lateral_counts(enc):
    """Positive = the car moved LEFT (the same sign convention as the D field)."""
    return (enc["LF"] + enc["RF"] - enc["LR"] - enc["RR"]) / 4.0


def main():
    # int() throughout: the firmware parses integers and rejects anything else
    # outright ("D 0 -10.0 0 25" -> ERR), and a rejected D looks exactly like a
    # car that will not move.
    if "--left" in sys.argv:
        right_cm = -int(arg("--left", 0))
    else:
        right_cm = int(arg("--right", 40))
    speed = int(arg("--speed", 35))
    rotate = int(arg("--rotate", 0))
    chunk = int(arg("--chunk", DEFAULT_CHUNK_CM))
    max_cm = arg("--max-cm", 400.0)
    stop_on_line = "--no-stop-on-line" not in sys.argv
    # Optional exact mask.  By default the run stops at the FIRST line that
    # reappears after the car has been off the line, and reports which mask it
    # was.  Waiting for one exact value would drive the car straight past the
    # target and off the end of the table if the target's real pattern differs
    # by a single probe.
    target_mask = int(arg("--target", -1))
    # Stop on the first frame that reads exactly this mask, with no "the line
    # must have been lost first" gate.  --target cannot express that: it only
    # ever reports the first reading after an 0xFF, so a mask that shows up
    # while the car is still on the black area is invisible to it.
    stop_on_mask = int(arg("--stop-on-mask", -1))
    # Stop the moment the bar comes off the all-black area with its leftmost
    # probe still on black -- the 00000001 / 00000011 / 00000111 family.
    # Unlike --stop-on-mask this one has to act DURING the move, so it is
    # handled by the sensor thread (see LineWatcher._check_stop), and it turns
    # the default line trigger off rather than competing with it: the default
    # fires on the very same transition, one chunk later, at a position that is
    # already stale by a whole chunk.
    stop_on_black_edge = "--stop-on-black-edge" in sys.argv
    if stop_on_black_edge:
        stop_on_line = False

    if target_mask != -1 and not 0 <= target_mask <= 0xFF:
        print("target must be 0..255", flush=True)
        return 2
    if stop_on_mask != -1 and not 0 <= stop_on_mask <= 0xFF:
        print("stop-on-mask must be 0..255", flush=True)
        return 2
    if speed <= 0 or speed > 100:
        print("speed must be 1..100", flush=True)
        return 2
    if chunk <= 0:
        print("chunk must be positive", flush=True)
        return 2
    if abs(right_cm) > 400 or abs(rotate) > 360:
        print("refusing: |--right| > 400 cm or |--rotate| > 360 deg", flush=True)
        return 2

    direction = "RIGHT" if right_cm > 0 else "LEFT"
    remaining = abs(right_cm)
    # The D field is named "right" but a positive value moves the car LEFT.
    sign = -1 if right_cm > 0 else 1

    watcher = LineWatcher(
        stop_predicate=left_the_black_area if stop_on_black_edge else None)
    watcher.start()
    time.sleep(1.5)
    ser, lock = open_chassis()

    def safe_stop():
        try:
            if ser.is_open:
                with CHASSIS_WRITE_LOCK:
                    ser.write(b"STOP\r\n")
                    ser.flush()
        except Exception:
            pass

    atexit.register(safe_stop)

    # atexit does not run on a signal, and a killed run leaves the soft UART
    # claimed inside pigpiod: every later run then fails with a misleading
    # "already open in another program".
    def _on_signal(signum, _frame):
        safe_stop()
        watcher.stop()
        raise SystemExit(128 + signum)

    for _sig in (signal.SIGHUP, signal.SIGTERM, signal.SIGINT):
        try:
            signal.signal(_sig, _on_signal)
        except Exception:
            pass

    def cleanup():
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

    if not watcher.service.snapshot.connected or watcher.latest() is None:
        # Never drive without a working sensor.  An earlier lateral-search
        # script reached its motion loop with a dead line sensor and issued a D
        # move anyway; with no mask there is nothing to stop it and the car just
        # goes.
        print(f"\nABORT: line sensor is not delivering readings "
              f"({watcher.service.snapshot.state} / {watcher.service.snapshot.error}); "
              f"no motion command sent", flush=True)
        cleanup()
        return 3

    start_mask = watcher.latest()
    print(f"\ntarget    : {remaining:.0f} cm to the {direction}, "
          f"rotate {rotate} deg, speed {speed}, chunks of {chunk} cm", flush=True)
    print(f"start mask: {start_mask} {bits(start_mask)}", flush=True)
    print(f"estimated : {remaining / (0.403 * speed):.1f}s of strafing "
          f"(14.1 cm/s at speed 35); chunking keeps the link inside its "
          f"~20s idle-out\n", flush=True)

    with CHASSIS_WRITE_LOCK:
        ser.write(b"ENC RESET\r\n")
        ser.flush()
    time.sleep(0.4)
    drain(ser, 0.15)

    if stop_on_black_edge:
        # Armed only now, with the car in position: the criterion accepts any
        # reading that is not all-black with x1 on black, and the car passes
        # through those while being placed by hand.
        watcher.arm(ser)
        print("edge stop : armed -- STOP fires on the first non-all-black "
              "reading whose leftmost probe is still black (00000001 family)\n",
              flush=True)

    chunks = []
    cumulative = None
    stopped_because = None
    seen_lost = False

    try:
        index = 0
        while remaining > 0:
            index += 1
            step = int(min(chunk, remaining))
            command = f"D 0 {sign * step} {rotate} {speed}\r\n"

            before_mask = watcher.latest()
            mark = len(watcher.events)
            print(f"  [chunk {index}] t={time.monotonic() - watcher.t0:6.2f}s  "
                  f"-> {command.strip()!r}   mask {before_mask} {bits(before_mask)}", flush=True)

            move_started = time.monotonic()
            try:
                with CHASSIS_WRITE_LOCK:
                    ser.write(command.encode("ascii"))
                    ser.flush()
            except Exception as exc:
                print(f"           link died before the move could be sent: {exc}", flush=True)
                stopped_because = "link lost before send"
                break

            # D is a self-terminating distance move: the firmware stops the car
            # itself.  Never interrupt it with STOP -- that would measure the
            # interruption instead of the move, and this chassis has no reliable
            # comms-timeout stop to fall back on.
            collected = ""
            deadline = time.time() + D_REPLY_TIMEOUT_S
            while time.time() < deadline:
                collected += drain(ser, 0.2)
                if "DONE" in collected or "ERR" in collected:
                    break
            elapsed_move = time.monotonic() - move_started
            reply = " ".join(collected.split())
            print(f"           reply : {reply or '(none)'}   after {elapsed_move:.2f}s", flush=True)
            if "ERR" in collected:
                print(f"ABORT: the chassis rejected the move ({reply})", flush=True)
                stopped_because = "chassis rejected the move"
                break
            if not collected.strip():
                # Silence means the link idled out mid-move, not that the move
                # failed: D finishes on its own.  Say so, because a missing reply
                # otherwise reads as a chassis fault.
                print(f"           NOTE  : no reply within {D_REPLY_TIMEOUT_S:.0f}s -- the RFCOMM "
                      f"link most likely idled out. The move self-terminates, so the car has "
                      f"stopped, but the ENC reading below may be unavailable.", flush=True)

            time.sleep(0.5)
            enc = read_enc(ser)
            after_mask = watcher.latest()
            if enc is None:
                print("           ENC   : unreadable (link down)", flush=True)
            else:
                cumulative = lateral_counts(enc)
                cm_nominal = cumulative / NOMINAL_LATERAL_COUNTS_PER_CM
                print(f"           ENC   : LF {enc['LF']:+7d} RF {enc['RF']:+7d} "
                      f"LR {enc['LR']:+7d} RR {enc['RR']:+7d}", flush=True)
                print(f"           moved : {cumulative:+9.1f} counts = {cm_nominal:+7.2f} cm "
                      f"cumulative (nominal {NOMINAL_LATERAL_COUNTS_PER_CM} counts/cm)", flush=True)
            print(f"           mask  : {before_mask} -> {after_mask}  "
                  f"({bits(before_mask)} -> {bits(after_mask)})", flush=True)

            events = watcher.since(mark)
            for ts, mask in events:
                print(f"             {ts:8.3f}s  {mask:3d}  {bits(mask)}", flush=True)

            # Did the armed criterion fire while the move was still running?
            # If it did, the car was stopped by the sensor thread and the
            # distance actually covered is short of this chunk's request --
            # so do not subtract the whole chunk from what is left.
            if watcher.tripped is not None:
                ts, mask = watcher.tripped
                fraction = (0.0 if elapsed_move <= 0
                            else (ts - (move_started - watcher.t0)) / elapsed_move)
                offset = step * min(1.0, max(0.0, fraction))
                covered_before = abs(right_cm) - remaining
                at_cm = covered_before + offset
                print(f"\n  >> EDGE STOP fired mid-move: mask {mask} {bits(mask)} "
                      f"at {offset:.1f} cm into this chunk, i.e. ~{at_cm:.1f} cm "
                      f"from the start", flush=True)
                stopped_because = f"edge stop on mask {mask} {bits(mask)} at ~{at_cm:.1f} cm"
                break

            chunks.append((index, step, elapsed_move, after_mask))
            remaining -= step

            # Direct exact-mask search, checked before the lost-line trigger.
            # Measured 2026-09-15: the J1 -> J2 strafe reads 00000001 twice --
            # once at ~207 cm where the bar's right tip leaves the long black
            # area, and once at ~316 cm inside the J2 line crossing.  Only the
            # second one is accompanied by 129 (the bar centred on a line), so
            # --stop-on-mask 1 stops at the first; use --stop-on-mask with the
            # target read off a trace that shows which one the operator wants.
            if stop_on_mask != -1:
                for ts, mask in events:
                    if mask != stop_on_mask:
                        continue
                    fraction = (0.0 if elapsed_move <= 0
                                else (ts - (move_started - watcher.t0)) / elapsed_move)
                    offset = step * min(1.0, max(0.0, fraction))
                    if cumulative is not None:
                        base = abs(cumulative) / NOMINAL_LATERAL_COUNTS_PER_CM - step
                    else:
                        base = abs(right_cm) - remaining
                    at_cm = base + offset
                    print(f"\n  >> mask {stop_on_mask} {bits(stop_on_mask)} at "
                          f"{offset:.1f} cm into this chunk, i.e. ~{at_cm:.1f} cm "
                          f"from the start", flush=True)
                    stopped_because = f"mask {stop_on_mask} at ~{at_cm:.1f} cm"
                    break
                if stopped_because:
                    break

            # Locate the moment a line came back from the recorded trace, not
            # from the chunk-end reading: a chunk is one to two seconds of
            # travel and the bar is only 7 cm wide, so a 5 cm line can pass
            # entirely between two samples and leave no trace in the endpoint
            # reading at all.  The position inside the chunk is interpolated
            # from the wall-clock fraction, which the per-chunk encoder delta
            # then confirms.
            trigger = None
            for ts, mask in events:
                if mask == LOST_MASK:
                    seen_lost = True
                    continue
                if seen_lost:
                    trigger = (ts, mask)
                    break
            if trigger is not None:
                ts, mask = trigger
                fraction = 0.0 if elapsed_move <= 0 else (ts - (move_started - watcher.t0)) / elapsed_move
                offset = step * min(1.0, max(0.0, fraction))
                # Anchor on the ENCODER-measured distance, not the requested one.
                # D overshoots by 3-4% (measured), so a commanded-cumulative
                # anchor reports a position that is systematically short of
                # where the car actually is.
                if cumulative is not None:
                    base = abs(cumulative) / NOMINAL_LATERAL_COUNTS_PER_CM - step
                else:
                    base = abs(right_cm) - remaining
                at_cm = base + offset
                print(f"\n  >> line reappeared: mask {mask} {bits(mask)} about {offset:.1f} cm "
                      f"into this chunk, i.e. ~{at_cm:.1f} cm from the start", flush=True)
                stopped_because = f"line at ~{at_cm:.1f} cm (mask {mask} {bits(mask)})"
                if not stop_on_line:
                    pass
                elif target_mask != -1 and mask != target_mask:
                    # Exact-match mode: not the mask we are waiting for, so keep
                    # going.  Only safe because it was asked for explicitly, and
                    # the distance guard still bounds the total travel.
                    print(f"     --target {target_mask} was requested; this is not it, "
                          f"continuing\n", flush=True)
                    stopped_because = None
                else:
                    break

            if cumulative is not None and abs(cumulative) / NOMINAL_LATERAL_COUNTS_PER_CM > max_cm:
                stopped_because = f"exceeded the {max_cm:.0f} cm guard"
                print(f"\nABORT: {stopped_because}", flush=True)
                break
            time.sleep(0.4)
    finally:
        safe_stop()
        time.sleep(0.2)
        cleanup()

    print("\n=== result ===", flush=True)
    print(f"commanded : {abs(right_cm)} cm to the {direction}, "
          f"{'completed' if remaining <= 0 else f'{remaining:.0f} cm NOT covered'}", flush=True)
    if cumulative is not None:
        cm_nominal = cumulative / NOMINAL_LATERAL_COUNTS_PER_CM
        covered = abs(right_cm) - remaining
        print(f"measured  : {cm_nominal:+.2f} cm by the nominal constant "
              f"(positive would be LEFT; negative here = went RIGHT)", flush=True)
        if covered:
            print(f"overshoot : {abs(cm_nominal) / covered * 100 - 100:+.1f}% against the "
                  f"{covered:.0f} cm actually requested", flush=True)
    else:
        print("measured  : unavailable -- the ENC reply never came back", flush=True)
    print(f"stopped   : {stopped_because or 'target reached'}", flush=True)
    for index, step, elapsed, mask in chunks:
        print(f"  chunk {index}: {step:4d} cm in {elapsed:5.2f}s  "
              f"({step / elapsed if elapsed else 0:5.2f} cm/s)  final mask {mask} {bits(mask)}", flush=True)

    print("\n--- full mask trace (t, mask, bits) ---", flush=True)
    for ts, mask in watcher.events:
        print(f"  {ts:8.3f}s  {mask:3d}  {bits(mask)}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
