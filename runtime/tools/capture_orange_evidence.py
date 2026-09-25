"""Capture camera evidence when the route's orange detector accepts a block.

This tool is deliberately read-only with respect to the robot: it opens only
the camera, never creates a chassis or line-sensor connection, and never sends
motion or arm commands.
"""

from __future__ import annotations

import argparse
from collections import deque
from collections.abc import Mapping
import json
from pathlib import Path
import time
from typing import Any


def _json_value(value: Any) -> Any:
    """Convert detector dataclasses/enums into JSON-safe evidence fields."""
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, Mapping):
        return {str(key): _json_value(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_value(item) for item in value]
    if hasattr(value, "value"):
        return _json_value(value.value)
    if hasattr(value, "__dataclass_fields__"):
        return {
            name: _json_value(getattr(value, name))
            for name in value.__dataclass_fields__
        }
    return str(value)


def detection_record(result) -> dict[str, Any]:
    """Return all accepted and rejected candidates from one detector frame."""
    return {
        "frame_index": int(result.frame_index),
        "timestamp_ns": int(result.timestamp_ns),
        "accepted": _json_value(result.accepted),
        "rejected": _json_value(result.rejected),
    }


def _draw_overlay(cv2, frame, result):
    overlay = frame.copy()
    for candidate in result.accepted:
        x, y, width, height = candidate.bounding_box
        cv2.rectangle(overlay, (x, y), (x + width, y + height), (0, 220, 0), 3)
        cv2.putText(
            overlay,
            f"ACCEPT orange area={candidate.area:.0f} conf={candidate.confidence:.2f}",
            (max(0, x), max(24, y - 8)),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.65,
            (0, 220, 0),
            2,
            cv2.LINE_AA,
        )
    for candidate in result.rejected:
        x, y, width, height = candidate.bounding_box
        cv2.rectangle(overlay, (x, y), (x + width, y + height), (0, 0, 220), 2)
        cv2.putText(
            overlay,
            f"REJECT {candidate.reason}",
            (max(0, x), min(frame.shape[0] - 8, y + height + 20)),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.55,
            (0, 0, 220),
            2,
            cv2.LINE_AA,
        )
    return overlay


def _write_frame(cv2, output: Path, item: dict[str, Any], quality: int) -> dict[str, Any]:
    frame = item["frame"]
    frame_id = int(item["frame_id"])
    raw_name = f"frame_{frame_id:06d}_raw.jpg"
    overlay_name = f"frame_{frame_id:06d}_overlay.jpg"
    params = [int(cv2.IMWRITE_JPEG_QUALITY), quality]
    if not cv2.imwrite(str(output / raw_name), frame, params):
        raise RuntimeError(f"failed to write {output / raw_name}")
    if not cv2.imwrite(str(output / overlay_name), item["overlay"], params):
        raise RuntimeError(f"failed to write {output / overlay_name}")
    return {
        "frame_id": frame_id,
        "captured_at": item["captured_at"],
        "raw": raw_name,
        "overlay": overlay_name,
        "detection": item["detection"],
    }


def _capture_hit(
    cv2,
    camera,
    detector,
    *,
    trigger: dict[str, Any],
    pre_frames: list[dict[str, Any]],
    post_count: int,
    output: Path,
    hit_index: int,
    quality: int,
    next_frame_id: int,
) -> tuple[int, dict[str, Any]]:
    hit_dir = output / f"hit_{hit_index:03d}"
    hit_dir.mkdir(parents=True, exist_ok=False)
    items = list(pre_frames)
    if not items or items[-1]["frame_id"] != trigger["frame_id"]:
        items.append(trigger)

    frame_id = next_frame_id
    for _ in range(post_count):
        ok, frame = camera.read()
        if not ok or frame is None:
            break
        frame_id += 1
        timestamp_ns = time.time_ns()
        result = detector.detect(frame, timestamp_ns=timestamp_ns, frame_index=frame_id)
        items.append({
            "frame": frame,
            "frame_id": frame_id,
            "captured_at": time.time(),
            "detection": detection_record(result),
            "overlay": _draw_overlay(cv2, frame, result),
        })

    manifest = {
        "trigger_frame_id": trigger["frame_id"],
        "trigger_detection": trigger["detection"],
        "frames": [_write_frame(cv2, hit_dir, item, quality) for item in items],
    }
    (hit_dir / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return frame_id, manifest


def capture(args) -> int:
    import cv2

    from route_v2.config import load_route_v2_config
    from rg_runtime.blocks import ProfiledBlockDetector
    from rg_runtime.config import load_camera_config
    from rg_runtime.models import BlockColor
    from run_route_v2 import _configure_camera

    route_config = load_route_v2_config(args.route_config)
    if route_config.vision is None:
        raise RuntimeError("route config has no vision section")
    profile = route_config.vision.block_profiles["orange_pickup_close"]
    camera_config = load_camera_config(args.camera_config)
    output = Path(args.output).resolve()
    output.mkdir(parents=True, exist_ok=True)

    camera = cv2.VideoCapture(camera_config.camera)
    if not camera.isOpened():
        raise RuntimeError("camera did not open")
    detector = ProfiledBlockDetector(profile, color=BlockColor.ORANGE)
    warnings = _configure_camera(
        camera, camera_config, cv2, require_calibrated_size=True
    )
    session = {
        "started_at": time.time(),
        "route_config": str(Path(args.route_config).resolve()),
        "camera_config": str(Path(args.camera_config).resolve()),
        "profile": "orange_pickup_close",
        "frames_per_hit_before": args.pre_frames,
        "frames_per_hit_after": args.post_frames,
        "cooldown_s": args.cooldown_s,
        "max_hits": args.max_hits,
        "warnings": warnings,
        "hits": [],
    }
    (output / "session.json").write_text(
        json.dumps(session, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    history: deque[dict[str, Any]] = deque(maxlen=max(1, args.pre_frames + 1))
    started = time.monotonic()
    last_hit_at = -float("inf")
    frame_id = 0
    hit_index = 0
    try:
        print(f"ORANGE_EVIDENCE_OUTPUT={output}", flush=True)
        print("camera-only capture; press Ctrl+C to stop", flush=True)
        while time.monotonic() - started < args.duration_s:
            ok, frame = camera.read()
            if not ok or frame is None:
                raise RuntimeError(f"camera read failed at frame {frame_id + 1}")
            frame_id += 1
            result = detector.detect(
                frame, timestamp_ns=time.time_ns(), frame_index=frame_id
            )
            item = {
                "frame": frame,
                "frame_id": frame_id,
                "captured_at": time.time(),
                "detection": detection_record(result),
                "overlay": _draw_overlay(cv2, frame, result),
            }
            history.append(item)
            now = time.monotonic()
            if (
                result.accepted
                and now - last_hit_at >= args.cooldown_s
                and hit_index < args.max_hits
            ):
                hit_index += 1
                frame_id, manifest = _capture_hit(
                    cv2,
                    camera,
                    detector,
                    trigger=item,
                    pre_frames=list(history),
                    post_count=args.post_frames,
                    output=output,
                    hit_index=hit_index,
                    quality=args.jpeg_quality,
                    next_frame_id=frame_id,
                )
                session["hits"].append({"hit": hit_index, **manifest})
                session["ended_after_hit"] = hit_index >= args.max_hits
                (output / "session.json").write_text(
                    json.dumps(session, ensure_ascii=False, indent=2), encoding="utf-8"
                )
                last_hit_at = time.monotonic()
                history.clear()
    except KeyboardInterrupt:
        session["stopped_by"] = "keyboard_interrupt"
    finally:
        camera.release()
        session["finished_at"] = time.time()
        session["frames_seen"] = frame_id
        session["hit_count"] = hit_index
        (output / "session.json").write_text(
            json.dumps(session, ensure_ascii=False, indent=2), encoding="utf-8"
        )
    print(f"saved {hit_index} orange hit burst(s) and {frame_id} frame(s)", flush=True)
    return 0


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        description="Save multi-frame evidence whenever the route orange detector accepts a block"
    )
    parser.add_argument("--route-config", default="config/route_v2.yaml")
    parser.add_argument("--camera-config", default="config/camera_config.yaml")
    parser.add_argument("--output", required=True)
    parser.add_argument("--duration-s", type=float, default=30.0)
    parser.add_argument("--pre-frames", type=int, default=3)
    parser.add_argument("--post-frames", type=int, default=8)
    parser.add_argument("--cooldown-s", type=float, default=2.0)
    parser.add_argument("--max-hits", type=int, default=10)
    parser.add_argument("--jpeg-quality", type=int, default=95)
    args = parser.parse_args(argv)
    if args.duration_s <= 0 or args.pre_frames < 0 or args.post_frames < 0:
        parser.error("duration and frame counts must be non-negative; duration must be positive")
    if args.cooldown_s < 0 or not 1 <= args.max_hits <= 100:
        parser.error("cooldown must be non-negative and max-hits must be 1..100")
    if not 50 <= args.jpeg_quality <= 100:
        parser.error("jpeg-quality must be 50..100")
    return capture(args)


if __name__ == "__main__":
    raise SystemExit(main())
