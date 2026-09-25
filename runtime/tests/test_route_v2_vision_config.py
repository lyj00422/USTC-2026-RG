from pathlib import Path

import pytest
import yaml
import cv2
import numpy as np

from route_v2.config import load_route_v2_config
from rg_runtime.blocks import ProfiledBlockDetector
from rg_runtime.models import BlockColor
from route_v2.vision_config import ColorBand


ROOT = Path(__file__).parents[1]


def _patched_config(tmp_path: Path, dotted_key: str, value):
    source = yaml.safe_load((ROOT / "config" / "route_v2.yaml").read_text(encoding="utf-8"))
    node = source
    parts = dotted_key.split(".")
    for part in parts[:-1]:
        node = node[part]
    node[parts[-1]] = value
    target = tmp_path / "route_v2.yaml"
    target.write_text(yaml.safe_dump(source, sort_keys=False), encoding="utf-8")
    return target


def test_route_visual_profiles_are_independent_and_tag_scoped():
    cfg = load_route_v2_config(ROOT / "config" / "route_v2.yaml")

    assert cfg.vision.task_startup_timeout_s == 2.0
    assert cfg.vision.result_stall_timeout_s == 1.0
    assert set(cfg.vision.block_profiles) == {
        "purple_j3_prescan",
        "purple_pickup_close",
        "orange_pickup_close",
        "build_occupancy",
    }
    assert (
        cfg.vision.block_profiles["purple_j3_prescan"].roi
        != cfg.vision.block_profiles["purple_pickup_close"].roi
    )
    assert cfg.vision.tag_gates["pickup_seek_line"].target_id == 3
    assert cfg.vision.tag_gates["pickup_2_seek_line"].target_id == 4
    assert cfg.vision.tag_gates["pickup_seek_line"].confirm_frames == 3


def test_purple_prescan_area_rejects_recorded_empty_scene_noise():
    profile = load_route_v2_config(
        ROOT / "config" / "route_v2.yaml"
    ).vision.block_profiles["purple_j3_prescan"]

    # Latest field capture: empty-scene maximum 2955 px2, real block minimum
    # 13095 px2. Keep a useful margin instead of a 45 px boundary.
    assert 2955 < profile.area_px.minimum < 13095
    assert profile.area_px.minimum >= 6000


def test_purple_action_uses_recorded_window_and_safe_forward_limit():
    vision = load_route_v2_config(ROOT / "config" / "route_v2.yaml").vision

    assert vision.purple_action_package == "data/route_v2_actions/purple_pickup_v1"
    assert vision.purple_action_forward_speed == 20
    assert vision.purple_action_ack_timeout_s == 1.0
    window = vision.block_profiles["purple_pickup_close"].capture_window
    assert (window.left, window.top, window.right, window.bottom) == pytest.approx((
        0.4739738806, 0.4731343284, 0.5369402985, 0.5805970149,
    ))


def test_each_pickup_area_has_explicit_motion_guards():
    cfg = load_route_v2_config(ROOT / "config" / "route_v2.yaml")

    purple = cfg.vision.pickup_areas["purple"]
    orange = cfg.vision.pickup_areas["orange"]
    assert purple.search_right.max_distance_cm > 0
    assert purple.search_right.timeout_s > 0
    assert purple.search_left.max_distance_cm > 0
    assert purple.search_left.timeout_s > 0
    assert orange.search_right.max_distance_cm > 0
    assert orange.search_left.max_distance_cm > 0
    assert purple.search_speed == 40
    assert purple.search_speed > abs(purple.coarse_speed)
    assert purple.coarse_speed != 0
    assert purple.fine_speed != 0


def test_older_config_without_search_speed_uses_coarse_speed(tmp_path):
    source = yaml.safe_load((ROOT / "config" / "route_v2.yaml").read_text(encoding="utf-8"))
    del source["route_v2"]["vision"]["pickup_areas"]["purple"]["search_speed"]
    target = tmp_path / "route_v2.yaml"
    target.write_text(yaml.safe_dump(source, sort_keys=False), encoding="utf-8")

    purple = load_route_v2_config(target).vision.pickup_areas["purple"]

    assert purple.search_speed == purple.coarse_speed


def test_full_visual_route_refuses_uncalibrated_motion_bounds(tmp_path):
    path = _patched_config(tmp_path, "route_v2.vision.calibrated", False)
    with pytest.raises(ValueError, match="visual route is not calibrated"):
        load_route_v2_config(path, require_visual_calibration=True)


