"""Configuration and startup guards shared by CLI entry points."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml


@dataclass(frozen=True)
class RuntimeConfig:
    chassis_device: str
    chassis_baudrate: int
    chassis_bluetooth_mac: str
    chassis_spp_channel: int
    arm_device: str
    arm_baudrate: int
    heartbeat_ms: int
    chassis_settle_ms: int
    auto_requires_sensors: bool
    hub_host: str = "0.0.0.0"
    hub_port: int = 8080
    control_lease_ms: int = 900000
    hub_log_path: str = "logs/control_hub.jsonl"
    camera_config_path: str = "config/camera_config.yaml"
    camera_output_dir: str = "data/control_hub"
    arm_probe_timeout_ms: int = 2000
    arm_action_timeout_ms: int = 45000
    line_enabled: bool = True
    line_transport: str = "pigpio_soft_uart"
    # The vendor's Raspberry Pi example uses the hardware UART on GPIO14/15
    # (physical pins 8/10), exposed as /dev/ttyAMA0 after UART setup.
    line_device: str = "/dev/ttyAMA0"
    line_rx_gpio: int = 23
    line_tx_gpio: int = 24
    line_baudrate: int = 115200
    line_frame_mode: str = "ascii_digital"
    line_active_level: int = 0
    line_reverse_order: bool = False
    line_request_command: str = "$0,0,1#"
    line_startup_delay_s: float = 20.0
    line_request_retry_s: float = 1.0
    chassis_keepalive_enabled: bool = True
    chassis_keepalive_s: float = 5.0
    chassis_keepalive_quiet_s: float = 2.0
    chassis_keepalive_log_every_s: float = 60.0


def load_runtime_config(path: str | Path) -> RuntimeConfig:
    raw: dict[str, Any] = yaml.safe_load(Path(path).expanduser().read_text(encoding="utf-8")) or {}
    chassis = raw.get("chassis", {})
    arm = raw.get("arm", {})
    safety = raw.get("safety", {})
    hub = raw.get("control_hub", {})
    camera = raw.get("camera", {})
    line = raw.get("line_sensor", {})
    return RuntimeConfig(
        chassis_device=str(chassis.get("device", "/dev/robogame-chassis")),
        chassis_baudrate=int(chassis.get("baudrate", 9600)),
        chassis_bluetooth_mac=str(chassis.get("bluetooth_mac", "6E:53:BD:74:00:A7")),
        chassis_spp_channel=int(chassis.get("spp_channel", 1)),
        arm_device=str(arm.get("device", "/dev/robogame-arm")),
        arm_baudrate=int(arm.get("baudrate", 115200)),
        heartbeat_ms=int(chassis.get("heartbeat_ms", 50)),
        chassis_settle_ms=int(safety.get("chassis_settle_ms", 500)),
        auto_requires_sensors=bool(safety.get("require_real_sensors_for_auto", True)),
        hub_host=str(hub.get("host", "0.0.0.0")),
        hub_port=int(hub.get("port", 8080)),
        control_lease_ms=int(hub.get("control_lease_ms", 900000)),
        hub_log_path=str(hub.get("log_path", "logs/control_hub.jsonl")),
        camera_config_path=str(camera.get("config", "config/camera_config.yaml")),
        camera_output_dir=str(camera.get("output_dir", "data/control_hub")),
        arm_probe_timeout_ms=int(arm.get("probe_timeout_ms", 2000)),
        arm_action_timeout_ms=int(arm.get("action_timeout_ms", 45000)),
        line_enabled=bool(line.get("enabled", True)),
        line_transport=str(line.get("transport", "pigpio_soft_uart")),
        line_device=str(line.get("device", "/dev/ttyAMA0")),
        line_rx_gpio=int(line.get("rx_gpio", 23)),
        line_tx_gpio=int(line.get("tx_gpio", 24)),
        line_baudrate=int(line.get("baudrate", 115200)),
        line_frame_mode=str(line.get("frame_mode", "ascii_digital")),
        line_active_level=int(line.get("active_level", 0)),
        line_reverse_order=bool(line.get("reverse_order", False)),
        line_request_command=str(line.get("request_command", "$0,0,1#")),
        line_startup_delay_s=float(line.get("startup_delay_s", 20.0)),
        line_request_retry_s=float(line.get("request_retry_s", 1.0)),
        chassis_keepalive_enabled=bool(chassis.get("keepalive_enabled", True)),
        chassis_keepalive_s=float(chassis.get("keepalive_s", 5.0)),
        chassis_keepalive_quiet_s=float(chassis.get("keepalive_quiet_s", 2.0)),
        chassis_keepalive_log_every_s=float(chassis.get("keepalive_log_every_s", 60.0)),
    )


def validate_mode(mode: str, config: RuntimeConfig, *, line_available: bool, encoder_available: bool) -> None:
    if mode not in {"diagnose", "manual", "dry-run", "auto"}:
        raise ValueError(f"unsupported mode: {mode}")
    if mode == "auto" and config.auto_requires_sensors:
        if not line_available:
            raise RuntimeError("auto mode requires a valid line source")
        if not encoder_available:
            raise RuntimeError("auto mode requires a valid encoder source")
