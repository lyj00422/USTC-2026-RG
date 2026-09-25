"""Measure the real per-tick cost of RouteRunner.tick()'s camera work.

Route V2 reads the camera and runs the AprilTag detector inside every tick.
If that costs far more than config.poll_period_s (50 ms), the junction
debouncer needs 3 x (effective tick period) of consecutive all-black reads,
which can be longer than the junction itself.

Read-only.  No chassis commands.
"""

import sys
import time

sys.path.insert(0, "/home/pi/robogame-runtime")

import cv2

from rg_runtime.apriltag import AprilTagDetector
from rg_runtime.config import load_camera_config

print("opening camera 0 ...", flush=True)
cap = cv2.VideoCapture(0)
print("camera opened:", cap.isOpened(), flush=True)

detector = None
try:
    detector = AprilTagDetector(load_camera_config("/home/pi/robogame-runtime/config/camera_config.yaml"))
    print("AprilTag detector loaded", flush=True)
except Exception as exc:
    print(f"AprilTag detector unavailable: {type(exc).__name__}: {exc}", flush=True)

read_times, detect_times, totals = [], [], []
for index in range(25):
    start = time.monotonic()
    ok, frame = cap.read()
    after_read = time.monotonic()
    if ok and detector is not None:
        detector.detect(frame, timestamp_ns=int(time.time() * 1e9))
    end = time.monotonic()
    read_ms = (after_read - start) * 1000
    detect_ms = (end - after_read) * 1000
    total_ms = (end - start) * 1000
    read_times.append(read_ms)
    detect_times.append(detect_ms)
    totals.append(total_ms)
    print(f"  tick[{index:02d}] frame_ok={ok} read={read_ms:6.1f}ms detect={detect_ms:6.1f}ms total={total_ms:6.1f}ms", flush=True)

cap.release()
if totals:
    print(f"\npoll_period_s configured: 0.05 (50 ms)", flush=True)
    print(f"mean total per tick: {sum(totals)/len(totals):.1f} ms", flush=True)
    print(f"max  total per tick: {max(totals):.1f} ms", flush=True)
    print(f"=> 3-frame junction confirmation takes about {3*max(totals)/1000:.2f} s worst case", flush=True)
