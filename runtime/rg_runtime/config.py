"""Load portable camera and route configuration files."""

from __future__ import annotations

from dataclasses import dataclass
import math
from pathlib import Path
from typing import Any

import numpy as np


class ConfigError(ValueError):
    """Raised when a runtime configuration is missing or invalid."""


@dataclass(frozen=True)
class CameraRuntimeConfig:
    camera: int
    width: int
    height: int
    fps: float
    pixel_format: str
    camera_matrix: np.ndarray
    distortion_coefficients: np.ndarray
    translation_mm: tuple[float, float, float]
    rotation_deg: tuple[float, float, float]
    tag_size_mm: float | None = None


@dataclass(frozen=True)
class TagNode:
    name: str
    state: str
    ids: tuple[int, ...]
    unverified: bool


@dataclass(frozen=True)
class RouteConfig:
    name: str
    line_hold_ms: int
    state_timeout_ms: int
    forward_speed: float
    turn_speed: float
    tag_nodes: tuple[TagNode, ...]


def _finite_tuple(values: Any, name: str, length: int) -> tuple[float, ...]:
    if not isinstance(values, (list, tuple)) or len(values) != length:
        raise ConfigError(f"{name} must contain {length} numbers")
    result = tuple(float(value) for value in values)
    if not all(math.isfinite(value) for value in result):
        raise ConfigError(f"{name} must contain finite numbers")
    return result


def load_camera_config(path: str | Path) -> CameraRuntimeConfig:
    config_path = Path(path).expanduser()
    if not config_path.is_file():
        raise ConfigError(f"camera config does not exist: {config_path}")

    try:
        import cv2
    except ImportError as exc:  # pragma: no cover - environment error
        raise ConfigError("OpenCV is required to load camera calibration") from exc

    storage = cv2.FileStorage(str(config_path), cv2.FILE_STORAGE_READ)
    if not storage.isOpened():
        raise ConfigError(f"unable to open camera config: {config_path}")
    try:
        camera = storage.getNode("camera")
        calibration = storage.getNode("calibration")
        extrinsics = storage.getNode("extrinsics")
        if camera.empty() or calibration.empty() or extrinsics.empty():
            raise ConfigError(f"{config_path}: camera, calibration and extrinsics are required")

        def real(parent, name: str) -> float:
            node = parent.getNode(name)
            if node.empty():
                raise ConfigError(f"{config_path}: missing {name}")
            return float(node.real())

        def string(parent, name: str) -> str:
            node = parent.getNode(name)
            value = node.string() if not node.empty() else ""
            if not value:
                raise ConfigError(f"{config_path}: missing {name}")
            return value

        camera_id = int(real(camera, "device"))
        width = int(real(camera, "width"))
        height = int(real(camera, "height"))
        fps = real(camera, "fps")
        pixel_format = string(camera, "pixel_format").upper()
        if camera_id < 0 or width <= 0 or height <= 0 or not math.isfinite(fps) or fps <= 0:
            raise ConfigError(f"{config_path}: invalid camera stream values")
        if pixel_format != "MJPG":
            raise ConfigError(f"{config_path}: pixel_format must be MJPG")

        matrix_node = calibration.getNode("camera_matrix")
        distortion_node = calibration.getNode("distortion_coefficients")
        if matrix_node.empty() or distortion_node.empty():
            raise ConfigError(f"{config_path}: camera calibration matrices are required")
        matrix = np.asarray(matrix_node.mat(), dtype=float)
        distortion = np.asarray(distortion_node.mat(), dtype=float)
        if matrix.shape != (3, 3):
            raise ConfigError(f"{config_path}: camera_matrix must be 3x3")
        if distortion.size not in (4, 5, 8, 12, 14):
            raise ConfigError(f"{config_path}: invalid distortion coefficient count")
        translation = _finite_tuple(
            [extrinsics.getNode("translation_mm").at(i).real() for i in range(3)],
            "translation_mm",
            3,
        )
        rotation = _finite_tuple(
            [extrinsics.getNode("rotation_deg").at(i).real() for i in range(3)],
            "rotation_deg",
            3,
        )
        tag_size_node = calibration.getNode("tag_size_mm")
        tag_size = float(tag_size_node.real()) if not tag_size_node.empty() else 0.0
        if not math.isfinite(tag_size) or tag_size < 0:
            raise ConfigError(f"{config_path}: tag_size_mm must be non-negative")
        return CameraRuntimeConfig(
            camera=camera_id,
            width=width,
            height=height,
            fps=fps,
            pixel_format=pixel_format,
            camera_matrix=matrix,
            distortion_coefficients=distortion,
            translation_mm=translation,
            rotation_deg=rotation,
            tag_size_mm=tag_size or None,
        )
    finally:
        storage.release()


def load_route_config(path: str | Path) -> dict[str, RouteConfig]:
    config_path = Path(path).expanduser()
    if not config_path.is_file():
        raise ConfigError(f"route config does not exist: {config_path}")
    try:
        import yaml
        data = yaml.safe_load(config_path.read_text(encoding="utf-8")) or {}
    except ImportError as exc:  # pragma: no cover - environment error
        raise ConfigError("PyYAML is required to load routes") from exc
    except yaml.YAMLError as exc:
        raise ConfigError(f"invalid route YAML: {config_path}") from exc

    raw_routes = data.get("routes") if isinstance(data, dict) else None
    if not isinstance(raw_routes, dict) or not raw_routes:
        raise ConfigError(f"{config_path}: routes must be a non-empty mapping")
    routes: dict[str, RouteConfig] = {}
    for name, raw in raw_routes.items():
        if not isinstance(raw, dict):
            raise ConfigError(f"route {name} must be a mapping")
        try:
            line_hold_ms = int(raw["line_hold_ms"])
            timeout_ms = int(raw["state_timeout_ms"])
            forward_speed = float(raw["forward_speed"])
            turn_speed = float(raw["turn_speed"])
        except (KeyError, TypeError, ValueError) as exc:
            raise ConfigError(f"route {name} has invalid timing or speed fields") from exc
        if line_hold_ms < 0 or timeout_ms <= 0:
            raise ConfigError(f"route {name}: line_hold_ms and state_timeout_ms are invalid")
        if not all(math.isfinite(value) and 0 <= value <= 1 for value in (forward_speed, turn_speed)):
            raise ConfigError(f"route {name}: speeds must be between 0 and 1")
        nodes = []
        for raw_node in raw.get("tag_nodes", []):
            if not isinstance(raw_node, dict):
                raise ConfigError(f"route {name}: tag_nodes must contain mappings")
            ids = tuple(int(tag_id) for tag_id in raw_node.get("ids", []))
            if not raw_node.get("name") or not raw_node.get("state") or not ids:
                raise ConfigError(f"route {name}: each tag node needs name, state and ids")
            nodes.append(
                TagNode(
                    name=str(raw_node["name"]),
                    state=str(raw_node["state"]),
                    ids=ids,
                    unverified=bool(raw_node.get("unverified", False)),
                )
            )
        routes[str(name)] = RouteConfig(
            name=str(name),
            line_hold_ms=line_hold_ms,
            state_timeout_ms=timeout_ms,
            forward_speed=forward_speed,
            turn_speed=turn_speed,
            tag_nodes=tuple(nodes),
        )
    return routes
