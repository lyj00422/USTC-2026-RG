from pathlib import Path

import pytest

from rg_runtime.config import ConfigError, load_camera_config, load_route_config
from rg_runtime.app_support import load_runtime_config


ROOT = Path(__file__).resolve().parents[1]


def test_camera_baseline_loads_from_portable_config():
    config = load_camera_config(ROOT / "config" / "camera_config.yaml")

    assert (config.width, config.height) == (1280, 720)
    assert config.fps == 30.0
    assert config.pixel_format == "MJPG"
    assert config.camera_matrix.shape == (3, 3)
    assert config.distortion_coefficients.size == 5


def test_route_config_contains_three_unverified_routes():
    routes = load_route_config(ROOT / "config" / "routes.yaml")

    assert set(routes) == {"zone_1", "zone_2", "purple_required"}
    assert all(node.unverified for route in routes.values() for node in route.tag_nodes)


def test_missing_camera_file_is_rejected(tmp_path):
    with pytest.raises(ConfigError, match="does not exist"):
        load_camera_config(tmp_path / "missing.yaml")


def test_invalid_route_timeout_is_rejected(tmp_path):
    path = tmp_path / "routes.yaml"
    path.write_text(
        "routes:\n"
        "  bad:\n"
        "    line_hold_ms: -1\n"
        "    state_timeout_ms: 100\n"
        "    forward_speed: 0.2\n"
        "    turn_speed: 0.2\n"
        "    tag_nodes: []\n",
        encoding="utf-8",
    )

    with pytest.raises(ConfigError, match="line_hold_ms"):
        load_route_config(path)


def test_runtime_config_matches_measured_raspberry_pi_uart_wiring():
    config = load_runtime_config(ROOT / "config" / "runtime.yaml")
    assert config.line_transport == "pigpio_soft_uart"
    # Measured 2026-09-14: the module transmits into GPIO24 and receives on
    # GPIO23.  The vendor document's GPIO23-RX direction returns idle 0xFF only.
    assert config.line_rx_gpio == 24
    assert config.line_tx_gpio == 23
    assert config.line_baudrate == 115200
    assert config.line_startup_delay_s == 20.0
    assert config.line_request_retry_s == 1.0
