"""Which formats actually stream?  Read-only, sends no chassis command.

    python pi-tools\\_pi_run_file.py pi-tools\\_pi_cam_stream_sweep.py

Separates "the camera cannot stream at all" from "only the 720p MJPG mode the
route needs cannot stream" -- the two point at different hardware faults.
Each attempt is bounded, because a wedged UVC endpoint hangs the ioctl.

2026-09-23: the capture node's index moves between boots (video0 on one boot,
video1 on the next), so both nodes are probed and the one that answers formats
is the capture node.
"""

import subprocess
import sys
import time

sys.path.insert(0, "/home/pi/robogame-runtime")

NODES = ("/dev/video0", "/dev/video1")


def run(cmd, timeout=15):
    try:
        out = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    except subprocess.TimeoutExpired:
        return "TIMEOUT", ""
    return f"rc={out.returncode}", (out.stdout + out.stderr)


def main():
    capture_node = None
    for node in NODES:
        status, text = run(["v4l2-ctl", "-d", node, "--list-formats-ext"], timeout=15)
        lines = [ln.strip() for ln in text.strip().splitlines() if ln.strip()]
        fmt_lines = [ln for ln in lines if ln.startswith("[") or "MJPG" in ln or "YUYV" in ln]
        print(f"--- {node} --- {status}")
        for ln in lines[:12]:
            print("   " + ln)
        if fmt_lines and capture_node is None:
            capture_node = node

    if capture_node is None:
        print("\nVERDICT: no node advertises any pixel format -- the device is not answering.")
        return 1

    print(f"\n--- streaming attempts on {capture_node} (3 frames each, 20s cap) ---")
    for size, pixfmt in (("1280x720", "MJPG"), ("640x480", "MJPG"), ("640x480", "YUYV")):
        w, h = size.split("x")
        status, text = run([
            "v4l2-ctl", "-d", capture_node,
            f"--set-fmt-video=width={w},height={h},pixelformat={pixfmt}",
            "--stream-mmap", "--stream-count=3",
        ], timeout=20)
        tail = text.strip().splitlines()[-2:]
        print(f"  {size:10s} {pixfmt:5s} -> {status} {' | '.join(tail)}")

    print("\n--- cv2 with the route's calibrated settings ---")
    import cv2

    from rg_runtime.config import load_camera_config
    from run_route_v2 import _configure_camera

    cfg = load_camera_config("config/camera_config.yaml")
    print(f"  config device={cfg.camera} ({cfg.width}x{cfg.height} {cfg.pixel_format})")
    cap = cv2.VideoCapture(cfg.camera)
    if not cap.isOpened():
        print(f"  index {cfg.camera}: NOT opened")
        return 1
    try:
        warnings = _configure_camera(cap, cfg, cv2)
    except RuntimeError as exc:
        print(f"  index {cfg.camera}: opened, configure failed: {exc}")
        return 1
    ok = 0
    shape = None
    for _ in range(30):
        got, frame = cap.read()
        if got and frame is not None:
            ok += 1
            shape = frame.shape
        time.sleep(0.01)
    cap.release()
    print(f"  index {cfg.camera}: OPENED warnings={warnings} frames={ok}/30 shape={shape}")
    print("\nVERDICT: " + ("camera OK" if ok >= 25 else "camera opened but frames are not flowing"))
    return 0 if ok >= 25 else 1


if __name__ == "__main__":
    raise SystemExit(main())
