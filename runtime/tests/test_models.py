import json
from dataclasses import FrozenInstanceError

import pytest

from rg_runtime.models import (
    BlockColor,
    LineIntersection,
    LineState,
    MotionCommand,
    MotionMode,
    PoseCamera,
    TagObservation,
    TaskAction,
    TaskCommand,
)


def test_observations_and_commands_serialize_to_json_native_values():
    observation = TagObservation(
        id=3,
        family="36H11",
        corners_px=((10.0, 20.0), (30.0, 20.0), (30.0, 40.0), (10.0, 40.0)),
        center_px=(20.0, 30.0),
        decision_margin=42.5,
        timestamp_ns=100,
        frame_index=7,
    )
    line = LineState(
        line_error=0.2,
        sensor_mask=3,
        intersection=LineIntersection.NONE,
        line_lost=False,
        turn_completed=False,
        timestamp_ms=10,
    )
    motion = MotionCommand(
        mode=MotionMode.FORWARD,
        speed=0.4,
        duration_ms=100,
        reason="test",
        timestamp_ms=10,
    )
    task = TaskCommand(
        action=TaskAction.NONE,
        target_color=BlockColor.NONE,
        reason="idle",
        timestamp_ms=10,
    )

    payload = {
        "tag": observation.to_dict(),
        "line": line.to_dict(),
        "motion": motion.to_dict(),
        "task": task.to_dict(),
    }
    encoded = json.dumps(payload)
    assert '"id": 3' in encoded
    assert payload["line"]["intersection"] == "none"
    assert payload["motion"]["mode"] == "forward"
    assert payload["task"]["target_color"] == "none"


def test_motion_command_rejects_speed_outside_normalized_range():
    with pytest.raises(ValueError, match="speed"):
        MotionCommand(
            mode=MotionMode.FORWARD,
            speed=1.01,
            duration_ms=None,
            reason="invalid",
            timestamp_ms=0,
        )


def test_negative_timestamps_are_rejected():
    with pytest.raises(ValueError, match="timestamp"):
        LineState(
            line_error=None,
            sensor_mask=0,
            intersection=LineIntersection.NONE,
            line_lost=True,
            turn_completed=False,
            timestamp_ms=-1,
        )


def test_contract_objects_are_immutable():
    observation = TagObservation(
        id=1,
        family="36H11",
        corners_px=((0.0, 0.0),) * 4,
        center_px=(0.0, 0.0),
        decision_margin=1.0,
        timestamp_ns=0,
        frame_index=0,
    )
    with pytest.raises(FrozenInstanceError):
        observation.id = 2


def test_tag_pose_serializes_to_json_native_values():
    observation = TagObservation(
        id=3,
        family="36H11",
        corners_px=((0.0, 0.0),) * 4,
        center_px=(0.0, 0.0),
        decision_margin=1.0,
        timestamp_ns=0,
        frame_index=0,
        pose_camera=PoseCamera((0.1, 0.2, 0.3), (1.0, 2.0, 3.0)),
    )

    encoded = json.dumps(observation.to_dict())
    assert '"rvec": [0.1, 0.2, 0.3]' in encoded
    assert '"tvec": [1.0, 2.0, 3.0]' in encoded
