import json
from dataclasses import replace

import cv2
import numpy as np

from rg_runtime.adapters import FakeMotionAdapter
from rg_runtime.blocks import BlockDetector, ProfiledBlockDetector, StableBlockDetector
from rg_runtime.models import (
    BlockColor,
    MotionCommand,
    MotionMode,
    TaskAction,
    TaskCommand,
)
from route_v2.vision_config import BlockVisionProfile, ColorBand, Rect, ScalarRange


def color_frame(bgr, *, rectangle=(60, 50, 140, 150), noise=False):
    frame = np.zeros((200, 220, 3), dtype=np.uint8)
    if noise:
        cv2.rectangle(frame, (5, 5), (12, 12), bgr, -1)
    x1, y1, x2, y2 = rectangle
    cv2.rectangle(frame, (x1, y1), (x2, y2), bgr, -1)
    return frame


def test_detector_returns_orange_block_and_rejects_tiny_noise():
    detector = BlockDetector(min_area=500)
    observations = detector.detect(color_frame((0, 140, 255), noise=True))

    assert len(observations) == 1
    block = observations[0]
    assert block.color == BlockColor.ORANGE
    assert block.bounding_box == (60, 50, 81, 101)
    assert block.center_px == (100.0, 100.0)
    assert 0.0 <= block.confidence <= 1.0


def test_stable_detector_requires_three_matching_frames():
    detector = StableBlockDetector(confirm_frames=3, min_area=500)
    frame = color_frame((255, 0, 255))

    assert detector.update(frame) is None
    assert detector.update(frame) is None
    stable = detector.update(frame)

    assert stable is not None
    assert stable.color == BlockColor.PURPLE


def test_fake_adapter_records_commands_and_jsonl(tmp_path):
    adapter = FakeMotionAdapter()
    adapter.send_motion(
        MotionCommand(MotionMode.STOP, None, None, "test", timestamp_ms=1)
    )
    adapter.send_task(
        TaskCommand(TaskAction.NONE, BlockColor.NONE, "idle", timestamp_ms=1)
    )
    path = tmp_path / "events.jsonl"
    adapter.write_jsonl(path)

    lines = path.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 2
    assert json.loads(lines[0])["type"] == "motion"
    assert json.loads(lines[1])["type"] == "task"
    assert [event["type"] for event in adapter.events] == ["motion", "task"]


def diagnostic_profile(*, min_rectangularity=0.45, roi=(0, 0, 1, 1), bottom_y=None,
                       near_field=None, min_near_field_fill=0.0):
    return BlockVisionProfile(
        roi=Rect(*roi),
        capture_window=Rect(0.4, 0.3, 0.6, 0.9),
        hsv_bands=(ColorBand((5, 220, 80), (35, 255, 255)),),
        lab_bands=(ColorBand((140, 120, 130), (170, 145, 155)),),
        area_px=ScalarRange(200, 50000),
        aspect_ratio=ScalarRange(0.3, 3.0),
        height_px=ScalarRange(10, 190),
        center_y=ScalarRange(0.05, 0.95),
        min_rectangularity=min_rectangularity,
        morphology_kernel=3,
        bottom_y=None if bottom_y is None else ScalarRange(*bottom_y),
        near_field=None if near_field is None else Rect(*near_field),
        min_near_field_fill=min_near_field_fill,
    )


def test_low_saturation_orange_can_enter_through_lab_band():
    detector = ProfiledBlockDetector(diagnostic_profile(), color=BlockColor.ORANGE)
    result = detector.detect(color_frame((120, 140, 160)), frame_index=7)

    assert len(result.accepted) == 1
    assert result.accepted[0].color is BlockColor.ORANGE
    assert result.accepted[0].color_channels == ("lab",)
    assert result.accepted[0].frame_index == 7


def test_low_saturation_orange_can_enter_through_ycrcb_band():
    profile = replace(
        diagnostic_profile(),
        hsv_bands=(),
        lab_bands=(),
        ycrcb_bands=(ColorBand((150, 131, 115), (250, 150, 132)),),
    )
    bgr = cv2.cvtColor(
        np.uint8([[[200, 134, 124]]]), cv2.COLOR_YCrCb2BGR
    )[0, 0].tolist()
    detector = ProfiledBlockDetector(profile, color=BlockColor.ORANGE)

    result = detector.detect(color_frame(bgr))

    assert len(result.accepted) == 1
    assert result.accepted[0].color_channels == ("ycrcb",)


def test_profile_detector_clips_candidates_to_roi():
    detector = ProfiledBlockDetector(
        diagnostic_profile(roi=(0.5, 0, 1, 1)), color=BlockColor.ORANGE
    )
    result = detector.detect(color_frame((120, 140, 160), rectangle=(5, 40, 70, 160)))

    assert result.accepted == ()


def test_candidate_reports_rectangularity_rejection():
    image = np.zeros((200, 220, 3), dtype=np.uint8)
    cv2.circle(image, (110, 100), 35, (120, 140, 160), -1)
    detector = ProfiledBlockDetector(
        diagnostic_profile(min_rectangularity=0.85), color=BlockColor.ORANGE
    )
    result = detector.detect(image)

    assert result.accepted == ()
    assert result.rejected[0].reason == "rectangularity_below_min"
    assert result.rejected[0].metrics["rectangularity"] < 0.85


