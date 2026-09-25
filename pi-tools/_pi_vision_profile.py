"""Analyse a capture-profile directory on the Pi: AprilTag spread, or block gates.

`calibrate_route_vision.py capture-profile` only SAVES frames -- it runs no
detection.  The field runbook (handoff/交接文档.md, "Route V2 视觉现场标定执行单")
then asks for the things that only come from running the real detector over those
frames.  Those numbers are what `route_v2.vision.tag_gates.*` and
`route_v2.vision.block_profiles.*` get filled in from, so this prints them next to
whatever the config currently demands -- and, for a block profile, why each
rejected candidate was rejected, because the useful question at the track is
"which threshold is the one doing the rejecting".

Run it on the Pi with the runtime's venv, from /home/pi/robogame-runtime:

    ~/rg-venv/bin/python /tmp/_pi_vision_profile.py <capture_dir> \
        --gate pickup_seek_line
    ~/rg-venv/bin/python /tmp/_pi_vision_profile.py <capture_dir> \
        --block-profile purple_j3_prescan --color purple

Both branches mirror the runtime's own accept rule rather than approximating it,
so the verdict here is the verdict the route would reach on the same frames.
"""
from __future__ import annotations

import argparse
import json
import math
import sys
from pathlib import Path

sys.path.insert(0, "/home/pi/robogame-runtime")

# Which metric each rejection reason is about, so a report can say how far the
# rejected candidates sat from the threshold that killed them.
REASON_METRIC = {
    "area_below_min": "area_px",
    "area_above_max": "area_px",
    "aspect_below_min": "aspect_ratio",
    "aspect_above_max": "aspect_ratio",
    "rectangularity_below_min": "rectangularity",
    "height_below_min": "height_px",
    "height_above_max": "height_px",
    "center_y_below_min": "center_y",
    "center_y_above_max": "center_y",
}


def _edge_px(corners) -> float:
    """Mean of the four side lengths of the detected quad, in pixels."""
    sides = []
    for index in range(4):
        (x0, y0), (x1, y1) = corners[index], corners[(index + 1) % 4]
        sides.append(math.hypot(x1 - x0, y1 - y0))
    return sum(sides) / 4.0


def _summary(values) -> str:
    if not values:
        return "--"
    return (f"min {min(values):8.2f}  max {max(values):8.2f}  "
            f"mean {sum(values) / len(values):8.2f}")


def _load_frames(root: Path):
    manifest = json.loads((root / "manifest.json").read_text())
    return manifest, [entry["path"] for entry in manifest["frames"]]


def _tag_report(args, root, manifest, cv2, detector, config) -> int:
    gate = None
    if args.gate:
        gates = config.vision.tag_gates if config.vision else {}
        if args.gate not in gates:
            print(f"unknown tag gate {args.gate!r}; known: {sorted(gates)}")
            return 1
        gate = gates[args.gate]
        window, size = gate.center_window, gate.edge_px
        print(f"gate {args.gate}: target_id={gate.target_id} "
              f"center_window=[{window.left}, {window.top}, {window.right}, {window.bottom}] "
              f"edge_px=[{size.minimum}, {size.maximum}] "
              f"confirm_frames={gate.confirm_frames}")

    rows = []
    for entry in manifest["frames"]:
        frame = cv2.imread(str(root / entry["path"]))
        if frame is None:
            rows.append({"frame": entry["frame"], "tags": []})
            continue
        height, width = frame.shape[:2]
        row = {"frame": entry["frame"], "width": width, "height": height, "tags": []}
        for obs in detector.detect(frame, frame_index=entry["frame"]):
            cx, cy = obs.center_px
            row["tags"].append({"id": obs.id, "center_norm": (cx / width, cy / height),
                                "center_px": (cx, cy), "edge_px": _edge_px(obs.corners_px)})
        rows.append(row)

    detected = [r for r in rows if r.get("tags")]
    ids: dict[int, int] = {}
    for row in detected:
        for tag in row["tags"]:
            ids[tag["id"]] = ids.get(tag["id"], 0) + 1

    print(f"\nframes: {len(rows)}   with any tag: {len(detected)}   "
          f"with none: {len(rows) - len(detected)}")
    print(f"ID histogram: {dict(sorted(ids.items()))}")
    if not detected:
        print("\nno tag detected in any frame")
        return 1

    target = gate.target_id if gate else max(ids, key=lambda k: ids[k])
    print(f"\n--- id {target} ({ids.get(target, 0)} frames) ---")
    picked = [t for r in detected for t in r["tags"] if t["id"] == target]
    print(f"  centre x (norm): {_summary([t['center_norm'][0] for t in picked])}")
    print(f"  centre y (norm): {_summary([t['center_norm'][1] for t in picked])}")
    print(f"  edge (px)      : {_summary([t['edge_px'] for t in picked])}")

    if gate is not None:
        x0, y0, x1, y1 = window.left, window.top, window.right, window.bottom
        lo, hi = size.minimum, size.maximum
        passing = []
        for row in rows:
            hit = [t for t in row.get("tags", []) if t["id"] == target]
            passing.append(any(x0 <= t["center_norm"][0] <= x1 and y0 <= t["center_norm"][1] <= y1
                               and lo <= t["edge_px"] <= hi for t in hit))
        best = run = 0
        for ok in passing:
            run = run + 1 if ok else 0
            best = max(best, run)
        print(f"\n  within the gate: {sum(passing)}/{len(rows)} frames, "
              f"longest consecutive run {best} (needs {gate.confirm_frames})")
        if not sum(passing):
            print("  -> would NOT confirm: no frame is inside both the centre "
                  "window and the size band")
        elif best < gate.confirm_frames:
            print("  -> would NOT confirm: frames pass but never "
                  f"{gate.confirm_frames} in a row")
        else:
            print("  -> would confirm")
    return 0


