"""AprilTag 36H11 detection with optional camera pose estimation."""

from __future__ import annotations

from typing import Iterable

import numpy as np

from .config import CameraRuntimeConfig
from .models import PoseCamera, TagObservation


class AprilTagDetector:
    def __init__(
        self,
        camera_config: CameraRuntimeConfig | None = None,
        allowed_ids: Iterable[int] | None = None,
        cv2_module=None,
    ) -> None:
        if cv2_module is None:
            import cv2 as cv2_module
        self._cv2 = cv2_module
        self.camera_config = camera_config
        self.allowed_ids = None if allowed_ids is None else frozenset(int(value) for value in allowed_ids)
        aruco = self._cv2.aruco
        self._dictionary = aruco.getPredefinedDictionary(aruco.DICT_APRILTAG_36h11)
        if hasattr(aruco, "ArucoDetector"):
            parameters = aruco.DetectorParameters()
            self._detector = aruco.ArucoDetector(self._dictionary, parameters)
        else:  # pragma: no cover - compatibility with older OpenCV
            self._detector = None

    def detect(
        self,
        frame,
        *,
        timestamp_ns: int = 0,
        frame_index: int = 0,
    ) -> tuple[TagObservation, ...]:
        if frame is None or not hasattr(frame, "shape"):
            raise ValueError("frame must be an image array")
        image = frame
        config = self.camera_config
        if config is not None:
            image = self._cv2.undistort(
                frame, config.camera_matrix, config.distortion_coefficients
            )
        gray = image
        if len(image.shape) == 3:
            gray = self._cv2.cvtColor(image, self._cv2.COLOR_BGR2GRAY)
        if self._detector is not None:
            corners, ids, _rejected = self._detector.detectMarkers(gray)
        else:  # pragma: no cover - compatibility with older OpenCV
            corners, ids, _rejected = self._cv2.aruco.detectMarkers(
                gray, self._dictionary
            )
        if ids is None:
            return ()

        observations = []
        for marker_corners, marker_id in zip(corners, ids.reshape(-1)):
            tag_id = int(marker_id)
            if self.allowed_ids is not None and tag_id not in self.allowed_ids:
                continue
            points = np.asarray(marker_corners, dtype=float).reshape(4, 2)
            center = tuple(float(value) for value in points.mean(axis=0))
            pose = self._estimate_pose(points) if config is not None else None
            observations.append(
                TagObservation(
                    id=tag_id,
                    family="36H11",
                    corners_px=tuple(tuple(float(value) for value in point) for point in points),
                    center_px=center,
                    decision_margin=0.0,
                    timestamp_ns=timestamp_ns,
                    frame_index=frame_index,
                    pose_camera=pose,
                )
            )
        return tuple(observations)

    def _estimate_pose(self, corners: np.ndarray) -> PoseCamera | None:
        config = self.camera_config
        if config is None or not config.tag_size_mm:
            return None
        half = float(config.tag_size_mm) / 2.0
        object_points = np.array(
            [
                (-half, half, 0.0),
                (half, half, 0.0),
                (half, -half, 0.0),
                (-half, -half, 0.0),
            ],
            dtype=np.float64,
        )
        try:
            success, rvec, tvec = self._cv2.solvePnP(
                object_points,
                corners.astype(np.float64),
                config.camera_matrix,
                config.distortion_coefficients,
                flags=getattr(self._cv2, "SOLVEPNP_IPPE_SQUARE", 7),
            )
        except self._cv2.error:
            return None
        if not success:
            return None
        return PoseCamera(
            rvec=tuple(float(value) for value in np.asarray(rvec).reshape(-1)[:3]),
            tvec=tuple(float(value) for value in np.asarray(tvec).reshape(-1)[:3]),
        )
