"""The orange pickup photo recorder: one photo per decision, off the control loop.

Written against the 2026-09-20 field question -- the route confirmed the same
orange candidate three times without moving, and only an image could say whether
a block was really there.  These tests pin the two properties that matter: the
producer side never blocks or raises, and each offer lands as one raw + one
overlay JPEG with the decision that triggered it recorded next to it.

The encoder is the real one (cv2 and numpy are present in the dev environment);
what is stubbed is only the frame, so nothing here depends on a camera.
"""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pytest

from route_v2.evidence import OrangeEvidenceRecorder, draw_overlay, json_safe


def frame(height=720, width=1280):
    return np.zeros((height, width, 3), dtype="uint8")


def record(**overrides):
    base = {
        "trigger": "orange_pickup_ready",
        "state": "PICKUP_2_VISION_ONLY",
        "pending_action": "PICK_ORANGE_1",
        "pickup_phase": "READY",
        "pickup_reason": "capture_window_confirmed",
        "block": {"color": "orange", "bounding_box": [475, 202, 565, 504],
                  "area": 219056.5, "confidence": 0.838},
    }
    base.update(overrides)
    return base


def read_index(directory: Path) -> list[dict]:
    return [json.loads(line) for line in (directory / "index.jsonl").read_text().splitlines()]


def test_one_offer_lands_as_a_paired_photo_plus_its_decision(tmp_path):
    output = tmp_path / "evidence"
    recorder = OrangeEvidenceRecorder(output, max_frames=10)
    recorder.start()
    assert recorder.offer(frame(), frame_id=2137, captured_at=123.5, record=record()) is True
    recorder.close()

    raw = output / "pickup_001_frame_002137_raw.jpg"
    overlay = output / "pickup_001_frame_002137_overlay.jpg"
    assert raw.stat().st_size > 0
    assert overlay.stat().st_size > 0

    index = read_index(output)
    assert len(index) == 1
    assert index[0]["frame_id"] == 2137
    assert index[0]["raw"] == raw.name
    assert index[0]["record"]["pending_action"] == "PICK_ORANGE_1"
    assert index[0]["record"]["block"]["area"] == 219056.5

    assert recorder.stats == {"offered": 1, "written": 1, "dropped": 0, "failed": 0}
    summary = json.loads((output / "session.json").read_text())
    assert summary["written"] == 1
    assert summary["jpeg_quality"] == 90


def test_the_overlay_marks_the_box_the_decision_was_made_on(tmp_path):
    """The overlay must differ from the raw frame, or it proves nothing."""
    output = tmp_path / "ev"
    recorder = OrangeEvidenceRecorder(output, max_frames=4)
    recorder.start()
    recorder.offer(frame(), frame_id=7, captured_at=1.0, record=record())
    recorder.close()
    raw = (output / "pickup_001_frame_000007_raw.jpg").read_bytes()
    overlay = (output / "pickup_001_frame_000007_overlay.jpg").read_bytes()
    assert raw != overlay


def test_offer_stops_at_max_frames_rather_than_filling_the_disk(tmp_path):
    recorder = OrangeEvidenceRecorder(tmp_path / "ev", max_frames=2)
    recorder.start()
    for frame_id in range(5):
        recorder.offer(frame(), frame_id=frame_id, captured_at=0.0, record=record())
    recorder.close()
    assert recorder.stats["offered"] == 2
    assert len(read_index(tmp_path / "ev")) == 2


def test_a_slow_writer_drops_frames_instead_of_blocking_the_route(tmp_path):
    """No writer thread: the queue must fill and then shed, never block."""
    recorder = OrangeEvidenceRecorder(tmp_path / "ev", max_frames=100, queue_size=2)
    for frame_id in range(5):
        recorder.offer(frame(), frame_id=frame_id, captured_at=0.0, record=record())
    stats = recorder.stats
    assert stats["offered"] == 5
    assert stats["dropped"] == 3


def test_close_is_idempotent_and_never_started_is_safe(tmp_path):
    recorder = OrangeEvidenceRecorder(tmp_path / "ev", max_frames=2)
    recorder.start()
    recorder.close()
    recorder.close()
    assert (tmp_path / "ev" / "session.json").exists()

    untouched = OrangeEvidenceRecorder(tmp_path / "ev2", max_frames=2)
    untouched.close()
    assert (tmp_path / "ev2" / "session.json").exists()


def test_an_empty_record_draws_nothing_and_does_not_raise():
    plain = frame(8, 8)
    assert draw_overlay(_NoDrawCv2(), plain, {}) is not None


class _NoDrawCv2:
    def rectangle(self, *args, **kwargs):
        raise AssertionError("no box should be drawn for an empty record")

    def putText(self, *args, **kwargs):
        raise AssertionError("no label should be drawn for an empty record")


def test_json_safe_handles_the_shapes_the_detector_returns():
    assert json_safe({"a": (1, 2), "b": None}) == {"a": [1, 2], "b": None}

    class WithValue:
        value = "ready"

    assert json_safe(WithValue()) == "ready"


def test_quality_bounds_are_enforced(tmp_path):
    with pytest.raises(ValueError):
        OrangeEvidenceRecorder(tmp_path / "ev", quality=10)
