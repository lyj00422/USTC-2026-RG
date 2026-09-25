from rg_runtime.line_sensor import LineFrameParser, line_state_from_mask


def test_parser_accepts_single_byte_mask_and_maps_left_to_right():
    parser = LineFrameParser(mode="bitmask_byte", active_level=0)
    state = parser.parse(bytes([0b11100111]), timestamp_ms=123)
    assert state.sensor_mask == 0b11100111
    assert state.line_error == 0.0
    assert state.line_lost is False
    assert state.timestamp_ms == 123


def test_parser_accepts_ascii_binary_frame_and_detects_lost_line():
    parser = LineFrameParser(mode="ascii_bits", active_level=0)
    state = parser.parse(b"11111111\r\n", timestamp_ms=5)
    assert state.sensor_mask == 0xFF
    assert state.line_lost is True


def test_parser_accepts_documented_digital_ascii_frame():
    parser = LineFrameParser(mode="ascii_digital", active_level=0)
    state = parser.parse(b"$D,x1:0,x2:1,x3:1,x4:0,x5:0,x6:0,x7:1,x8:1#", timestamp_ms=9)
    assert state.sensor_mask == 0b01100011
    assert state.line_lost is False


def test_line_state_reverses_sensor_order_when_configured():
    state = line_state_from_mask(0b10000000, timestamp_ms=1, active_level=1, reverse_order=True)
    assert state.sensor_mask == 0b00000001
    assert state.line_error > 0
