import json
from pathlib import Path
import time
from types import SimpleNamespace

import pytest

from rg_runtime.arm_tools import ArmSession, ArmTimeoutError, discover_serial_ports, execute_console_command
from rg_runtime.devices import ArmDevice
from rg_runtime.hardware_models import ArmMode
from rg_runtime.transports import MemoryTransport


class SingleCommandTransport:
    """Emulate firmware that drops writes until its pending reply is read."""

    RESPONSES = {
        "ARM,STOP\r\n": "ACK,STOPPED_LOCKED\r\n",
        "ARM,PING\r\n": "ACK,PONG\r\n",
        "ARM,STATUS\r\n": "STATE,LOCKED,CAL=1,SUCTION=0,ROUTINE=255,STEP=0,RX3=0\r\n",
    }

    def __init__(self):
        self.sent = []
        self._pending = []

    def send_line(self, line):
        self.sent.append(line)
        if not self._pending and line in self.RESPONSES:
            self._pending.append(self.RESPONSES[line])

    def read_lines(self):
        lines = self._pending
        self._pending = []
        return [line.rstrip("\r\n") for line in lines]

    def close(self):
        return None


class StaleReplyTransport:
    def __init__(self):
        self.sent = []
        self._pending = []

    def send_line(self, line):
        self.sent.append(line)
        if line == "ARM,STOP\r\n":
            self._pending.extend(
                [
                    "ACK,STOPPED_LOCKED\r\n",
                    "ACK,PONG\r\n",
                    "STATE,LOCKED,CAL=1,SUCTION=0,ROUTINE=255,STEP=0,RX3=0\r\n",
                ]
            )

    def read_lines(self):
        lines = self._pending
        self._pending = []
        return [line.rstrip("\r\n") for line in lines]

    def close(self):
        return None


class DelayedSingleCommandTransport(SingleCommandTransport):
    def read_lines(self):
        time.sleep(0.02)
        return super().read_lines()


def test_serial_discovery_marks_ch340_candidate():
    ports = [
        SimpleNamespace(device="/dev/ttyUSB0", description="USB2.0-Serial", vid=0x1A86, pid=0x7523),
        SimpleNamespace(device="/dev/ttyACM0", description="Other", vid=1, pid=2),
    ]
    result = discover_serial_ports(ports)
    assert result[0].device == "/dev/ttyUSB0"
    assert result[0].is_ch340 is True
    assert result[1].is_ch340 is False


def test_safe_probe_only_sends_stop_ping_and_status(tmp_path: Path):
    transport = SingleCommandTransport()
    session = ArmSession(ArmDevice(transport), transport, tmp_path / "arm.jsonl")
    replies = session.safe_probe()
    assert transport.sent == ["ARM,STOP\r\n", "ARM,PING\r\n", "ARM,STATUS\r\n"]
    assert len(replies) == 3
    records = [json.loads(line) for line in (tmp_path / "arm.jsonl").read_text().splitlines()]
    assert {record["direction"] for record in records} == {"tx", "rx"}


def test_safe_probe_waits_for_each_reply_before_sending_next_command():
    transport = SingleCommandTransport()
    session = ArmSession(ArmDevice(transport), transport)

    replies = session.safe_probe(timeout_s=0.05)

    assert transport.sent == ["ARM,STOP\r\n", "ARM,PING\r\n", "ARM,STATUS\r\n"]
    assert [getattr(reply, "command", None) for reply in replies[:2]] == ["STOPPED_LOCKED", "PONG"]
    assert replies[-1].mode == "LOCKED"


def test_safe_probe_does_not_accept_replies_received_before_current_command():
    transport = StaleReplyTransport()
    session = ArmSession(ArmDevice(transport), transport)

    with pytest.raises(ArmTimeoutError, match="ACK,PONG"):
        session.safe_probe(timeout_s=0.05)

    assert transport.sent == ["ARM,STOP\r\n", "ARM,PING\r\n"]


def test_safe_probe_rejects_reply_arriving_after_deadline():
    transport = DelayedSingleCommandTransport()
    session = ArmSession(ArmDevice(transport), transport)

    with pytest.raises(ArmTimeoutError, match="ACK,STOPPED_LOCKED"):
        session.safe_probe(timeout_s=0.001)

    assert transport.sent == ["ARM,STOP\r\n"]


def test_console_run_requires_ready_state():
    transport = MemoryTransport()
    arm = ArmDevice(transport)
    session = ArmSession(arm, transport)
    with pytest.raises(RuntimeError, match="READY"):
        execute_console_command(session, "run 2")
    arm.state = arm.state.__class__(mode=ArmMode.READY)
    assert execute_console_command(session, "run 2") == "running action 2"
    assert transport.sent == ["ARM,RUN,2\r\n"]


def test_console_rejects_raw_servo_and_unknown_commands():
    session = ArmSession(ArmDevice(MemoryTransport()), MemoryTransport())
    with pytest.raises(ValueError, match="unsupported"):
        execute_console_command(session, "servo 0 1500 3000")
    with pytest.raises(ValueError, match="unsupported"):
        execute_console_command(session, "anything")


def test_session_close_stops_arm_before_closing():
    transport = MemoryTransport()
    session = ArmSession(ArmDevice(transport), transport)
    session.close()
    session.close()
    assert transport.sent == ["ARM,STOP\r\n"]


def test_wait_for_action_returns_on_matching_done():
    transport = MemoryTransport(["ACK,RUN,2\r\n", "EVENT,DONE,2\r\n"])
    session = ArmSession(ArmDevice(transport), transport)
    replies = session.wait_for_action(2, timeout_s=0.1)
    assert replies[-1].value == "2"


def test_wait_for_action_timeout_stops_and_locks():
    transport = MemoryTransport()
    session = ArmSession(ArmDevice(transport), transport)
    with pytest.raises(ArmTimeoutError):
        session.wait_for_action(2, timeout_s=0.0)
    assert transport.sent == ["ARM,STOP\r\n"]


def test_wait_for_ready_mode_consumes_enable_ack():
    transport = MemoryTransport(["ACK,ENABLED\r\n"])
    session = ArmSession(ArmDevice(transport), transport)
    state = session.wait_for_mode(ArmMode.READY, timeout_s=0.1)
    assert state.mode is ArmMode.READY
