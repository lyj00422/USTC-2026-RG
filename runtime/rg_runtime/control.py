"""Safety and coordination for the independent chassis and arm links."""

from __future__ import annotations

from dataclasses import dataclass

from .devices import ArmDevice, ChassisDevice
from .hardware_models import ArmMode


@dataclass
class Coordinator:
    chassis: ChassisDevice
    arm: ArmDevice
    settle_ms: int = 500
    phase: str = "IDLE"
    requested_routine: int | None = None
    settle_started_ms: int | None = None
    arm_action_done: bool = False

    def start_arm_action(self, routine: int, *, now_ms: int) -> None:
        if self.phase != "IDLE":
            raise RuntimeError(f"coordinator is {self.phase}")
        if self.arm.state.mode is ArmMode.BUSY:
            raise RuntimeError("arm is BUSY")
        self.chassis.stop()
        self.requested_routine = routine
        self.settle_started_ms = now_ms
        self.arm_action_done = False
        self.phase = "SETTLING"

    def tick(self, now_ms: int) -> None:
        arm_events = self.arm.poll()
        self.chassis.poll()
        if self.phase == "SETTLING":
            assert self.settle_started_ms is not None
            if now_ms - self.settle_started_ms >= self.settle_ms:
                self.arm.enable()
                self.phase = "ENABLING"
        elif self.phase == "ENABLING":
            if self.arm.state.mode is ArmMode.READY:
                assert self.requested_routine is not None
                self.arm.run(self.requested_routine)
                self.phase = "RUNNING"
        elif self.phase == "RUNNING":
            if any(getattr(event, "name", None) == "DONE" for event in arm_events) or (
                self.arm.last_done_routine == self.requested_routine
            ):
                self.arm_action_done = True
                self.phase = "IDLE"
                self.requested_routine = None
                self.settle_started_ms = None

    def stop_all(self) -> None:
        self.chassis.stop()
        self.arm.stop()
        self.phase = "IDLE"
        self.requested_routine = None
        self.settle_started_ms = None


class SafetySupervisor:
    def __init__(self, coordinator: Coordinator) -> None:
        self.coordinator = coordinator
        self.latched = False
        self.fault_reason: str | None = None

    def fault(self, reason: str, *, now_ms: int) -> None:
        del now_ms
        if self.latched:
            return
        self.latched = True
        self.fault_reason = reason
        self.coordinator.stop_all()

    def require_healthy(self) -> None:
        if self.latched:
            raise RuntimeError(f"FAULT_SAFE: {self.fault_reason}")
