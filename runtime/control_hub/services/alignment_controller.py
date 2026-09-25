from __future__ import annotations
from enum import Enum
import time

class AlignmentState(str, Enum):
    IDLE = "IDLE"; SEARCH = "SEARCH"; ALIGNING = "ALIGNING"; STABLE = "STABLE"; RUNNING_ACTION = "RUNNING_ACTION"; COMPLETE = "COMPLETE"; FAULT = "FAULT"

class AlignmentController:
    def __init__(self, chassis, arm, *, stable_frames=3, step_cm=2, speed=15, max_travel_cm=30, deadband_px=3, timeout_s=10, clock=time.monotonic):
        self.chassis, self.arm = chassis, arm
        self.stable_frames, self.step_cm, self.speed = int(stable_frames), int(step_cm), int(speed)
        self.max_travel_cm, self.deadband_px, self.timeout_s, self.clock = int(max_travel_cm), float(deadband_px), float(timeout_s), clock
        self.state, self.zone, self.stable_count, self.travel, self.started_at, self.action = AlignmentState.IDLE, None, 0, 0, None, None

    def start(self, zone):
        if self.state not in {AlignmentState.IDLE, AlignmentState.COMPLETE, AlignmentState.FAULT}: raise RuntimeError("alignment is already active")
        self.zone, self.state, self.stable_count, self.travel, self.started_at = zone, AlignmentState.SEARCH, 0, 0, self.clock()
        self.action = zone.get("action_id")
        return self.status()

    def tick(self, target=None):
        if self.state in {AlignmentState.IDLE, AlignmentState.COMPLETE, AlignmentState.FAULT}: return self.status()
        if self.started_at is not None and self.clock() - self.started_at > self.timeout_s: return self._fault("alignment timeout")
        if not target or target.get("color") != self.zone.get("color"):
            self.stable_count = 0; self.state = AlignmentState.SEARCH; return self.status()
        rect = self.zone["rect"]; center = target.get("center_px", [0, 0]); left, right = rect["x"], rect["x"] + rect["width"]
        if center[0] < left - self.deadband_px:
            self.chassis.run_distance(0, self.step_cm, 0, self.speed); self.travel += self.step_cm; self.state = AlignmentState.ALIGNING
        elif center[0] > right + self.deadband_px:
            self.chassis.run_distance(0, -self.step_cm, 0, self.speed); self.travel += self.step_cm; self.state = AlignmentState.ALIGNING
        else:
            self.stable_count += 1; self.state = AlignmentState.STABLE
            if self.stable_count >= self.stable_frames:
                self.chassis.stop(); self.arm.run(self.action); self.state = AlignmentState.RUNNING_ACTION
        if self.travel > self.max_travel_cm: return self._fault("alignment travel limit exceeded")
        return self.status()

    def _fault(self, message):
        try: self.chassis.stop()
        finally: self.state = AlignmentState.FAULT
        return self.status(error=message)

    def status(self, error=None):
        return {"state": self.state.value, "stable_frames": self.stable_count, "travel_cm": self.travel, "action_id": self.action, "error": error}
