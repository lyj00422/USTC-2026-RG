"""Read-only AprilTag visibility probe: which tags can the camera see from here?

Sends no chassis command of any kind -- this only captures frames and runs the
detector, so it is safe to run with the car parked anywhere.

Motivation: the J1 -> J2 lateral strafe drifts because mecanum rollers slip
sideways.  The encoders cannot see that slip (measured: forward drift 0.44 cm
and 0.03 cm over 330 / 206 cm at speed 30 / 60, i.e. the wheels say the car went
straight at both speeds while the operator watched it go crooked).  Closing the
loop therefore needs an external reference, and the AprilTag system is the one
already installed -- config/route_v2_apriltags.yaml states its purpose as
"robot_positioning_and_pose_correction" and puts tag 3 at JUNCTION_2.

Whether a tag is actually visible from the strafe is an open question: the
camera looks along the car's nose (extrinsics rotation 0, X_forward_Y_left),
while during a rightward strafe the J2 tag sits about 90 degrees off that axis,
far outside the ~60 degree horizontal field of view implied by the calibration
(fx 1107.7 px over 1280 px).  Hence this probe.

Usage: python3 _pi_cam_probe.py [frames]
"""

import math
import sys
import time

sys.path.insert(0, "/home/pi/robogame-runtime")


def main():
    frames = int(sys.argv[1]) if len(sys.argv) > 1 else 30

    import cv2

    from rg_runtime.apriltag import AprilTagDetector
    from rg_runtime.config import load_camera_config

    try:
        camera_config = load_camera_config("config/camera_config.yaml")
    except Exception as exc:
        print(f"camera config unusable: {exc}")
        return 1
    detector = AprilTagDetector(camera_config, allowed_ids=range(1, 7))

    capture = cv2.VideoCapture(0)
    if not capture.isOpened():
        print("camera 0 did not open")
        return 1
    # The calibration in camera_config.yaml is for config.camera.width x
    # config.camera.height.  cv2.VideoCapture(0) on its own opened at 640x480,
    # and feeding a 1280x720 intrinsic matrix to undistort a 640x480 frame puts
    # the geometry -- and therefore any pose solved from it -- wrong.  Ask for
    # the calibrated size explicitly and report what actually came back, because
    # the driver is free to ignore the request.
    capture.set(cv2.CAP_PROP_FRAME_WIDTH, camera_config.width)
    capture.set(cv2.CAP_PROP_FRAME_HEIGHT, camera_config.height)
    try:
        # The first frames after opening are usually dark/garbage; discard them.
        for _ in range(10):
            capture.read()
            time.sleep(0.03)

        started = time.monotonic()
        seen = {}
        for index in range(frames):
            ok, frame = capture.read()
            if not ok:
                print(f"frame {index}: read failed")
                continue
            if index and index % 60 == 0:
                # Heartbeat: proves the probe was alive and looking at this
                # moment, so a silent stretch in the log means "no tag", not
                # "the probe had already died".
                print(f"  ...{index} frames, t={time.monotonic() - started:6.2f}s", flush=True)
            if index and index % 150 == 0:
                # A still every few seconds, so a run that finds no tag can
                # still show what the camera was pointed at and when.
                snap = f"/home/pi/cam_run_{index:04d}.jpg"
                cv2.imwrite(snap, frame)
                print(f"  snap {snap}", flush=True)
            height, width = frame.shape[:2]
            found = detector.detect(frame, timestamp_ns=time.monotonic_ns(), frame_index=index)
            for tag in found:
                cx, cy = tag.center_px
                # Bearing off the optical axis, in degrees: positive = the tag is
                # to the right of where the camera is looking.
                bearing = (cx - width / 2.0) / camera_config.camera_matrix[0][0]
                bearing_deg = math.degrees(math.atan(bearing))
                seen.setdefault(tag.id, []).append((cx, cy, bearing_deg, tag.pose_camera))
                print(f"frame {index:3d} t={time.monotonic() - started:6.2f}s: tag {tag.id}  "
                      f"centre ({cx:6.1f},{cy:6.1f}) px  bearing {bearing_deg:+6.2f} deg",
                      flush=True)
            if index == 0:
                print(f"frame size {width}x{height}, looking for tags 1..6")
                # A detection miss has two very different causes -- no tag in
                # view, or a camera that is dead/covered/misaimed.  Brightness
                # and contrast tell them apart, and the saved frame lets the
                # operator confirm what the camera is actually pointed at.
                gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
                print(f"           frame mean {gray.mean():6.1f}  "
                      f"min {int(gray.min()):3d}  max {int(gray.max()):3d}  "
                      f"std {gray.std():5.1f}")
                out = f"/home/pi/cam_probe_{int(time.time())}.jpg"
                cv2.imwrite(out, frame)
                print(f"           saved {out}")
            # Cap the rate.  Unthrottled, the capture runs flat out and competes
            # with the pigpio soft UART behind the line sensor -- and a dropped
            # line frame during a strafe is exactly the data this run is for.
            time.sleep(0.05)
    finally:
        capture.release()

    print("\n=== summary ===")
    if not seen:
        print(f"no tags detected in {frames} frames")
        return 0
    for tag_id in sorted(seen):
        samples = seen[tag_id]
        bearings = [s[2] for s in samples]
        print(f"tag {tag_id}: {len(samples)}/{frames} frames, "
              f"bearing {min(bearings):+.2f} .. {max(bearings):+.2f} deg")
        last = samples[-1][3]
        if last is not None:
            tvec = last.tvec
            print(f"         last pose tvec (mm, camera frame): "
                  f"x {tvec[0]:8.1f}  y {tvec[1]:8.1f}  z {tvec[2]:8.1f}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
