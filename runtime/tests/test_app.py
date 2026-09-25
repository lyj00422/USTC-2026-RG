from pathlib import Path

import pytest

from rg_runtime.app_support import load_runtime_config, validate_mode
from rg_runtime.sources import NullEncoderSource, NullLineSource


def test_null_sources_are_explicitly_unavailable():
    assert NullLineSource().read(10).line_lost
    assert NullEncoderSource().read(10).valid is False


def test_auto_requires_real_sensor_sources():
    config = load_runtime_config(Path("config/runtime.yaml"))
    with pytest.raises(RuntimeError, match="line source"):
        validate_mode("auto", config, line_available=False, encoder_available=False)


def test_dry_run_is_allowed_without_hardware():
    config = load_runtime_config(Path("config/runtime.yaml"))
    validate_mode("dry-run", config, line_available=False, encoder_available=False)
    assert config.arm_probe_timeout_ms == 2000
    assert config.arm_action_timeout_ms == 45000


def test_runtime_config_uses_fifteen_minute_control_lease():
    config = load_runtime_config(Path("config/runtime.yaml"))
    assert config.control_lease_ms == 900_000
