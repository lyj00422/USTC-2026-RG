from __future__ import annotations
import time

import pytest

from control_hub.services.chassis_service import ChassisService
from control_hub.services.event_log import EventLog
from control_hub.state import HubState
from rg_runtime.transports import MemoryTransport


def make_service(transport=None):
    transport = transport or MemoryTransport()
    service = ChassisService(
        HubState(),
        EventLog(),
        transport_factory=lambda _device, _baudrate: transport,
        port_discovery=lambda: [{"device": "/dev/rfcomm0", "description": "JDY-31", "is_chassis": True}],
    )
    return service, transport


def test_connect_reports_status_and_does_not_move():
    service, transport = make_service()
    status = service.connect("/dev/rfcomm0", 9600)
    assert status["connected"] is True
    assert status["device"] == "/dev/rfcomm0"
    assert status["baudrate"] == 9600
    assert status["velocity"] == {"vx": 0, "vy": 0, "wz": 0}
    assert transport.sent == []


def test_velocity_validates_and_formats_protocol():
    service, transport = make_service()
    service.connect("/dev/rfcomm0", 9600)
    service.set_velocity(20, -5, 0)
    assert transport.sent == ["V 20 -5 0\r\n"]
    with pytest.raises(ValueError):
        service.set_velocity(101, 0, 0)
    with pytest.raises(ValueError):
        service.set_velocity(True, 0, 0)


def test_velocity_motion_history_records_duration_and_command_integral():
    now = [100.0]
    service, _ = make_service()
    service._clock = lambda: now[0]
    service.connect("/dev/rfcomm0", 9600)
    service.set_velocity(20, 0, 0)
    now[0] = 102.5
    service.stop()

    history = service.status()["motion_history"]
    assert len(history) == 1
    assert history[0]["velocity"] == {"vx": 20, "vy": 0, "wz": 0}
    assert history[0]["duration_ms"] == 2500
    assert history[0]["command_integral"] == {"vx_ms": 50000, "vy_ms": 0, "wz_ms": 0}
    assert service.reset_distance()["motion_history"] == []


def test_velocity_change_closes_previous_segment_and_starts_next_segment():
    now = [100.0]
    service, _ = make_service()
    service._clock = lambda: now[0]
    service.connect("/dev/rfcomm0", 9600)
    service.set_velocity(20, 0, 0)
    now[0] = 101.0
    service.set_velocity(0, 30, 0)
    now[0] = 103.5
    service.stop()

    assert service.status()["motion_history"] == [
        {
            "velocity": {"vx": 20, "vy": 0, "wz": 0},
            "duration_ms": 1000,
            "command_integral": {"vx_ms": 20000, "vy_ms": 0, "wz_ms": 0},
        },
        {
            "velocity": {"vx": 0, "vy": 30, "wz": 0},
            "duration_ms": 2500,
            "command_integral": {"vx_ms": 0, "vy_ms": 75000, "wz_ms": 0},
        },
    ]


def test_run_distance_validates_and_formats_open_loop_command():
    service, transport = make_service()
    service.connect("/dev/rfcomm0", 9600)
    result = service.run_distance(10, 0, 0, 80)
    assert transport.sent == ["D 10 0 0 80\r\n"]
    assert result["command"] == {"forward_cm": 10, "right_cm": 0, "rotate_deg": 0, "speed": 80}
    with pytest.raises(ValueError):
        service.run_distance(10, 0, 0, 101)
    with pytest.raises(ValueError):
        service.run_distance(True, 0, 0, 80)

def test_distance_status_accumulates_signed_command_distance_and_can_reset():
    service, _ = make_service()
    service.connect("/dev/rfcomm0", 9600)
    service.run_distance(100, 0, 0, 50)
    service.run_distance(-40, 25, 0, 50)
    assert service.status()["distance"] == {"forward_cm": 60, "right_cm": 25}
    assert service.reset_distance()["distance"] == {"forward_cm": 0, "right_cm": 0}


def test_named_diagnostic_commands_and_sequence_use_firmware_protocol():
    service, transport = make_service()
    service.connect("/dev/rfcomm0", 9600)
    service.run_sequence()
    service.request_encoder()
    service.request_speed()
    assert transport.sent == ["SEQ\r\n", "ENC\r\n", "SPD\r\n"]


def test_motor_test_and_encoder_reset_validate_named_protocol():
    service, transport = make_service()
    service.connect("/dev/rfcomm0", 9600)
    service.motor_test("LF", -20)
    service.reset_encoder()
    assert transport.sent == ["M LF -20\r\n", "ENC RESET\r\n"]
    assert service.status()["command"] == {"kind": "M", "wheel": "LF", "speed": -20}


