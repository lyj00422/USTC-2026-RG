"""Why can't OpenCV open the camera?  Read-only, sends no chassis command.

Run on the Pi via _pi_run_file.py:

    python pi-tools\\_pi_run_file.py pi-tools\\_pi_cam_index_probe.py

Three layers, so the failing one is unambiguous:

1. kernel open(2) of each node -- raw errno, no OpenCV involved
2. kernel-level streaming (v4l2-ctl --stream-mmap) -- does the device deliver frames
3. cv2.VideoCapture with the route's calibrated settings

Written 2026-09-23, when `cv2.VideoCapture` failed on every index while
`v4l2-ctl -d /dev/video0 --list-formats` answered normally.
"""

import os
import subprocess
import sys
import time

sys.path.insert(0, "/home/pi/robogame-runtime")


def raw_open_probe():
    print("--- 1. raw os.open(2) ---")
    for path in ("/dev/video0", "/dev/video1"):
        for flags, name in ((os.O_RDWR, "O_RDWR"), (os.O_RDONLY, "O_RDONLY")):
            try:
                fd = os.open(path, flags)
            except OSError as exc:
                print(f"  {path:14s} {name:8s} FAIL errno={exc.errno} {exc.strerror}")
            else:
                print(f"  {path:14s} {name:8s} OK")
                os.close(fd)


def streaming_probe():
    print("--- 2. kernel streaming (v4l2-ctl) ---")
    cmd = [
        "v4l2-ctl", "-d", "/dev/video1",
        "--set-fmt-video=width=1280,height=720,pixelformat=MJPG",
        "--stream-mmap", "--stream-count=5",
    ]
    try:
        out = subprocess.run(cmd, capture_output=True, text=True, timeout=25)
    except subprocess.TimeoutExpired:
        print("  TIMEOUT while streaming")
        return
    print(f"  rc={out.returncode}")
    for line in (out.stdout + out.stderr).strip().splitlines()[-8:]:
        print(f"  {line}")


def cv2_probe():
    print("--- 3. cv2 with calibrated settings ---")
    import cv2

    from rg_runtime.config import load_camera_config
    from run_route_v2 import _configure_camera

    cfg = load_camera_config("config/camera_config.yaml")
    print(f"  config device={cfg.camera} size={cfg.width}x{cfg.height} fmt={cfg.pixel_format}")
    for idx in range(0, 4):
        cap = cv2.VideoCapture(idx)
        if not cap.isOpened():
            print(f"  index {idx}: NOT opened")
            cap.release()
            continue
        try:
            warnings = _configure_camera(cap, cfg, cv2)
        except RuntimeError as exc:
            print(f"  index {idx}: opened, configure failed: {exc}")
            cap.release()
            continue
        ok = 0
        shape = None
        for _ in range(10):
            got, frame = cap.read()
            if got and frame is not None:
                ok += 1
                shape = frame.shape
            time.sleep(0.01)
        print(f"  index {idx}: OPENED warnings={warnings} frames={ok}/10 shape={shape}")
        cap.release()


def main():
    raw_open_probe()
    streaming_probe()
    cv2_probe()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
