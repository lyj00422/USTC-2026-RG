import json

import cv2
import numpy as np
import pytest

from rg_runtime.models import LineIntersection, LineState
from rg_runtime.replay import ReplayError, run_replay


def test_image_replay_writes_structured_jsonl(tmp_path):
    image_path = tmp_path / "frame.png"
    output_path = tmp_path / "run.jsonl"
    cv2.imwrite(str(image_path), np.zeros((120, 160, 3), dtype=np.uint8))

    line_events = {
        0: LineState(
            line_error=0.0,
            sensor_mask=1,
            intersection=LineIntersection.NONE,
            line_lost=False,
            turn_completed=False,
            timestamp_ms=0,
        )
    }
    result = run_replay(image_path, "zone_1", output_path, line_events=line_events)

    assert result["frames"] == 1
    payload = json.loads(output_path.read_text(encoding="utf-8").splitlines()[0])
    assert payload["frame_index"] == 0
    assert payload["tag_observations"] == []
    assert payload["state"] == "INITIAL_RIGHT_TURN"
    assert "motion_commands" in payload
    assert payload["errors"] == []


def test_replay_rejects_missing_input(tmp_path):
    with pytest.raises(ReplayError, match="does not exist"):
        run_replay(tmp_path / "missing.png", "zone_1", tmp_path / "run.jsonl", line_events={})