def _block_report(args, root, manifest, cv2, config) -> int:
    from rg_runtime.blocks import BlockColor, ProfiledBlockDetector

    profiles = config.vision.block_profiles if config.vision else {}
    if args.block_profile not in profiles:
        print(f"unknown block profile {args.block_profile!r}; known: {sorted(profiles)}")
        return 1
    profile = profiles[args.block_profile]
    color = BlockColor.PURPLE if args.color == "purple" else BlockColor.ORANGE
    detector = ProfiledBlockDetector(profile, color=color)

    print(f"profile {args.block_profile} ({args.color}): "
          f"roi=[{profile.roi.left}, {profile.roi.top}, {profile.roi.right}, {profile.roi.bottom}] "
          f"area_px=[{profile.area_px.minimum}, {profile.area_px.maximum}] "
          f"aspect=[{profile.aspect_ratio.minimum}, {profile.aspect_ratio.maximum}] "
          f"height_px=[{profile.height_px.minimum}, {profile.height_px.maximum}] "
          f"center_y=[{profile.center_y.minimum}, {profile.center_y.maximum}] "
          f"min_rect={profile.min_rectangularity}")

    # The route's own prescan rule: take the largest accepted candidate, count
    # consecutive frames whose centres are within 100 px of each other, call it
    # present at confirm_frames, and call it absent once the frame budget is up.
    confirm = config.vision.prescan_confirm_frames
    budget = config.vision.prescan_frames

    frames = []
    for entry in manifest["frames"]:
        frame = cv2.imread(str(root / entry["path"]))
        result = detector.detect(frame, frame_index=entry["frame"])
        frames.append(result)

    with_candidate = [r for r in frames if r.accepted]
    print(f"\nframes: {len(frames)}   with a candidate: {len(with_candidate)}   "
          f"with none: {len(frames) - len(with_candidate)}")
    print(f"prescan rule: largest candidate per frame, centres within 100 px count "
          f"as the same block; present at {confirm} in a row, absent once "
          f"{budget} frames are up")

    stable = 0
    last_center = None
    trajectory = []
    for result in frames:
        candidate = result.accepted[0] if result.accepted else None
        if candidate is None:
            stable, last_center = 0, None
        else:
            same = last_center is not None and math.dist(candidate.center_px, last_center) <= 100
            stable = stable + 1 if same else 1
            last_center = candidate.center_px
        trajectory.append(stable)
    print(f"  stable counter over the frames: {' '.join(str(v) for v in trajectory)}")
    print(f"  peak {max(trajectory) if trajectory else 0}, needs {confirm}")
    reached = max(trajectory) if trajectory else 0
    if reached >= confirm:
        print("  -> the route would read PRESENT")
    elif len(frames) >= budget:
        print("  -> the route would read ABSENT (no run of "
              f"{confirm}, budget of {budget} frames used up)")
    else:
        print(f"  -> undecided: only {len(frames)} frames captured, the route "
              f"needs {budget}")

    if with_candidate:
        print(f"\n--- the largest candidate on each of those {len(with_candidate)} frames ---")
        for name, getter in (
            ("centre x (px) ", lambda o: o.center_px[0]),
            ("centre y (px) ", lambda o: o.center_px[1]),
            ("area (px^2)   ", lambda o: o.area),
            ("bbox height   ", lambda o: o.bounding_box[3]),
            ("bbox width    ", lambda o: o.bounding_box[2]),
            ("confidence    ", lambda o: o.confidence),
        ):
            print(f"  {name}: "
                  f"{_summary([getter(r.accepted[0]) for r in with_candidate])}")
        channels: dict[tuple, int] = {}
        for result in with_candidate:
            channels[result.accepted[0].color_channels] = \
                channels.get(result.accepted[0].color_channels, 0) + 1
        print(f"  colour channels hit: {channels}")

    reasons: dict[str, list[float]] = {}
    for result in frames:
        for rejected in result.rejected:
            reasons.setdefault(rejected.reason, []).append(
                rejected.metrics[REASON_METRIC.get(rejected.reason, "area_px")])
    if reasons:
        print("\n--- why candidates were rejected ---")
        for reason, values in sorted(reasons.items(), key=lambda kv: -len(kv[1])):
            print(f"  {reason:26s} {len(values):4d}x   "
                  f"{REASON_METRIC.get(reason, '?')}: {_summary(values)}")
    else:
        print("\nno candidate was rejected by a gate")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("capture_dir")
    parser.add_argument("--gate", default=None,
                        help="key under route_v2.vision.tag_gates")
    parser.add_argument("--block-profile", default=None,
                        help="key under route_v2.vision.block_profiles")
    parser.add_argument("--color", default="purple", choices=("purple", "orange"))
    parser.add_argument("--camera-config", default="config/camera_config.yaml")
    parser.add_argument("--route-config", default="config/route_v2.yaml")
    args = parser.parse_args()

    import cv2
    from route_v2.config import load_route_v2_config

    root = Path(args.capture_dir)
    manifest_path = root / "manifest.json"
    if not manifest_path.exists():
        print(f"no manifest.json in {root}")
        return 1
    manifest, _paths = _load_frames(root)
    config = load_route_v2_config(args.route_config)

    if args.block_profile:
        return _block_report(args, root, manifest, cv2, config)

    from rg_runtime.config import load_camera_config
    from rg_runtime.apriltag import AprilTagDetector
    return _tag_report(args, root, manifest, cv2,
                       AprilTagDetector(load_camera_config(args.camera_config)), config)


if __name__ == "__main__":
    raise SystemExit(main())
