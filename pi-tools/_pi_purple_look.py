"""READ-ONLY: what does purple_pickup_close see right now?

Opens the camera, runs the same ProfiledBlockDetector the route uses for
PICKUP_VISION_ONLY over a handful of fresh frames, and reports for each frame
whether a candidate survived the gates and whether its centre lands inside the
0004 capture window.  When nothing survives it prints why the candidates were
rejected, so a colour-threshold problem and a shape-threshold problem do not
look the same.

Opens no serial port.  Sends no command to anything.  Safe to run as often as
you like while the car sits still.
"""
import sys
import time

sys.path.insert(0, "/home/pi/robogame-runtime")

from pathlib import Path  # noqa: E402

ROOT = Path("/home/pi/robogame-runtime")
FRAMES = 20


def main():
    import cv2
    from rg_runtime.blocks import ProfiledBlockDetector
    from rg_runtime.config import load_camera_config
    from rg_runtime.models import BlockColor
    from route_v2.config import load_route_v2_config
    from run_route_v2 import _configure_camera

    config = load_route_v2_config(str(ROOT / "config/route_v2.yaml"))
    profile = config.vision.block_profiles["purple_pickup_close"]
    win = profile.capture_window
    print(f"capture_window : [{win.left:.4f}, {win.top:.4f}, {win.right:.4f}, {win.bottom:.4f}]")
    print(f"roi            : [{profile.roi.left}, {profile.roi.top}, {profile.roi.right}, {profile.roi.bottom}]")
    print(f"area_px        : {profile.area_px.minimum} .. {profile.area_px.maximum}")
    print(f"height_px      : {profile.height_px.minimum} .. {profile.height_px.maximum}")

    camera_config = load_camera_config(ROOT / "config/camera_config.yaml")
    camera = cv2.VideoCapture(camera_config.camera)
    if not camera.isOpened():
        print("camera did not open")
        return 1
    for warning in _configure_camera(camera, camera_config, cv2):
        print(f"WARNING: {warning}")
    detector = ProfiledBlockDetector(profile, color=BlockColor.PURPLE)
    inside = 0
    seen = 0
    rejections: dict[str, int] = {}
    try:
        # Discard a few frames: the first reads after opening are stale/auto-exposing.
        for _ in range(5):
            camera.read()
        for index in range(FRAMES):
            ok, frame = camera.read()
            if not ok or frame is None:
                print(f"frame {index:02d}: READ FAILED")
                continue
            height, width = frame.shape[:2]
            result = detector.detect(frame, frame_index=index, timestamp_ns=time.time_ns())
            if not result.accepted:
                for rejected in result.rejected:
                    rejections[rejected.reason] = rejections.get(rejected.reason, 0) + 1
                print(f"frame {index:02d}: no candidate  ({len(result.rejected)} rejected)  {width}x{height}")
                continue
            seen += 1
            for candidate in result.accepted:
                cx, cy = candidate.center_px
                nx, ny = cx / width, cy / height
                hit = win.left <= nx <= win.right and win.top <= ny <= win.bottom
                inside += int(hit)
                print(f"frame {index:02d}: HIT  centre=({cx:.1f},{cy:.1f}) norm=({nx:.4f},{ny:.4f}) "
                      f"area={candidate.area:.0f} bbox={candidate.bounding_box} "
                      f"conf={candidate.confidence:.2f} ch={candidate.color_channels} "
                      f"in_window={hit}")
            if len(result.accepted) > 1:
                print(f"          ^ {len(result.accepted)} accepted candidates this frame "
                      f"(route locks the one nearest the window centre)")
    finally:
        camera.release()

    print(f"\nsummary: {seen}/{FRAMES} frames had an accepted candidate, "
          f"{inside} centre(s) landed inside the capture window")
    if rejections:
        print("rejection reasons (counted across frames):")
        for reason, count in sorted(rejections.items(), key=lambda item: -item[1]):
            print(f"    {count:4d}  {reason}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
