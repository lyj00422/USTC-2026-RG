import time

import pytest

from control_hub.services.arm_service import ArmService
from rg_runtime.arm_tools import ArmTimeoutError
from control_hub.services.event_log import EventLog
from control_hub.state import HubState
from rg_runtime.transports import MemoryTransport


class ProbeTransport(MemoryTransport):
    RESPONSES = {
        "ARM,STOP\r\n": "ACK,STOPPED_LOCKED\r\n",
        "ARM,PING\r\n": "ACK,PONG\r\n",
        "ARM,STATUS\r\n": "STATE,LOCKED,CAL=1,SUCTION=0,ROUTINE=255,STEP=0,RX3=0\r\n",
    }

    def send_line(self, line):
        super().send_line(line)
        if not self._incoming and line in self.RESPONSES:
            self.feed(self.RESPONSES[line])


class ReadyServoTransport(ProbeTransport):
    RESPONSES = {
        **ProbeTransport.RESPONSES,
        "ARM,STATUS\r\n": "STATE,READY,CAL=1,SUCTION=0,ROUTINE=255,STEP=0,RX3=0\r\n",
        "ARM,SERVO,0,1510,500\r\n": "ACK,SERVO\r\n",
    }


def make_service(transport, **kwargs):
    return ArmService(HubState(), EventLog(), transport_factory=lambda _device, _baud: transport, background=False, **kwargs)


def test_connect_does_not_send_commands_and_probe_is_safe():
    transport = ProbeTransport()
    service = make_service(transport)
    service.connect("memory", 115200)
    assert transport.sent == []
    status = service.probe(timeout_s=0.1)
    assert status["mode"] == "LOCKED"
    assert transport.sent == ["ARM,STOP\r\n", "ARM,PING\r\n", "ARM,STATUS\r\n"]
    events = service.event_log.recent()
    assert [event["payload"]["raw"] for event in events if event["type"] == "serial_tx"] == [
        "ARM,STOP", "ARM,PING", "ARM,STATUS"
    ]
    assert any(event["type"] == "serial_rx" and event["payload"]["raw"] == "ACK,PONG" for event in events)


def test_arm_actions_require_ready_and_update_from_replies():
    transport = ProbeTransport()
    service = make_service(transport)
    service.connect("memory", 115200)
    service.probe(timeout_s=0.1)
    with pytest.raises(RuntimeError, match="READY"):
        service.run(2)
    service.enable()
    transport.feed("ACK,ENABLED\r\n")
    service.poll_once()
    service.run(2)
    transport.feed("ACK,RUN,2\r\n")
    service.poll_once()
    assert service.status()["mode"] == "BUSY"
    with pytest.raises(RuntimeError, match="READY"):
        service.suction(True)
    transport.feed("EVENT,DONE,2\r\n")
    service.poll_once()
    assert service.status()["mode"] == "READY"


def test_rejected_commands_are_not_logged_as_serial_transmissions():
    transport = ProbeTransport()
    service = make_service(transport)
    service.connect("memory", 115200)

    with pytest.raises(RuntimeError, match="calibrated LOCKED"):
        service.enable()

    assert transport.sent == []
    assert not any(event["type"] == "serial_tx" for event in service.event_log.recent())


def test_disconnect_stops_before_close():
    transport = MemoryTransport()
    service = make_service(transport)
    service.connect("memory", 115200)
    service.disconnect()
    assert transport.sent == ["ARM,STOP\r\n"]
    assert service.status()["connected"] is False


def test_busy_action_timeout_sends_stop_and_locks():
    now = [0.0]
    transport = ProbeTransport()
    service = make_service(transport, action_timeout_s=2.0, clock=lambda: now[0])
    service.connect("memory", 115200)
    service.probe(timeout_s=0.1)
    service.enable()
    transport.feed("ACK,ENABLED\r\n")
    service.poll_once()
    service.run(2)
    transport.feed("ACK,RUN,2\r\n")
    service.poll_once()
    now[0] = 2.1
    service.poll_once()
    assert transport.sent[-1] == "ARM,STOP\r\n"
    assert service.status()["mode"] == "LOCKED"


def test_poll_fault_handler_sends_stop():
    transport = MemoryTransport()
    service = make_service(transport)
    service.connect("memory", 115200)
    service.handle_poll_fault(RuntimeError("serial disconnected"))
    assert transport.sent == ["ARM,STOP\r\n"]
    assert any(event["type"] == "fault" for event in service.event_log.recent())


def test_servo_waits_for_matching_ack_before_reporting_success():
    transport = ReadyServoTransport()
    service = make_service(transport)
    service.connect("memory", 115200)
    service.probe(timeout_s=0.1)

    assert service.servo(0, 1510, 500)["servo_targets"]["0"]["position"] == 1510


def test_servo_ack_timeout_does_not_update_target():
    transport = ReadyServoTransport()
    transport.RESPONSES = {key: value for key, value in transport.RESPONSES.items() if "SERVO" not in key}
    service = make_service(transport, command_ack_timeout_s=0.01)
    service.connect("memory", 115200)
    service.probe(timeout_s=0.1)

    with pytest.raises(ArmTimeoutError, match="ACK,SERVO"):
        service.servo(0, 1510, 500)
    assert service.status()["servo_targets"] == {}
