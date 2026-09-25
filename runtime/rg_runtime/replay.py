"""Offline image/video replay for navigation dry-runs."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Mapping

import cv2

from .adapters import FakeMotionAdapter
from .apriltag import AprilTagDetector
from .config import ConfigError, load_camera_config, load_route_config
from .models import LineIntersection, LineState
from .state_machine import RouteStateMachine


class ReplayError(ValueError):
    """Raised when a replay cannot be started or decoded."""


def _default_config(name: str) -> Path:
    return Path(__file__).resolve().parents[1] / "config" / name


def _default_line(now_ms: int) -> LineState:
    return LineState(
        line_error=None,
        sensor_mask=0,
        intersection=LineIntersection.NONE,
        line_lost=True,
        turn_completed=False,
        timestamp_ms=now_ms,
    )


def _frames(path: Path):
    suffix = path.suffix.lower()
    if suffix in {".jpg", ".jpeg", ".png", ".bmp", ".webp"}:
        frame = cv2.imread(str(path))
        if frame is None:
            raise ReplayError(f"unable to decode image: {path}")
        yield frame
        return
    capture = cv2.VideoCapture(str(path))
    if not capture.isOpened():
        raise ReplayError(f"unable to open video: {path}")
    try:
        while True:
            ok, frame = capture.read()
            if not ok:
                break
            yield frame
    finally:
        capture.release()


def run_replay(
    input_path: str | Path,
    route_name: str,
    output_path: str | Path,
    *,
    line_events: Mapping[int, LineState] | None = None,
    route_path: str | Path | None = None,
    camera_path: str | Path | None = None,
) -> dict:
    source = Path(input_path).expanduser()
    if not source.is_file():
        raise ReplayError(f"input does not exist: {source}")
    route_file = Path(route_path or _default_config("routes.yaml"))
    camera_file = Path(camera_path or _default_config("camera_config.yaml"))
    try:
        routes = load_route_config(route_file)
        route = routes[route_name]
        camera = load_camera_config(camera_file)
    except (ConfigError, KeyError) as exc:
        raise ReplayError(str(exc)) from exc

    detector = AprilTagDetector(camera_config=camera)
    machine = RouteStateMachine(route)
    adapter = FakeMotionAdapter()
    destination = Path(output_path).expanduser()
    destination.parent.mkdir(parents=True, exist_ok=True)
    total_frames = 0
    with destination.open("w", encoding="utf-8") as stream:
        for frame_index, frame in enumerate(_frames(source)):
            now_ms = int(round(frame_index * 1000 / 30.0))
            timestamp_ns = frame_index * 1_000_000_000 // 30
            errors: list[str] = []
            try:
                tags = detector.detect(frame, timestamp_ns=timestamp_ns, frame_index=frame_index)
            except (ValueError, cv2.error) as exc:
                tags = ()
                errors.append(f"tag detection: {exc}")
            line = (line_events or {}).get(frame_index, _default_line(now_ms))
            if line.timestamp_ms != now_ms:
                line = LineState(
                    line_error=line.line_error,
                    sensor_mask=line.sensor_mask,
                    intersection=line.intersection,
                    line_lost=line.line_lost,
                    turn_completed=line.turn_completed,
                    timestamp_ms=now_ms,
                )
            decision = machine.step(now_ms, line, tags)
            for command in decision.motion_commands:
                adapter.send_motion(command)
            for command in decision.task_commands:
                adapter.send_task(command)
            record = {
                "frame_index": frame_index,
                "timestamp_ns": timestamp_ns,
                "tag_observations": [observation.to_dict() for observation in tags],
                "line_state": line.to_dict(),
                "state": decision.state.value,
                "previous_state": decision.previous_state.value,
                "motion_commands": [command.to_dict() for command in decision.motion_commands],
                "task_commands": [command.to_dict() for command in decision.task_commands],
                "reason": decision.reason,
                "errors": errors,
            }
            stream.write(json.dumps(record, ensure_ascii=False) + "\n")
            total_frames += 1
    return {"frames": total_frames, "output": str(destination), "events": len(adapter.events)}


def _load_line_events(path: str | Path | None) -> dict[int, LineState]:
    if path is None:
        return {}
    raw = json.loads(Path(path).read_text(encoding="utf-8"))
    events = {}
    for key, value in raw.items():
        events[int(key)] = LineState(
            line_error=value.get("line_error"),
            sensor_mask=int(value.get("sensor_mask", 0)),
            intersection=LineIntersection(value.get("intersection", "none")),
            line_lost=bool(value.get("line_lost", False)),
            turn_completed=bool(value.get("turn_completed", False)),
            timestamp_ms=int(value.get("timestamp_ms", 0)),
        )
    return events


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Dry-run RoboGame navigation on an image or video.")
    parser.add_argument("--input", required=True, help="image or video path")
    parser.add_argument("--route", required=True, choices=("zone_1", "zone_2", "purple_required"))
    parser.add_argument("--output", required=True, help="JSONL output path")
    parser.add_argument("--line-events", help="optional JSON mapping frame index to LineState fields")
    args = parser.parse_args(argv)
    try:
        result = run_replay(
            args.input,
            args.route,
            args.output,
            line_events=_load_line_events(args.line_events),
        )
    except (ReplayError, OSError, ValueError) as exc:
        parser.error(str(exc))
    print(f"Frames: {result['frames']}")
    print(f"JSONL: {result['output']}")
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
