"""Is the camera stream actually live, or is it handing back the same frame?

Why this exists: RouteRunner.tick() only calls tag_tracker.update() when
camera.read() succeeds, and TagTracker._stable is sticky.  So a stalled stream
freezes _tag2_result at its last value -- a dead camera looks exactly like a
tag that is stable forever.  The 2026-09-15 run showed a tag pose that did not
change while the car advanced 65 cm, which is what that failure looks like.

A real sensor always has noise, so consecutive frames of a static scene are
never byte-identical.  Zero difference across every pair == frozen stream.

Read-only: opens the camera and reads frames.  Sends no motion command.

Run on the Pi, from the runtime directory:
    /home/pi/rg-venv/bin/python /home/pi/_pi_cam_stream_check.py [frames]
"""

import sys
import time

sys.path.insert(0, "/home/pi/robogame-runtime")

import numpy as np  # noqa: E402

from rg_runtime.apriltag import AprilTagDetector  # noqa: E402
from rg_runtime.config import load_camera_config  # noqa: E402
from run_route_v2 import _configure_camera  # noqa: E402

CAMERA_CONFIG = "config/camera_config.yaml"
N = int(sys.argv[1]) if len(sys.argv) > 1 else 30


def main():
    import cv2

    cfg = load_camera_config(CAMERA_CONFIG)
    camera = cv2.VideoCapture(cfg.camera)
    print(f"isOpened : {camera.isOpened()}")
    for warning in _configure_camera(camera, cfg, cv2):
        print(f"WARNING  : {warning}")

    detector = AprilTagDetector(cfg)
    prev = None
    ok_count = 0
    identical = 0
    diffs = []
    per_frame_ids = []
    tvecs = []

    for i in range(N):
        ok, frame = camera.read()
        if not ok or frame is None:
            print(f"  {i:02d}  read FAILED")
            continue
        ok_count += 1
        if prev is not None and prev.shape == frame.shape:
            d = float(np.abs(frame.astype(np.int16) - prev.astype(np.int16)).mean())
            diffs.append(d)
            if d == 0.0:
                identical += 1
        prev = frame

        obs = detector.detect(frame, timestamp_ns=int(time.monotonic() * 1e9), frame_index=i)
        ids = [o.id for o in obs]
        per_frame_ids.append(tuple(ids))
        for o in obs:
            if o.id == 2 and o.pose_camera is not None:
                tvecs.append(tuple(round(float(v), 1) for v in o.pose_camera.tvec))
        if i < 3 or i % 5 == 0:
            print(f"  {i:02d}  shape={frame.shape}  tags={ids}")
        time.sleep(0.05)

    camera.release()

    print(f"\nreads ok            : {ok_count}/{N}")
    if diffs:
        print(f"consecutive diffs   : min={min(diffs):.4f} max={max(diffs):.4f} "
              f"mean={sum(diffs)/len(diffs):.4f}")
        print(f"byte-identical pairs: {identical}/{len(diffs)}")
    print(f"frames with any tag : {sum(1 for x in per_frame_ids if x)}/{len(per_frame_ids)}")
    print(f"frames with tag 2   : {sum(1 for x in per_frame_ids if 2 in x)}/{len(per_frame_ids)}")
    print(f"distinct tag2 tvec  : {len(set(tvecs))}  (from {len(tvecs)} detections)")

    if diffs and identical == len(diffs):
        print("\nVERDICT: FROZEN -- every frame is identical; the stream is stalled")
        return 1
    if diffs and max(diffs) == 0.0:
        print("\nVERDICT: FROZEN")
        return 1
    print("\nVERDICT: stream is live (frames vary)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
