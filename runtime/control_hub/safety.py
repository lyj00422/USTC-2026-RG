"""Hub-wide stop and control-lease watchdog behavior."""

from __future__ import annotations

from .services.event_log import EventLog
from .state import ControlLease


class HubSafety:
    def __init__(self, arm_service, lease: ControlLease, event_log: EventLog, *, chassis_service=None) -> None:
        self.arm_service = arm_service
        self.chassis_service = chassis_service
        self.lease = lease
        self.event_log = event_log
        self.latched = False
        self.reason: str | None = None
        self.stop_failed = False

    def global_stop(self, reason: str) -> None:
        if self.latched and not self.stop_failed:
            return
        self.latched = True
        self.reason = reason
        failures = []
        if self.chassis_service is not None and self.chassis_service.connected:
            try:
                self.chassis_service.stop()
            except Exception as exc:
                failures.append({"device": "chassis", "message": str(exc)})
        if self.arm_service.connected:
            try:
                self.arm_service.stop()
            except Exception as exc:
                failures.append({"device": "arm", "message": str(exc)})
        for failure in failures:
            self.event_log.append("fault", failure["device"], {"message": failure["message"], "during": "global_stop"})
        self.stop_failed = bool(failures)
        self.event_log.append("global_stop", "safety", {"reason": reason})

    def check_lease(self, *, now_ms: int) -> bool:
        if self.lease.expired(now_ms=now_ms):
            self.global_stop("control_lease_expired")
            return True
        return False

    def reset(self) -> None:
        if self.stop_failed:
            raise RuntimeError("cannot reset safety stop until all devices acknowledge STOP")
        self.latched = False
        self.reason = None
        self.event_log.append("safety_reset", "safety", {})
