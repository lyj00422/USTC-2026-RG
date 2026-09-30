"""Capture one frame at the car's current pose and show the build_occupancy mask.

Read-only: opens the camera, grabs frames, writes images to /home/pi and prints
the detector's verdict.  Sends NO chassis command, so it is safe with the car
parked anywhere.

Why this exists: the telemetry only records the detector's ANSWER.  When the
answer is "yes, there is a building" and the box is 1174 px wide out of 1280,
the question is what the mask is actually covering -- background, floor, or the
structure.  Only the image shows that.

Outputs, in /home/pi:
    buildmask_<ts>_raw.jpg      the frame, with the ROI drawn
    buildmask_<ts>_mask.png     the binary colour mask (white = matched)
    buildmask_<ts>_overlay.jpg  mask tinted over the frame + accepted/rejected boxes
"""
import sys
import time

sys.path.insert(0, "/home/pi/robogame-runtime")


def main():
    frames = int(sys.argv[1]) if len(sys.argv) > 1 else 1

    import cv2
    import numpy as np

    from rg_runtime.blocks import BlockColor, ProfiledBlockDetector
    from rg_runtime.config import load_camera_config
    from route_v2.config import load_route_v2_config

    route_cfg = load_route_v2_config("config/route_v2.yaml")
    profile = route_cfg.vision.block_profiles["build_occupancy"]
    detector = ProfiledBlockDetector(profile, color=BlockColor.ORANGE)

    print("build_occupancy profile in force:")
    for field in ("roi", "capture_window", "area_px", "aspect_ratio", "height_px",
                  "center_y", "bottom_y", "near_field", "min_near_field_fill",
                  "min_rectangularity", "morphology_kernel"):
        print(f"    {field:22} {getattr(profile, field)}")
    print(f"    hsv_bands              {profile.hsv_bands}")
    print(f"    ycrcb_bands            {profile.ycrcb_bands}")
    print()

    camera_config = load_camera_config("config/camera_config.yaml")
    capture = cv2.VideoCapture(0)
    if not capture.isOpened():
        print("camera 0 did not open")
        return 1
    capture.set(cv2.CAP_PROP_FRAME_WIDTH, camera_config.width)
    capture.set(cv2.CAP_PROP_FRAME_HEIGHT, camera_config.height)

    stamp = time.strftime("%H%M%S")
    try:
        for _ in range(10):
            capture.read()
            time.sleep(0.03)

        for index in range(frames):
            ok, frame = capture.read()
            if not ok:
                print(f"frame {index}: read failed")
                continue
            height, width = frame.shape[:2]

            # Rebuild the mask exactly as ProfiledBlockDetector does, so what is
            # drawn is what the gate saw -- not an approximation of it.
            hsv = cv2.cvtColor(frame, cv2.COLOR_BGR2HSV)
            lab = cv2.cvtColor(frame, cv2.COLOR_BGR2LAB)
            ycrcb = cv2.cvtColor(frame, cv2.COLOR_BGR2YCrCb)

            def band_mask(image, bands):
                out = np.zeros(image.shape[:2], dtype=np.uint8)
                for band in bands:
                    out = cv2.bitwise_or(out, cv2.inRange(
                        image, np.asarray(band.lower, dtype=np.uint8),
                        np.asarray(band.upper, dtype=np.uint8)))
                return out

            hsv_m = band_mask(hsv, profile.hsv_bands)
            lab_m = band_mask(lab, profile.lab_bands)
            ycc_m = band_mask(ycrcb, profile.ycrcb_bands)
            mask = cv2.bitwise_or(cv2.bitwise_or(hsv_m, lab_m), ycc_m)

            roi = profile.roi
            x1, x2 = int(round(roi.left * width)), int(round(roi.right * width))
            y1, y2 = int(round(roi.top * height)), int(round(roi.bottom * height))
            roi_mask = np.zeros_like(mask)
            roi_mask[y1:y2, x1:x2] = 255
            mask = cv2.bitwise_and(mask, roi_mask)
            k = profile.morphology_kernel
            kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (k, k))
            mask = cv2.morphologyEx(mask, cv2.MORPH_OPEN, kernel)
            mask = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, kernel)

            result = detector.detect(frame, timestamp_ns=time.monotonic_ns(),
                                     frame_index=index)
            print(f"--- frame {index}: {width}x{height}, ROI x{x1}..{x2} y{y1}..{y2}")
            print(f"    mask coverage: {cv2.countNonZero(mask) / float(width * height) * 100:5.1f}% "
                  f"of the frame, {cv2.countNonZero(mask) / float(max(1, (x2 - x1) * (y2 - y1))) * 100:5.1f}% "
                  f"of the ROI")
            print(f"    accepted {len(result.accepted)}  rejected {len(result.rejected)}")
            for item in result.accepted[:4]:
                print(f"      ACCEPT box={list(item.bounding_box)} area={item.area:.0f} "
                      f"centre={item.center_px[0]:.0f},{item.center_px[1]:.0f} "
                      f"conf={item.confidence:.2f}")
            tally = {}
            for item in result.rejected:
                tally[item.reason] = tally.get(item.reason, 0) + 1
            for reason, n in sorted(tally.items(), key=lambda kv: -kv[1]):
                print(f"      reject {reason}: {n}")

            raw_path = f"/home/pi/buildmask_{stamp}_{index:02d}_raw.jpg"
            mask_path = f"/home/pi/buildmask_{stamp}_{index:02d}_mask.png"
            over_path = f"/home/pi/buildmask_{stamp}_{index:02d}_overlay.jpg"
            cv2.rectangle(frame, (x1, y1), (x2, y2), (255, 255, 0), 2)
            cv2.imwrite(raw_path, frame)

            cv2.imwrite(mask_path, mask)

            overlay = frame.copy()
            tint = np.zeros_like(frame)
            tint[:, :, 2] = 255                      # mask shown in red
            overlay = np.where(mask[:, :, None] > 0,
                               (0.55 * overlay + 0.45 * tint).astype(np.uint8),
                               overlay)
            for item in result.accepted:
                bx, by, bw, bh = item.bounding_box
                cv2.rectangle(overlay, (bx, by), (bx + bw, by + bh), (0, 255, 0), 3)
            for item in result.rejected:
                bx, by, bw, bh = item.bounding_box
                cv2.rectangle(overlay, (bx, by), (bx + bw, by + bh), (0, 165, 255), 1)
            cv2.rectangle(overlay, (x1, y1), (x2, y2), (255, 255, 0), 2)
            cv2.imwrite(over_path, overlay)
            print(f"    saved {raw_path}\n    saved {mask_path}\n    saved {over_path}")
    finally:
        capture.release()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
