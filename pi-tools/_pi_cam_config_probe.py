"""Which cv2.VideoCapture configuration actually delivers frames on this Pi?

Read-only: opens the camera and reads frames.  Sends no chassis command, so it
is safe with the car parked anywhere.

Motivation: the 2026-09-15 start-to-J2 run never saw tag 2 in 2309 ticks, and
the startup log shows the capture pipeline dying -- "v4l2src0 reported: Internal
data stream error", "unable to start pipeline", "no pipeline".  A standalone
probe that sets ONLY width/height got 1280x720 frames minutes earlier, while
run_route_v2.py's _configure_camera() sets FOURCC first.  This decides it by
measurement instead of by guess: each candidate configuration is opened, read
back, and asked for real frames.

Usage: python3 _pi_cam_config_probe.py
"""

import sys
import time

sys.path.insert(0, "/home/pi/robogame-runtime")


def main():
    import cv2

    from rg_runtime.config import load_camera_config

    camera_config = load_camera_config("config/camera_config.yaml")
    print(f"calibrated: {camera_config.width}x{camera_config.height} "
          f"fps={camera_config.fps} pixel_format={camera_config.pixel_format}")

    fourcc = cv2.VideoWriter_fourcc(*camera_config.pixel_format)
    w_prop = cv2.CAP_PROP_FRAME_WIDTH
    h_prop = cv2.CAP_PROP_FRAME_HEIGHT

    def attempt(name, ops):
        """Open, apply ops in order, then report what came back and how many
        of 15 reads produced a frame."""
        try:
            capture = cv2.VideoCapture(0)
        except Exception as exc:
            print(f"  {name:22s} OPEN RAISED {exc!r}")
            return
        if not capture.isOpened():
            print(f"  {name:22s} did not open")
            capture.release()
            return
        try:
            for prop, value in ops:
                capture.set(prop, value)
            try:
                read_w = int(capture.get(w_prop))
                read_h = int(capture.get(h_prop))
            except Exception:
                read_w = read_h = -1
            frames, shape = 0, None
            for _ in range(15):
                ok, frame = capture.read()
                if ok and frame is not None:
                    frames += 1
                    shape = frame.shape
        finally:
            capture.release()
        verdict = "WORKS " if frames else "NO FRAMES"
        print(f"  {name:22s} {verdict} readback {read_w}x{read_h}  "
              f"frames {frames}/15  last {shape}")
        return frames

    candidates = (
        # What _pi_cam_probe.py does, and what worked minutes before the run.
        ("WIDTH,HEIGHT", [(w_prop, camera_config.width), (h_prop, camera_config.height)]),
        # What run_route_v2._configure_camera does today.
        ("FOURCC,WIDTH,HEIGHT", [(cv2.CAP_PROP_FOURCC, fourcc),
                                 (w_prop, camera_config.width),
                                 (h_prop, camera_config.height)]),
        # The full sequence the function was modelled on.
        ("FOURCC,W,H,FPS,BUF", [(cv2.CAP_PROP_FOURCC, fourcc),
                                (w_prop, camera_config.width),
                                (h_prop, camera_config.height),
                                (cv2.CAP_PROP_FPS, camera_config.fps),
                                (cv2.CAP_PROP_BUFFERSIZE, 1)]),
        ("WIDTH,HEIGHT,FPS", [(w_prop, camera_config.width),
                              (h_prop, camera_config.height),
                              (cv2.CAP_PROP_FPS, camera_config.fps)]),
        ("FOURCC only", [(cv2.CAP_PROP_FOURCC, fourcc)]),
        ("nothing set", []),
    )

    results = {}
    for name, ops in candidates:
        results[name] = attempt(name, ops)
        time.sleep(1.0)

    print("\n=== verdict ===")
    winners = [n for n, f in results.items() if f]
    if not winners:
        print("NOTHING delivered frames -- this is not a property-ordering issue.")
        return 1
    print(f"delivered frames: {', '.join(winners)}")
    for n in ("FOURCC,WIDTH,HEIGHT", "WIDTH,HEIGHT"):
        if n in results:
            print(f"  {n:22s} -> {results[n]} frames")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
