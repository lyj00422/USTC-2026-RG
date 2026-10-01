from __future__ import annotations
import time

import pytest

from control_hub.services.chassis_service import ChassisService
from control_hub.services.event_log import EventLog
from control_hub.state import HubState
from rg_runtime.transports import MemoryTransport

CRLF = "\r\n"  # the firmware STP line terminator


def make_service(transport=None):
    transport = transport or MemoryTransport()
    service = ChassisService(
        HubState(),
        EventLog(),
        transport_factory=lambda _device, _baudrate: transport,
        port_discovery=lambda: [{"device": "/dev/robogame-chassis", "description": "", "is_chassis": True}],
    )
    return service, transport


def test_connect_reports_status_and_does_not_move():
    service, transport = make_service()
    status = service.connect("/dev/robogame-chassis", 9600)
    assert status["connected"] is True
    assert status["device"] == "/dev/robogame-chassis"
    assert status["baudrate"] == 9600
    assert status["velocity"] == {"vx": 0, "vy": 0, "wz": 0}
    assert transport.sent == []


def test_velocity_validates_and_formats_protocol():
    service, transport = make_service()
    service.connect("/dev/robogame-chassis", 9600)
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
    service.connect("/dev/robogame-chassis", 9600)
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
    service.connect("/dev/robogame-chassis", 9600)
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
    service.connect("/dev/robogame-chassis", 9600)
    result = service.run_distance(10, 0, 0, 80)
    assert transport.sent == ["D 10 0 0 80\r\n"]
    assert result["command"] == {"forward_cm": 10, "right_cm": 0, "rotate_deg": 0, "speed": 80}
    with pytest.raises(ValueError):
        service.run_distance(10, 0, 0, 101)
    with pytest.raises(ValueError):
        service.run_distance(True, 0, 0, 80)

def test_distance_status_accumulates_signed_command_distance_and_can_reset():
    service, _ = make_service()
    service.connect("/dev/robogame-chassis", 9600)
    service.run_distance(100, 0, 0, 50)
    service.run_distance(-40, 25, 0, 50)
    assert service.status()["distance"] == {"forward_cm": 60, "right_cm": 25}
    assert service.reset_distance()["distance"] == {"forward_cm": 0, "right_cm": 0}


def test_named_diagnostic_commands_and_sequence_use_firmware_protocol():
    service, transport = make_service()
    service.connect("/dev/robogame-chassis", 9600)
    service.run_sequence()
    service.request_encoder()
    service.request_speed()
    assert transport.sent == ["SEQ\r\n", "ENC\r\n", "SPD\r\n"]


def test_motor_test_and_encoder_reset_validate_named_protocol():
    service, transport = make_service()
    service.connect("/dev/robogame-chassis", 9600)
    service.motor_test("LF", -20)
    service.reset_encoder()
    assert transport.sent == ["M LF -20\r\n", "ENC RESET\r\n"]
    assert service.status()["command"] == {"kind": "M", "wheel": "LF", "speed": -20}


def test_stop_is_idempotent_and_disconnect_stops_before_close():
    service, transport = make_service()
    service.connect("/dev/robogame-chassis", 9600)
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
                raise OSError("chassis link lost")
            super().send_line(line)

    service, transport = make_service(BrokenTransport())
    service.connect("/dev/robogame-chassis", 9600)
    with pytest.raises(OSError):
        service.set_velocity(20, 0, 0)
    assert service.status()["state"] == "FAULT"
    assert "chassis link lost" in service.status()["error"]
    assert transport.sent == ["STOP\r\n"]


def test_poll_io_fault_closes_broken_link_and_reports_disconnected():
    class BrokenReadTransport(MemoryTransport):
        def read_lines(self):
            raise OSError(5, "Input/output error")

    service, transport = make_service(BrokenReadTransport())
    service.connect("/dev/robogame-chassis", 9600)
    with pytest.raises(OSError):
        service.poll_once()
    status = service.status()
    assert status["connected"] is False
    assert status["state"] == "FAULT"
    assert "Input/output error" in status["error"]


def test_query_fault_closes_the_link_like_any_other_command():
    class BrokenQueryTransport(MemoryTransport):
        def send_line(self, line):
            if line.startswith("SPD"):
                raise OSError("chassis link lost")
            super().send_line(line)

    now = [100.0]
    service, _ = make_service(BrokenQueryTransport())
    service._clock = lambda: now[0]
    service.connect("/dev/robogame-chassis", 9600)
    now[0] = 110.0
    with pytest.raises(OSError):
        service.request_speed()
    status = service.status()
    assert status["connected"] is False
    assert status["state"] == "FAULT"


def test_read_only_query_never_touches_motion_history_or_stop_bookkeeping():
    now = [100.0]
    service, transport = make_service()
    service._clock = lambda: now[0]
    service.connect("/dev/robogame-chassis", 9600)
    service.set_velocity(20, 0, 0)
    now[0] = 110.0
    service.request_speed()
    assert transport.sent == ["V 20 0 0" + CRLF, "SPD" + CRLF]
    # SPD is a query: the car is still where the operator left it, and the
    # velocity segment it opened must not have been closed by the query.
    assert service.status()["velocity"] == {"vx": 20, "vy": 0, "wz": 0}
    now[0] = 112.0
    service.stop()
    history = service.status()["motion_history"]
    assert len(history) == 1
    assert history[0]["duration_ms"] == 12000
    assert history[0]["velocity"] == {"vx": 20, "vy": 0, "wz": 0}


def test_port_discovery_flags_the_uart_alias_and_the_raw_uart2_tty(monkeypatch):
    """is_chassis is a name match now: a UART has no product string to match."""

    class FakePort:
        def __init__(self, device, description):
            self.device, self.description, self.vid, self.pid = device, description, None, None

    monkeypatch.setattr(
        "serial.tools.list_ports.comports",
        lambda: [
            FakePort("/dev/ttyAMA2", "n/a"),
            FakePort("/dev/ttyAMA0", "n/a"),
            FakePort("/dev/ttyUSB0", "CH340"),
            FakePort("/dev/robogame-chassis", "n/a"),
        ],
    )
    flagged = {port["device"] for port in ChassisService._discover_ports() if port["is_chassis"]}
    assert flagged == {"/dev/ttyAMA2", "/dev/robogame-chassis"}
