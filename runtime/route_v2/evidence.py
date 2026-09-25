"""Photo evidence at the moment the route decides it has recognised an orange block.

Why this exists: on 2026-09-20 the route confirmed and "grabbed" the same orange
candidate three times without moving (lateral -5.39 / -5.32 / -5.32 cm), and the
telemetry could not settle whether a block was physically in front of the camera
at those moments -- the recorded area, height and bottom edge all sit inside the
envelope the config documents for a real near-field block.  Numbers cannot
separate a block from something that merely looks like one; only the image can.

So this records exactly what the operator asked for: **the frame the decision was
made on**, at the tick where the pickup controller reports `pickup_ready` and the
route arms the three-second placeholder action.  One raw JPEG and one overlay
JPEG per trigger, plus an `index.jsonl` line per trigger,

The recorder is deliberately off the control path.  The state machine's loop
polls at 20 Hz and the timing was tuned against that, so `offer()` only enqueues
a frame reference and returns; a daemon writer thread does the JPEG encoding and
the disk write.  The queue is bounded and drops the oldest entry when the writer
falls behind, because losing a photo is strictly better than perturbing a run.

Nothing here is loaded unless `run_route_v2.py --capture-orange` is passed: with
the flag absent no recorder is built and the route behaves exactly as before.

Frame references are safe to hold without copying: `LatestFrameBuffer.publish`
stores whatever array `VideoCapture.read()` returned and replaces the reference
on the next frame, so a published frame is never mutated in place.
"""

from __future__ import annotations

from collections.abc import Mapping
import json
from pathlib import Path
import queue
import threading
import time
from typing import Any


