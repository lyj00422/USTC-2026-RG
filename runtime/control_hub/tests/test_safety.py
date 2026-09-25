from control_hub.safety import HubSafety
from control_hub.services.event_log import EventLog
from control_hub.state import ControlLease


class FakeArm:
    def __init__(self):
        self.stops = 0

    @property
    def connected(self):
        return True

    def stop(self):
        self.stops += 1


class FailingStopArm(FakeArm):
    def stop(self):
        self.stops += 1
        raise OSError("stop failed")


def test_global_stop_is_idempotent_until_reset():
    arm = FakeArm()
    safety = HubSafety(arm, ControlLease(100), EventLog())
    safety.global_stop("operator")
    safety.global_stop("repeat")
    assert arm.stops == 1
    assert safety.latched


def test_expired_control_lease_stops_arm():
    arm = FakeArm()
    lease = ControlLease(100, token_factory=lambda: "token")
    lease.acquire("browser", now_ms=0)
    safety = HubSafety(arm, lease, EventLog())
    assert safety.check_lease(now_ms=101)
    assert arm.stops == 1


def test_failed_global_stop_blocks_reset_until_stop_succeeds():
    arm = FailingStopArm()
    safety = HubSafety(arm, ControlLease(100), EventLog())
    safety.global_stop("operator")
    assert safety.latched
    assert safety.stop_failed
    try:
        safety.reset()
    except RuntimeError as exc:
        assert "stop" in str(exc)
    else:
        raise AssertionError("reset must remain blocked after stop failure")


def test_global_stop_retries_after_a_previous_stop_failure():
    class FlakyStopArm(FakeArm):
        def stop(self):
            self.stops += 1
            if self.stops == 1:
                raise OSError("temporary failure")

    arm = FlakyStopArm()
    safety = HubSafety(arm, ControlLease(100), EventLog())
    safety.global_stop("operator")
    assert safety.stop_failed
    safety.global_stop("retry")
    assert arm.stops == 2
    assert not safety.stop_failed
    safety.reset()
    assert not safety.latched
