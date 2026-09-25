"""Per-frame line-sensor trace with timestamps.

Reuses the real LineSensorService so the parsing path is byte-for-byte the one
run_route_v2 uses (including the malformed-frame tolerance).  Logs EVERY parsed
frame -- not just the last one per read -- so the duration of a 0x00 burst is
measured at frame resolution rather than poll resolution.

This is the measurement that decides the junction algorithm: a plain 5 cm line
and a real junction both read 0x00, so the only usable discriminator is how long
the burst lasts and where it sits in the route.

Read-only: no chassis commands, no motion.

Usage:
    python3 _pi_line_trace.py <label> [seconds] [--poll-ms N]
Writes /home/pi/traces/<label>.csv and prints change events live.
"""

import atexit
import os
import sys
import time

sys.path.insert(0, "/home/pi/robogame-runtime")

from control_hub.services.line_service import LineSensorService  # noqa: E402
from rg_runtime.app_support import load_runtime_config  # noqa: E402

CONFIG = "/home/pi/robogame-runtime/config/runtime.yaml"
OUT_DIR = "/home/pi/traces"


class TracingLineService(LineSensorService):
    """LineSensorService that hands back every frame instead of only the last."""

    def poll_frames(self):
        if not self.snapshot.connected:
            return []
        try:
            if (self._serial is not None or self._pigpio is not None) and not self._streaming and self.clock() >= self._next_request_at:
                self._request_stream()
            data = self.reader() if self.reader is not None else self._read_serial()
            if not data:
                return []
            now_ns = time.monotonic_ns()
            states = self._parse_frames(bytes(data), timestamp_ms=now_ns // 1_000_000)
            if states:
                self._streaming = True
                self.snapshot.line_state = states[-1]
                self.snapshot.state = "LOST" if states[-1].line_lost else "FOLLOWING"
                self.snapshot.error = None
            return [(now_ns, s) for s in states]
        except Exception as exc:
            self.snapshot.connected = False
            self.snapshot.state = "FAULT"
            self.snapshot.error = str(exc)
            return []


def bits(mask):
    return "".join(str((mask >> (7 - i)) & 1) for i in range(8))


def main():
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    label = args[0] if args else "trace"
    seconds = float(args[1]) if len(args) > 1 else 60.0
    poll_ms = 2.0
    if "--poll-ms" in sys.argv:
        poll_ms = float(sys.argv[sys.argv.index("--poll-ms") + 1])

    cfg = load_runtime_config(CONFIG)
    service = TracingLineService(
        transport=cfg.line_transport, rx_gpio=cfg.line_rx_gpio, tx_gpio=cfg.line_tx_gpio,
        baudrate=cfg.line_baudrate, mode=cfg.line_frame_mode, active_level=cfg.line_active_level,
        reverse_order=cfg.line_reverse_order, enabled=cfg.line_enabled,
        request_command=cfg.line_request_command, startup_delay_s=0.0,
        request_retry_s=cfg.line_request_retry_s,
    )
    service.start()
    if not service.snapshot.connected:
        print(f"FATAL: sensor not connected: {service.snapshot.state} {service.snapshot.error}", flush=True)
        return 2

    # A crash must still release the pigpio claim.  Without this, one unhandled
    # exception leaves the soft-UART claimed inside pigpiod and every later run
    # fails with a misleading "already open in another program".
    atexit.register(service.close)

    os.makedirs(OUT_DIR, exist_ok=True)
    path = os.path.join(OUT_DIR, f"{label}.csv")

    t0 = time.monotonic()
    print(f"label={label} duration={seconds:.0f}s poll={poll_ms:.1f}ms -> {path}", flush=True)
    print("   t_s     mask  bits(x1..x8)  err   lost", flush=True)
    print("  (waiting for stream...)", flush=True)

    n_frames = 0
    n_polls = 0
    prev_mask = None
    prev_t = None
    malformed_at_start = service.malformed_frames
    # (start_s, end_s, mask) for every maximal run of one mask value.
    runs = []

    with open(path, "w", buffering=1) as fh:
        fh.write("t_s,mask,bits,line_error,line_lost\n")
        while True:
            now = time.monotonic()
            if now - t0 >= seconds:
                break
            frames = service.poll_frames()
            n_polls += 1
            for ts_ns, st in frames:
                n_frames += 1
                t = (ts_ns / 1e9) - t0
                err = "NA" if st.line_error is None else f"{st.line_error:.2f}"
                fh.write(f"{t:.6f},{st.sensor_mask},{bits(st.sensor_mask)},{err},{int(bool(st.line_lost))}\n")
                if st.sensor_mask != prev_mask:
                    if prev_mask is not None:
                        runs.append((prev_t, t, prev_mask))
                    held = "" if prev_t is None else f"  (prev held {t - prev_t:6.3f}s)"
                    print(f"{t:7.3f}  {st.sensor_mask:3d}   {bits(st.sensor_mask)}   {err:>5}  {int(bool(st.line_lost))}{held}", flush=True)
                    prev_t = t
                    prev_mask = st.sensor_mask
            time.sleep(poll_ms / 1000.0)
        if prev_mask is not None:
            runs.append((prev_t, time.monotonic() - t0, prev_mask))

    service.close()
    dur = time.monotonic() - t0
    print(f"\n=== {label} ===", flush=True)
    print(f"frames={n_frames} polls={n_polls} duration={dur:.1f}s", flush=True)
    print(f"frame rate={n_frames / dur:.1f} Hz   poll rate={n_polls / dur:.1f} Hz", flush=True)
    print(f"malformed frames={service.malformed_frames - malformed_at_start}", flush=True)

    # The junction question in one table: how long does each mask actually last?
    print("\n--- runs of 0x00 (all-black) ---", flush=True)
    blacks = [r for r in runs if r[2] == 0]
    if not blacks:
        print("  (none)", flush=True)
    for a, b, _ in sorted(blacks, key=lambda r: -(r[1] - r[0])):
        print(f"  {a:8.3f}s -> {b:8.3f}s   lasted {1000 * (b - a):8.1f} ms", flush=True)

    print("\n--- runs of 0xFF (all probes off the line) ---", flush=True)
    losts = [r for r in runs if r[2] == 0xFF]
    if not losts:
        print("  (none)", flush=True)
    for a, b, _ in sorted(losts, key=lambda r: -(r[1] - r[0]))[:15]:
        print(f"  {a:8.3f}s -> {b:8.3f}s   lasted {1000 * (b - a):8.1f} ms", flush=True)

    print("\n--- longest 12 runs overall (any mask) ---", flush=True)
    for a, b, m in sorted(runs, key=lambda r: -(r[1] - r[0]))[:12]:
        print(f"  mask={m:3d} {bits(m)}  {a:8.3f} -> {b:8.3f}   {1000 * (b - a):8.1f} ms", flush=True)

    print(f"\ncsv: {path}  ({os.path.getsize(path)} bytes)", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
