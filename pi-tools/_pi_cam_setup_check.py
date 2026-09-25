"""Run run_route_v2._configure_camera against the real camera.

Read-only: opens the capture and reads frames.  Sends no chassis command, so it
is safe with the car parked anywhere.

This is the decisive check for the 2026-09-15 failure, because it exercises the
exact code path the route runner uses -- not a probe that merely resembles it.
It answers three questions at once: does the capture survive the property setup,
does it deliver frames, and are those frames the calibrated 1280x720.

Usage: python3 _pi_cam_setup_check.py
"""

import sys
import time

sys.path.insert(0, "/home/pi/robogame-runtime")


def main():
    import cv2

    from rg_runtime.config import load_camera_config
    from run_route_v2 import _configure_camera

    camera_config = load_camera_config("config/camera_config.yaml")
    print(f"calibrated: {camera_config.width}x{camera_config.height} "
          f"pixel_format={camera_config.pixel_format}")

    capture = cv2.VideoCapture(camera_config.camera)
    print(f"isOpened    : {capture.isOpened()}")
    if not capture.isOpened():
        print("VERDICT: camera did not open")
        return 1

    try:
        started = time.monotonic()
        try:
            warnings = _configure_camera(capture, camera_config, cv2)
        except RuntimeError as exc:
            # This is the exact refusal the route runner would hit before moving.
            print(f"_configure_camera RAISED: {exc}")
            print("VERDICT: the runner would refuse to start -- no motion.")
            return 1
        print(f"_configure_camera OK in {time.monotonic() - started:.2f}s, "
              f"warnings={warnings}")

        # The runner keeps reading from here; prove the stream is still live
        # after the setup consumed its warm-up frames.
        frames, shape = 0, None
        for _ in range(30):
            ok, frame = capture.read()
            if ok and frame is not None:
                frames += 1
                shape = frame.shape
        print(f"after setup : {frames}/30 frames, last {shape}")
    finally:
        capture.release()

    if frames and shape and (shape[1], shape[0]) == (camera_config.width, camera_config.height):
        print("VERDICT: OK -- live stream at the calibrated size")
        return 0
    print("VERDICT: PROBLEM -- see the frame count and shape above")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
