from rg_runtime.control import Coordinator, SafetySupervisor
from rg_runtime.devices import ArmDevice, ChassisDevice
from rg_runtime.hardware_models import ArmMode
from rg_runtime.transports import MemoryTransport


def test_coordinator_stops_chassis_before_arm_run():
    chassis_transport = MemoryTransport()
    arm_transport = MemoryTransport()
    arm = ArmDevice(arm_transport)
    arm.state = arm.state.__class__(mode=ArmMode.LOCKED, calibrated=True)
    coordinator = Coordinator(
        ChassisDevice(chassis_transport), arm, settle_ms=100
    )
    coordinator.start_arm_action(2, now_ms=0)
    assert chassis_transport.sent == ["STOP\r\n"]
    coordinator.tick(99)
    assert arm_transport.sent == []
    coordinator.tick(100)
    assert arm_transport.sent == ["ARM,ENABLE\r\n"]
    arm_transport.feed("ACK,ENABLED\r\n")
    coordinator.tick(101)
    assert arm_transport.sent == ["ARM,ENABLE\r\n", "ARM,RUN,2\r\n"]
    arm_transport.feed("ACK,RUN,2\r\n", "EVENT,DONE,2\r\n")
    coordinator.tick(102)
    assert coordinator.arm_action_done
    assert coordinator.phase == "IDLE"


def test_safety_fault_sends_both_stops_once():
    chassis_transport = MemoryTransport()
    arm_transport = MemoryTransport()
    coordinator = Coordinator(ChassisDevice(chassis_transport), ArmDevice(arm_transport))
    safety = SafetySupervisor(coordinator)
    safety.fault("arm disconnected", now_ms=10)
    safety.fault("repeat", now_ms=11)
    assert safety.latched
    assert chassis_transport.sent == ["STOP\r\n"]
    assert arm_transport.sent == ["ARM,STOP\r\n"]