def test_stop_is_idempotent_and_disconnect_stops_before_close():
    service, transport = make_service()
    service.connect("/dev/rfcomm0", 9600)
    service.set_velocity(20, 0, 0)
    service.stop()
    service.stop()
    service.disconnect()
    assert transport.sent == ["V 20 0 0\r\n", "STOP\r\n", "STOP\r\n"]
    assert service.status()["connected"] is False


def test_serial_fault_latches_fault_and_attempts_stop():
    class BrokenTransport(MemoryTransport):
        def send_line(self, line):
            if line.startswith("V "):
                raise OSError("bluetooth link lost")
            super().send_line(line)

    service, transport = make_service(BrokenTransport())
    service.connect("/dev/rfcomm0", 9600)
    with pytest.raises(OSError):
        service.set_velocity(20, 0, 0)
    assert service.status()["state"] == "FAULT"
    assert "bluetooth link lost" in service.status()["error"]
    assert transport.sent == ["STOP\r\n"]


def test_poll_io_fault_closes_broken_link_and_reports_disconnected():
    class BrokenReadTransport(MemoryTransport):
        def read_lines(self):
            raise OSError(5, "Input/output error")

    service, transport = make_service(BrokenReadTransport())
    service.connect("/dev/rfcomm0", 9600)
    with pytest.raises(OSError):
        service.poll_once()
    status = service.status()
    assert status["connected"] is False
    assert status["state"] == "FAULT"
    assert "Input/output error" in status["error"]


def test_idle_keepalive_sends_one_read_only_spd_per_period():
    now = [100.0]
    service, transport = make_service()
    service._clock = lambda: now[0]
    service.connect("/dev/rfcomm0", 9600)
    assert transport.sent == []
    assert service.keepalive_tick() is False
    now[0] = 104.9
    assert service.keepalive_tick() is False
    now[0] = 105.0
    assert service.keepalive_tick() is True
    assert transport.sent == ["SPD\r\n"]
    assert service.status()["keepalive"]["sends"] == 1
    # SPD is a query: the car must still be exactly where the operator left it.
    assert service.status()["velocity"] == {"vx": 0, "vy": 0, "wz": 0}


def test_keepalive_defers_to_a_command_that_just_went_out():
    now = [100.0]
    service, transport = make_service()
    service._clock = lambda: now[0]
    service.connect("/dev/rfcomm0", 9600)
    now[0] = 104.5
    service.set_velocity(20, 0, 0)
    now[0] = 105.0
    # Due, but only half a second of silence: the firmware answers only the
    # first command of a back-to-back pair, so this tick must not fire.
    assert service.keepalive_tick() is False
    assert transport.sent == ["V 20 0 0\r\n"]
    now[0] = 110.0
    assert service.keepalive_tick() is True
    assert transport.sent == ["V 20 0 0\r\n", "SPD\r\n"]


def test_keepalive_never_touches_motion_history_or_stop_bookkeeping():
    now = [100.0]
    service, _ = make_service()
    service._clock = lambda: now[0]
    service.connect("/dev/rfcomm0", 9600)
    service.set_velocity(20, 0, 0)
    now[0] = 110.0
    assert service.keepalive_tick() is True
    now[0] = 112.0
    service.stop()
    history = service.status()["motion_history"]
    assert len(history) == 1
    assert history[0]["duration_ms"] == 12000
    assert history[0]["velocity"] == {"vx": 20, "vy": 0, "wz": 0}


def test_keepalive_can_be_disabled_and_stops_after_disconnect():
    now = [100.0]
    service, transport = make_service()
    service._clock = lambda: now[0]
    service.keepalive_enabled = False
    service.connect("/dev/rfcomm0", 9600)
    now[0] = 200.0
    assert service.keepalive_tick() is False
    assert transport.sent == []

    service.keepalive_enabled = True
    service.disconnect()
    now[0] = 300.0
    assert service.keepalive_tick() is False
    assert service.status()["keepalive"]["next_in_s"] is None


def test_keepalive_fault_closes_the_link_like_any_other_command():
    class BrokenQueryTransport(MemoryTransport):
        def send_line(self, line):
            if line.startswith("SPD"):
                raise OSError("bluetooth link lost")
            super().send_line(line)

    now = [100.0]
    service, _ = make_service(BrokenQueryTransport())
    service._clock = lambda: now[0]
    service.connect("/dev/rfcomm0", 9600)
    now[0] = 110.0
    with pytest.raises(OSError):
        service.keepalive_tick()
    status = service.status()
    assert status["connected"] is False
    assert status["state"] == "FAULT"
