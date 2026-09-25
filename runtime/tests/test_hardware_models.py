from rg_runtime.hardware_models import ArmMode, ArmState, DeviceHealth, EncoderState


def test_unavailable_encoder_is_explicitly_invalid():
    state = EncoderState.unavailable(123)
    assert state.valid is False
    assert state.rpm == (0.0, 0.0, 0.0, 0.0)
    assert state.timestamp_ms == 123


def test_device_health_reports_connection_and_error():
    health = DeviceHealth(connected=True, last_rx_ms=10, last_error=None)
    assert health.connected
    assert health.last_rx_ms == 10


def test_arm_state_has_safe_default():
    state = ArmState()
    assert state.mode is ArmMode.UNKNOWN
    assert state.routine is None
    assert state.suction_commanded is False