@pytest.mark.parametrize(
    ("key", "value"),
    [
        ("route_v2.vision.frame_timeout_s", 0),
        ("route_v2.vision.task_startup_timeout_s", 0),
        ("route_v2.vision.result_stall_timeout_s", 0.5),
        ("route_v2.vision.tag_gates.pickup_seek_line.confirm_frames", 0),
        ("route_v2.vision.pickup_areas.purple.search_left.max_distance_cm", 0),
        ("route_v2.vision.pickup_areas.orange.coarse_speed", 0),
        ("route_v2.vision.pickup_areas.purple.search_speed", 0),
        ("route_v2.vision.pickup_areas.orange.alignment_timeout_s", 0),
        ("route_v2.vision.purple_action_forward_speed", 21),
        ("route_v2.vision.purple_action_ack_timeout_s", 0),
        ("route_v2.vision.block_profiles.orange_pickup_close.bottom_y", [0.8, 1.1]),
        ("route_v2.vision.block_profiles.orange_pickup_close.min_near_field_fill", 1.1),
    ],
)
def test_invalid_visual_limits_are_rejected(tmp_path, key, value):
    with pytest.raises(ValueError):
        load_route_v2_config(_patched_config(tmp_path, key, value))


@pytest.mark.parametrize(
    ("profile_name", "color", "hsv"),
    [
        ("purple_pickup_close", BlockColor.PURPLE, (118, 40, 160)),
        ("orange_pickup_close", BlockColor.ORANGE, (8, 19, 209)),
    ],
)
def test_close_profiles_include_reference_media_low_saturation_colors(
    profile_name, color, hsv
):
    cfg = load_route_v2_config(ROOT / "config" / "route_v2.yaml")
    bgr = cv2.cvtColor(np.uint8([[hsv]]), cv2.COLOR_HSV2BGR)[0, 0].tolist()
    frame = np.zeros((720, 1280, 3), dtype=np.uint8)
    cv2.rectangle(frame, (320, 250), (900, 600), bgr, -1)

    result = ProfiledBlockDetector(
        cfg.vision.block_profiles[profile_name], color=color
    ).detect(frame)

    assert len(result.accepted) == 1


def test_orange_close_profile_includes_probe_measured_saturation():
    """The field sweep target must survive the orange HSV mask intact."""
    cfg = load_route_v2_config(ROOT / "config" / "route_v2.yaml")
    profile = cfg.vision.block_profiles["orange_pickup_close"]
    bgr = cv2.cvtColor(
        np.uint8([[[8, 120, 200]]]), cv2.COLOR_HSV2BGR
    )[0, 0].tolist()
    frame = np.zeros((720, 1280, 3), dtype=np.uint8)
    # Representative of sweep_20260922_204314 steps 6/7 after the complete
    # target is segmented: roughly 945 x 454 px in the close-pickup ROI.
    cv2.rectangle(frame, (126, 202), (1071, 656), bgr, -1)

    result = ProfiledBlockDetector(
        profile, color=BlockColor.ORANGE
    ).detect(frame)

    assert len(result.accepted) == 1


def test_build_occupancy_profile_accepts_a_connected_stack_silhouette():
    cfg = load_route_v2_config(ROOT / "config" / "route_v2.yaml")
    profile = cfg.vision.block_profiles["build_occupancy"]
    bgr = cv2.cvtColor(np.uint8([[[12, 85, 190]]]), cv2.COLOR_HSV2BGR)[0, 0].tolist()
    frame = np.zeros((720, 1280, 3), dtype=np.uint8)
    cv2.rectangle(frame, (410, 120), (870, 705), bgr, -1)

    result = ProfiledBlockDetector(profile, color=BlockColor.ORANGE).detect(frame)

    assert len(result.accepted) == 1


def test_purple_close_centroid_ignores_dark_reflection_connected_to_block():
    cfg = load_route_v2_config(ROOT / "config" / "route_v2.yaml")
    profile = cfg.vision.block_profiles["purple_pickup_close"]
    frame = np.zeros((720, 1280, 3), dtype=np.uint8)
    body = cv2.cvtColor(
        np.uint8([[[128, 60, 178]]]), cv2.COLOR_HSV2BGR
    )[0, 0].tolist()
    reflection = cv2.cvtColor(
        np.uint8([[[132, 62, 79]]]), cv2.COLOR_HSV2BGR
    )[0, 0].tolist()
    cv2.rectangle(frame, (260, 202), (1050, 578), body, -1)
    cv2.rectangle(frame, (260, 578), (1050, 705), reflection, -1)

    result = ProfiledBlockDetector(profile, color=BlockColor.PURPLE).detect(frame)

    assert len(result.accepted) == 1
    target = result.accepted[0]
    assert target.center_px[1] == pytest.approx(390, abs=5)
    assert profile.capture_window.top <= target.center_px[1] / 720
    assert target.center_px[1] / 720 <= profile.capture_window.bottom


