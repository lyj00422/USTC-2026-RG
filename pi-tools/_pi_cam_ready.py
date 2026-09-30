"""Read-only camera readiness probe, run before a motion run.

    _pi_cam_ready.py

After a reboot the USB camera re-enumerates late (observed: 112 s after boot),
so a launch that races it dies with `FAULT_SAFE: unable to open camera 0` and a
0-byte telemetry file.  This opens the camera exactly the way run_route_v2 does
and reports the frame shape, then releases it.  Sends no chassis command.
"""
import sys
import time

sys.path.insert(0, "/home/pi/robogame-runtime")

import cv2  # noqa: E402

from rg_runtime.config import load_camera_config  # noqa: E402

CAMERA_CONFIG = "/home/pi/robogame-runtime/config/camera_config.yaml"


def main():
    try:
        cfg = load_camera_config(CAMERA_CONFIG)
        index = cfg.camera
    except Exception as exc:  # noqa: BLE001
        print(f"camera config : UNREADABLE ({exc})")
        index = 0

    print(f"camera config : index={index}")

    for attempt in range(1, 11):
        camera = cv2.VideoCapture(index)
        if camera.isOpened():
            ok, frame = camera.read()
            if ok and frame is not None:
                print(f"camera        : OPEN on attempt {attempt}, "
                      f"frame {frame.shape[1]}x{frame.shape[0]}")
                camera.release()
                return 0
            print(f"  attempt {attempt}: opened but read() returned nothing")
        else:
            print(f"  attempt {attempt}: isOpened() false")
        camera.release()
        time.sleep(1.5)

    print("camera        : NOT USABLE after 10 attempts")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