def test_near_field_gate_rejects_a_far_block_and_accepts_a_low_block():
    detector = ProfiledBlockDetector(
        diagnostic_profile(bottom_y=(0.8, 1.0)), color=BlockColor.ORANGE
    )

    far = detector.detect(
        color_frame((0, 140, 255), rectangle=(60, 20, 140, 100))
    )
    near = detector.detect(
        color_frame((0, 140, 255), rectangle=(60, 100, 140, 190))
    )

    assert far.accepted == ()
    assert far.rejected[0].reason == "bottom_y_below_min"
    assert far.rejected[0].metrics["bottom_y"] < 0.8
    assert len(near.accepted) == 1


def test_near_field_fill_rejects_a_bbox_pulled_down_by_sparse_color():
    profile = diagnostic_profile(
        bottom_y=(0.8, 1.0),
        near_field=(0.0, 0.7, 1.0, 1.0),
        min_near_field_fill=0.1,
    )
    detector = ProfiledBlockDetector(profile, color=BlockColor.ORANGE)
    sparse = color_frame((0, 140, 255), rectangle=(60, 20, 140, 100))
    cv2.rectangle(sparse, (98, 100), (102, 190), (0, 140, 255), -1)
    near = color_frame((0, 140, 255), rectangle=(60, 100, 140, 190))

    sparse_result = detector.detect(sparse)
    near_result = detector.detect(near)

    assert sparse_result.accepted == ()
    assert sparse_result.rejected[0].reason == "near_field_fill_below_min"
    assert sparse_result.rejected[0].metrics["near_field_fill"] < 0.1
    assert len(near_result.accepted) == 1


def test_small_rejected_contours_skip_full_frame_candidate_masks():
    class CountingCv2:
        def __init__(self):
            self.draw_calls = 0

        def __getattr__(self, name):
            return getattr(cv2, name)

        def drawContours(self, *args, **kwargs):
            self.draw_calls += 1
            return cv2.drawContours(*args, **kwargs)

    wrapped_cv2 = CountingCv2()
    profile = replace(
        diagnostic_profile(
            near_field=(0.0, 0.7, 1.0, 1.0), min_near_field_fill=0.1,
        ),
        centroid_hsv_bands=(ColorBand((5, 220, 80), (35, 255, 255)),),
        centroid_min_area_px=200,
    )
    frame = color_frame((0, 140, 255), rectangle=(60, 80, 140, 190))
    for y in (10, 25, 40, 55):
        for x in (5, 20, 35, 155, 170, 185, 200):
            cv2.rectangle(frame, (x, y), (x + 4, y + 4), (0, 140, 255), -1)

    result = ProfiledBlockDetector(
        profile, color=BlockColor.ORANGE, cv2_module=wrapped_cv2,
    ).detect(frame)

    assert len(result.accepted) == 1
    assert len(result.rejected) >= 20
    # Near-field fill and bright-core centroid share the one accepted contour mask.
    assert wrapped_cv2.draw_calls == 1


def test_centroid_core_does_not_change_full_contour_acceptance_gates():
    profile = replace(
        diagnostic_profile(),
        hsv_bands=(ColorBand((110, 20, 70), (150, 180, 220)),),
        lab_bands=(),
        center_y=ScalarRange(0.45, 0.75),
        centroid_hsv_bands=(ColorBand((110, 20, 110), (150, 180, 220)),),
        centroid_min_area_px=1000,
    )
    frame = np.zeros((200, 220, 3), dtype=np.uint8)
    dark = cv2.cvtColor(
        np.uint8([[[128, 60, 79]]]), cv2.COLOR_HSV2BGR
    )[0, 0].tolist()
    bright = cv2.cvtColor(
        np.uint8([[[128, 60, 178]]]), cv2.COLOR_HSV2BGR
    )[0, 0].tolist()
    cv2.rectangle(frame, (60, 40), (140, 160), dark, -1)
    cv2.rectangle(frame, (60, 40), (140, 80), bright, -1)

    result = ProfiledBlockDetector(profile, color=BlockColor.PURPLE).detect(frame)

    assert len(result.accepted) == 1
    assert result.accepted[0].center_px[1] == 60.0


def test_centroid_core_below_minimum_area_falls_back_to_full_contour():
    profile = replace(
        diagnostic_profile(),
        hsv_bands=(ColorBand((110, 20, 70), (150, 180, 220)),),
        lab_bands=(),
        centroid_hsv_bands=(ColorBand((110, 20, 110), (150, 180, 220)),),
        centroid_min_area_px=1000,
    )
    frame = np.zeros((200, 220, 3), dtype=np.uint8)
    dark = cv2.cvtColor(
        np.uint8([[[128, 60, 79]]]), cv2.COLOR_HSV2BGR
    )[0, 0].tolist()
    bright = cv2.cvtColor(
        np.uint8([[[128, 60, 178]]]), cv2.COLOR_HSV2BGR
    )[0, 0].tolist()
    cv2.rectangle(frame, (60, 40), (140, 160), dark, -1)
    cv2.rectangle(frame, (95, 45), (105, 55), bright, -1)

    result = ProfiledBlockDetector(profile, color=BlockColor.PURPLE).detect(frame)

    assert len(result.accepted) == 1
    assert result.accepted[0].center_px == (100.0, 100.0)