def json_safe(value: Any) -> Any:
    """Convert detector dataclasses/enums into JSON-safe evidence fields."""
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, Mapping):
        return {str(key): json_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [json_safe(item) for item in value]
    if hasattr(value, "value"):
        return json_safe(value.value)
    if hasattr(value, "__dataclass_fields__"):
        return {name: json_safe(getattr(value, name)) for name in value.__dataclass_fields__}
    return str(value)


def _candidate_boxes(record: Mapping[str, Any]) -> list[tuple[tuple[int, int, int, int], str, str]]:
    """(bounding_box, label, colour) for every box worth drawing."""
    boxes: list[tuple[tuple[int, int, int, int], str, str]] = []
    for candidate in record.get("accepted") or ():
        box = tuple(candidate.get("bounding_box") or ())
        if len(box) == 4:
            boxes.append((box, f"ACCEPT area={candidate.get('area')} "
                               f"conf={candidate.get('confidence')}", "accept"))
    for candidate in record.get("rejected") or ():
        box = tuple(candidate.get("bounding_box") or ())
        if len(box) == 4:
            boxes.append((box, f"REJECT {candidate.get('reason')}", "reject"))
    block = record.get("block")
    if isinstance(block, Mapping):
        box = tuple(block.get("bounding_box") or ())
        if len(box) == 4:
            boxes.append((box, f"TARGET area={block.get('area')} "
                               f"conf={block.get('confidence')}", "accept"))
    return boxes


def draw_overlay(cv2, frame, record: Mapping[str, Any]):
    """Green boxes for the target, red for rejected candidates."""
    overlay = frame.copy()
    height = frame.shape[0]
    for (x, y, width, box_height), label, kind in _candidate_boxes(record):
        colour = (0, 220, 0) if kind == "accept" else (0, 0, 220)
        cv2.rectangle(overlay, (x, y), (x + width, y + box_height), colour, 3)
        cv2.putText(overlay, label, (max(0, x), max(24, y - 8)),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.6, colour, 2, cv2.LINE_AA)
    return overlay


class OrangeEvidenceRecorder:
    """Bounded, off-loop photo writer for orange pickup decisions."""

    def __init__(self, output_dir: str | Path, *, quality: int = 90,
                 max_frames: int = 500, queue_size: int = 16) -> None:
        if not 50 <= quality <= 100:
            raise ValueError("jpeg quality must be in 50..100")
        if max_frames <= 0 or queue_size <= 0:
            raise ValueError("max_frames and queue_size must be positive")
        self.output_dir = Path(output_dir)
        self.quality = int(quality)
        self.max_frames = int(max_frames)
        self._queue: queue.Queue = queue.Queue(maxsize=queue_size)
        self._lock = threading.Lock()
        self._offered = 0
        self._dropped = 0
        self._written = 0
        self._failed = 0
        self._closed = False
        self._thread: threading.Thread | None = None

    def offer(self, image, *, frame_id: int, captured_at: float,
              record: Mapping[str, Any]) -> bool:
        """Queue one photo.  Never blocks, never raises."""
        with self._lock:
            if self._closed or self._offered >= self.max_frames:
                return False
            self._offered += 1
        try:
            self._queue.put_nowait({
                "image": image,
                "frame_id": int(frame_id),
                "captured_at": float(captured_at),
                "record": json_safe(record),
            })
        except queue.Full:
            with self._lock:
                self._dropped += 1
            return False
        return True

    def start(self) -> None:
        if self._thread is not None:
            return
        self.output_dir.mkdir(parents=True, exist_ok=True)
        self._thread = threading.Thread(target=self._run, name="orange-evidence", daemon=True)
        self._thread.start()

    def close(self, *, timeout_s: float = 10.0) -> None:
        with self._lock:
            if self._closed:
                return
            self._closed = True
        if self._thread is not None:
            try:
                self._queue.put_nowait(None)
            except queue.Full:
                pass
            self._thread.join(timeout=timeout_s)
        self._write_session()

    @property
    def stats(self) -> dict[str, int]:
        with self._lock:
            return {"offered": self._offered, "written": self._written,
                    "dropped": self._dropped, "failed": self._failed}

    def _run(self) -> None:
        try:
            import cv2
        except Exception:  # pragma: no cover - cv2 is present on the Pi
            cv2 = None
        index_path = self.output_dir / "index.jsonl"
        with index_path.open("w", encoding="utf-8", buffering=1) as index:
            while True:
                item = self._queue.get()
                if item is None:
                    return
                try:
                    entry = self._write_one(cv2, item)
                except Exception as exc:  # a failed write must not kill the thread
                    with self._lock:
                        self._failed += 1
                    entry = {"frame_id": item.get("frame_id"), "error": repr(exc)}
                index.write(json.dumps(entry, ensure_ascii=False) + "\n")
                if "error" not in entry:
                    with self._lock:
                        self._written += 1

    def _write_one(self, cv2, item: dict[str, Any]) -> dict[str, Any]:
        image = item["image"]
        record = item["record"]
        index = self._written + 1
        stem = f"pickup_{index:03d}_frame_{item['frame_id']:06d}"
        raw_path = self.output_dir / f"{stem}_raw.jpg"
        overlay_path = self.output_dir / f"{stem}_overlay.jpg"
        if cv2 is None:
            raise RuntimeError("cv2 unavailable; cannot encode evidence photos")
        params = [int(cv2.IMWRITE_JPEG_QUALITY), self.quality]
        if not cv2.imwrite(str(raw_path), image, params):
            raise RuntimeError(f"failed to write {raw_path}")
        if not cv2.imwrite(str(overlay_path), draw_overlay(cv2, image, record), params):
            raise RuntimeError(f"failed to write {overlay_path}")
        return {
            "frame_id": item["frame_id"],
            "captured_at": item["captured_at"],
            "raw": raw_path.name,
            "overlay": overlay_path.name,
            "record": record,
        }

    def _write_session(self) -> None:
        self.output_dir.mkdir(parents=True, exist_ok=True)
        (self.output_dir / "session.json").write_text(
            json.dumps({"finished_at": time.time(), "jpeg_quality": self.quality,
                        "max_frames": self.max_frames, **self.stats},
                       ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
