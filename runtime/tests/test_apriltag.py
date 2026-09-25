from dataclasses import replace

import cv2
import numpy as np

from rg_runtime.apriltag import AprilTagDetector
from rg_runtime.config import CameraRuntimeConfig


def marker_frame(marker_id=3):
    dictionary = cv2.aruco.getPredefinedDictionary(cv2.aruco.DICT_APRILTAG_36h11)
    marker = cv2.aruco.generateImageMarker(dictionary, marker_id, 140)
    frame = np.full((240, 240), 255, dtype=np.uint8)
    frame[50:190, 50:190] = marker
    return cv2.cvtColor(frame, cv2.COLOR_GRAY2BGR)


def calibrated_camera():
    return CameraRuntimeConfig(
        camera=0,
        width=240,
        height=240,
        fps=30.0,
        pixel_format="MJPG",
        camera_matrix=np.array([[200.0, 0.0, 120.0], [0.0, 200.0, 120.0], [0.0, 0.0, 1.0]]),
        distortion_coefficients=np.zeros((1, 5), dtype=float),
        translation_mm=(0.0, 0.0, 0.0),
        rotation_deg=(0.0, 0.0, 0.0),
        tag_size_mm=20.0,
    )


def test_detects_expected_tag_and_preserves_frame_metadata():
    observations = AprilTagDetector().detect(marker_frame(), timestamp_ns=123, frame_index=7)

    assert len(observations) == 1
    observation = observations[0]
    assert observation.id == 3
    assert observation.family == "36H11"
    assert len(observation.corners_px) == 4
    assert observation.center_px == (119.5, 119.5)
    assert observation.timestamp_ns == 123
    assert observation.frame_index == 7
    assert observation.pose_camera is None


def test_allowed_ids_filter_detections():
    assert AprilTagDetector(allowed_ids={4}).detect(marker_frame()) == ()


def test_calibration_enables_optional_pose_output():
    observations = AprilTagDetector(camera_config=calibrated_camera()).detect(marker_frame())

    assert len(observations) == 1
    assert observations[0].pose_camera is not None
    assert len(observations[0].pose_camera.tvec) == 3