def test_close_profiles_require_the_candidate_to_reach_the_near_field():
    cfg = load_route_v2_config(ROOT / "config" / "route_v2.yaml")
    profile = cfg.vision.block_profiles["orange_pickup_close"]
    bgr = cv2.cvtColor(np.uint8([[[8, 19, 209]]]), cv2.COLOR_HSV2BGR)[0, 0].tolist()
    frame = np.zeros((720, 1280, 3), dtype=np.uint8)
    cv2.rectangle(frame, (250, 202), (1000, 560), bgr, -1)

    result = ProfiledBlockDetector(profile, color=BlockColor.ORANGE).detect(frame)

    assert result.accepted == ()
    assert any(item.reason == "bottom_y_below_min" for item in result.rejected)


def test_purple_close_profile_keeps_the_measured_near_field_envelope():
    cfg = load_route_v2_config(ROOT / "config" / "route_v2.yaml")
    profile = cfg.vision.block_profiles["purple_pickup_close"]

    assert profile.area_px.minimum == 150000
    assert profile.height_px.minimum == 350
    assert profile.center_y.minimum == 0.52
    assert profile.center_y.maximum == 0.72
    assert profile.bottom_y is not None
    assert profile.bottom_y.minimum == 0.80
    assert profile.bottom_y.maximum == 1.00
    assert profile.near_field is not None
    assert profile.near_field.top == 0.70
    assert profile.near_field.bottom == 0.98
    assert profile.min_near_field_fill == 0.20


def test_orange_close_profile_encodes_video_calibrated_color_and_geometry():
    cfg = load_route_v2_config(ROOT / "config" / "route_v2.yaml")
    profile = cfg.vision.block_profiles["orange_pickup_close"]

    assert profile.ycrcb_bands == (
        ColorBand((150, 131, 115), (250, 150, 132)),
    )
    assert profile.area_px.minimum == 100000
    assert profile.area_px.maximum == 600000
    assert (
        profile.roi.left, profile.roi.top, profile.roi.right, profile.roi.bottom
    ) == pytest.approx((0.08, 0.28, 0.92, 0.98))
    # TRIAL, 2026-09-24: x shifted right by 0.02 (0.42/0.58 -> 0.44/0.60), centre
    # 640 -> 665.6 px.  The operator asked for the grab to land further left on the
    # block; under the alignment's sign convention (error = target_x - window centre,
    # negative drives LEFT) moving the centre RIGHT makes the car settle further left
    # relative to the block.  Revert by putting 0.42 / 0.58 back -- see the parameter's
    # own comment in config/route_v2.yaml for the measurements behind it.
    assert (
        profile.capture_window.left,
        profile.capture_window.top,
        profile.capture_window.right,
        profile.capture_window.bottom,
    ) == pytest.approx((0.44, 0.42, 0.60, 0.88))
    assert profile.aspect_ratio.minimum == 0.45
    assert profile.aspect_ratio.maximum == 2.2
    assert profile.height_px.minimum == 350
    assert profile.height_px.maximum == 520
    assert profile.center_y.minimum == 0.52
    assert profile.center_y.maximum == 0.72
    assert profile.bottom_y is not None
    assert profile.bottom_y.minimum == 0.80
    assert profile.bottom_y.maximum == 1.00
    assert (
        profile.near_field.left,
        profile.near_field.top,
        profile.near_field.right,
        profile.near_field.bottom,
    ) == pytest.approx((0.08, 0.70, 0.92, 0.98))
    assert profile.min_near_field_fill == 0.20
    assert profile.min_rectangularity == 0.52
    assert cfg.vision.pickup_areas["orange"].confirm_frames == 3


def test_orange_close_profile_does_not_enable_the_field_proven_false_lab_band():
    cfg = load_route_v2_config(ROOT / "config" / "route_v2.yaml")

    assert cfg.vision.block_profiles["orange_pickup_close"].lab_bands == ()


def test_purple_profiles_do_not_enable_ycrcb():
    cfg = load_route_v2_config(ROOT / "config" / "route_v2.yaml")

    assert cfg.vision.block_profiles["purple_j3_prescan"].ycrcb_bands == ()
    assert cfg.vision.block_profiles["purple_pickup_close"].ycrcb_bands == ()
