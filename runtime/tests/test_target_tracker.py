from rg_runtime.models import BlockColor, BlockObservation
from route_v2.target_tracker import BlockTargetState, BlockTargetTracker


def block(x, y=100, *, area=1600, frame=1):
    return BlockObservation(
        color=BlockColor.PURPLE,
        bounding_box=(x - 20, y - 20, 40, 40),
        center_px=(float(x), float(y)),
        area=area,
        confidence=0.9,
        frame_index=frame,
    )


def test_tracker_locks_after_three_new_frames():
    tracker = BlockTargetTracker(confirm_frames=3, capture_center_px=(100, 100))
    assert tracker.update((block(102, frame=1),)).state is BlockTargetState.CANDIDATE
    assert tracker.update((block(102, frame=1),)).consecutive_frames == 1
    assert tracker.update((block(103, frame=2),)).state is BlockTargetState.CANDIDATE
    result = tracker.update((block(101, frame=3),))
    assert result.state is BlockTargetState.LOCKED


def test_locked_block_does_not_jump_to_other_candidate():
    tracker = BlockTargetTracker(confirm_frames=2, capture_center_px=(100, 100))
    tracker.update((block(100, frame=1),))
    tracker.update((block(102, frame=2),))

    result = tracker.update((block(105, area=1200, frame=3), block(400, area=9000, frame=3)))

    assert result.state is BlockTargetState.LOCKED
    assert result.target.center_px[0] < 200


def test_locked_target_becomes_lost_instead_of_switching_identity():
    tracker = BlockTargetTracker(confirm_frames=1, capture_center_px=(100, 100), max_missed_frames=0)
    tracker.update((block(100, frame=1),))

    result = tracker.update((block(400, frame=2),))

    assert result.state is BlockTargetState.LOST
    assert result.target is None
