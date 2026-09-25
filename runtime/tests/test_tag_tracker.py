from rg_runtime.models import TagObservation
from rg_runtime.tag_tracker import TagTracker
from route_v2.vision_config import Rect, ScalarRange, TagVisionGate


def observation(tag_id: int, frame: int) -> TagObservation:
    return TagObservation(
        id=tag_id,
        family="36H11",
        corners_px=((10.0, 10.0), (20.0, 10.0), (20.0, 20.0), (10.0, 20.0)),
        center_px=(15.0, 15.0),
        decision_margin=0.0,
        timestamp_ns=frame,
        frame_index=frame,
    )


def test_tag_tracker_requires_three_consecutive_target_frames():
    tracker = TagTracker(target_id=2, confirm_frames=3)

    assert tracker.update((observation(2, 1),)).stable is False
    assert tracker.update((observation(2, 2),)).stable is False
    result = tracker.update((observation(2, 3),))

    assert result.stable is True
    assert result.observation.id == 2
    assert result.consecutive_frames == 3


def test_tag_tracker_ignores_other_ids_and_resets_after_excessive_misses():
    tracker = TagTracker(target_id=2, confirm_frames=2, max_missed_frames=1)
    tracker.update((observation(2, 1),))
    assert tracker.update((observation(9, 2),)).stable is False
    assert tracker.update((observation(2, 3),)).stable is False
    assert tracker.update((observation(2, 4),)).stable is True

    tracker.update(())
    reset = tracker.update(())
    assert reset.stable is False
    assert reset.consecutive_frames == 0


def test_tag_tracker_keeps_last_observation_for_one_missed_frame():
    tracker = TagTracker(target_id=2, confirm_frames=2, max_missed_frames=1)
    tracker.update((observation(2, 1),))
    confirmed = tracker.update((observation(2, 2),))
    missed = tracker.update(())

    assert confirmed.stable is True
    assert missed.stable is True
    assert missed.observation.frame_index == 2
    assert missed.missed_frames == 1


def gated_observation(tag_id: int, frame: int, *, center=(320.0, 240.0), edge=40.0):
    x, y = center
    half = edge / 2
    return TagObservation(
        id=tag_id,
        family="36H11",
        corners_px=((x - half, y - half), (x + half, y - half),
                    (x + half, y + half), (x - half, y + half)),
        center_px=center,
        decision_margin=20.0,
        timestamp_ns=frame,
        frame_index=frame,
    )


def test_tag_gate_requires_center_size_and_three_new_frames():
    gate = TagVisionGate(3, Rect(0.25, 0.25, 0.75, 0.75), ScalarRange(20, 80), 3)
    tracker = TagTracker(target_id=3, gate=gate, frame_size=(640, 480))

    assert not tracker.update((gated_observation(3, 1),)).stable
    assert not tracker.update((gated_observation(3, 1),)).stable
    assert not tracker.update((gated_observation(3, 2),)).stable
    assert tracker.update((gated_observation(3, 3),)).stable


def test_tag_gate_rejects_wrong_id_center_and_size():
    gate = TagVisionGate(4, Rect(0.25, 0.25, 0.75, 0.75), ScalarRange(20, 80), 1)
    tracker = TagTracker(target_id=4, gate=gate, frame_size=(640, 480))

    assert not tracker.update((gated_observation(3, 1),)).stable
    assert not tracker.update((gated_observation(4, 2, center=(20, 20)),)).stable
    assert not tracker.update((gated_observation(4, 3, edge=100),)).stable
    assert tracker.update((gated_observation(4, 4),)).stable
