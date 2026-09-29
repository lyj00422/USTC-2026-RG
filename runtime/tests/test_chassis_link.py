"""ChassisLink: the heartbeat that keeps the Bluetooth session from idling out.

Everything here is synchronous and driven by an injected clock, which is how the
project already tests this kind of logic (see the keepalive block in
``control_hub/tests/test_chassis_service.py``).  One test at the end deliberately
starts the real thread, to prove the thin driver actually drives ``heartbeat_tick``.
"""
from __future__ import annotations

import time

import pytest

from rg_runtime.chassis_link import ChassisLink, speed_reply_is_stopped
from rg_runtime.transports import MemoryTransport

STOPPED_SPD = "SPD LF 0 RF 0 LR 0 RR 0 OUT 0 0 0 0\r\n"


class Clock:
    """Manual clock. `step` > 0 makes it advance on every read, which is how the
    deadline-bounded retry loops are driven to their timeout in a test."""

    def __init__(self, now: float = 1000.0, step: float = 0.0) -> None:
        self.now = now
        self.step = step

    def __call__(self) -> float:
        value = self.now
        self.now += self.step
        return value


class FakePortLock:
    def __init__(self) -> None:
        self.acquires = 0
        self.releases = 0
        self.held = False

    def acquire(self) -> None:
        self.acquires += 1
        self.held = True

    def release(self) -> None:
        if self.held:
            self.releases += 1
        self.held = False


class BrokenQueryTransport(MemoryTransport):
    """Raises on SPD -- the same shape the chassis service tests use."""

    def send_line(self, line):
        if line.startswith("SPD"):
            raise OSError("bluetooth link lost")
        super().send_line(line)


def make_link(transports=None, *, clock=None, log=None, **kwargs):
    """A link wired to fake lock, fake clock and a queue of transports.

    The factory hands out the next transport on each open/reconnect and keeps
    returning the last one once the queue is exhausted.
    """
    queue = list(transports or [MemoryTransport()])
    handed: list[MemoryTransport] = []

    def factory(_path, _baud):
        transport = queue[min(len(handed), len(queue) - 1)]
        handed.append(transport)
        return transport

    clock = clock if clock is not None else Clock()
    lock = FakePortLock()
    link = ChassisLink(
        "/dev/fake-chassis",
        9600,
        port_lock=lock,
        transport_factory=factory,
        clock=clock,
        sleeper=lambda _s: None,
        log=log or (lambda _m: None),
        wait_for_device=lambda *_a, **_k: None,
        **kwargs,
    )
    return link, handed, clock, lock


# --------------------------------------------------------------------- open


def test_open_primes_with_stop_and_starts_the_heartbeat():
    link, handed, _clock, lock = make_link()
    link.open()
    try:
        assert handed[0].sent == ["STOP\r\n"]
        assert link.connected
        assert lock.held
    finally:
        link.close()
    assert not lock.held


def test_open_releases_the_lock_when_the_transport_cannot_be_opened():
    def exploding_factory(_path, _baud):
        raise OSError("could not open port")

    lock = FakePortLock()
    link = ChassisLink(
        "/dev/fake-chassis",
        9600,
        port_lock=lock,
        transport_factory=exploding_factory,
        clock=Clock(),
        sleeper=lambda _s: None,
        log=lambda _m: None,
        wait_for_device=lambda *_a, **_k: None,
    )
    with pytest.raises(OSError):
        link.open()
    # A lock left held here would stop the maintainer from rebuilding the node --
    # the deadlock that stranded the robot on 2026-09-14.
    assert not lock.held
    assert lock.releases == 1


# ---------------------------------------------------------------- heartbeat


def test_heartbeat_fires_after_a_period_of_silence():
    link, handed, _clock, _lock = make_link()
    link.open()
    try:
        transport = handed[0]
        assert transport.sent == ["STOP\r\n"]
        assert link.heartbeat_tick(1004.9) is False
        assert link.heartbeat_tick(1005.0) is True
        assert transport.sent == ["STOP\r\n", "SPD\r\n"]
        assert link.heartbeat_sends == 1
    finally:
        link.close()


def test_heartbeat_defers_inside_the_quiet_window_and_rearms():
    link, handed, clock, _lock = make_link()
    link.open()
    try:
        transport = handed[0]
        clock.now = 1005.0
        link.set_velocity(20, 0, 0)
        # Due, but a command just went out: the firmware answers only the first
        # command of a burst, so a `SPD` here could swallow the `V`.
        assert link.heartbeat_tick(1005.0) is False
        assert link.heartbeat_defers == 1
        # Deferred ticks wait a full period rather than firing the moment the
        # window closes.
        assert link.heartbeat_tick(1007.0) is False
        assert link.heartbeat_tick(1010.0) is True
        assert transport.sent == ["STOP\r\n", "V 20 0 0\r\n", "SPD\r\n"]
    finally:
        link.close()


def test_every_wire_call_pushes_the_quiet_window_out():
    link, _handed, clock, _lock = make_link()
    link.open()
    try:
        for call in (
            lambda: link.set_velocity(0, 0, 0),
            lambda: link.run_distance(330, 0, 0, 80),
            lambda: link.stop(),
            lambda: link.request_speed(),
            lambda: link.request_encoder(),
        ):
            clock.now += 3.0
            call()
            assert link.seconds_since_last_tx() == 0.0
    finally:
        link.close()


def test_heartbeat_can_be_disabled():
    link, handed, _clock, _lock = make_link(heartbeat_enabled=False)
    link.open()
    try:
        assert link.heartbeat_tick(9999.0) is False
        assert handed[0].sent == ["STOP\r\n"]
    finally:
        link.close()


def test_heartbeat_is_silent_once_closed():
    link, handed, _clock, _lock = make_link()
    link.open()
    link.close()
    assert link.heartbeat_tick(9999.0) is False
    assert not link.connected
    assert handed[0].sent == ["STOP\r\n"]


def test_heartbeat_failure_surfaces_instead_of_being_swallowed():
    link, _handed, _clock, _lock = make_link([BrokenQueryTransport()])
    link.open()
    try:
        with pytest.raises(OSError):
            link.heartbeat_tick(1005.0)
    finally:
        link.close()


# ----------------------------------------------------------------- recovery


def test_recover_swaps_the_transport_and_confirms_stop():
    first = MemoryTransport()
    second = MemoryTransport()
    second.feed(STOPPED_SPD)
    link, handed, _clock, lock = make_link([first, second])
    link.open()
    try:
        result = link.recover(OSError("Input/output error"))
        # Returning `self` is load-bearing: the run loop assigns the result back
        # onto its chassis handle, and this object *is* that handle.
        assert result is link
        assert link.connected
        assert link._transport is second
        assert link._device is not None
        assert second.sent[0] == "STOP\r\n"
        assert "SPD\r\n" in second.sent
        assert link.reconnects == 1
        assert lock.held
    finally:
        link.close()


def test_recover_resumes_the_heartbeat_on_the_new_transport():
    first = MemoryTransport()
    second = MemoryTransport()
    second.feed(STOPPED_SPD)
    link, _handed, clock, _lock = make_link([first, second])
    link.open()
    try:
        link.recover(OSError("Input/output error"))
        clock.now = 9000.0
        assert link.heartbeat_tick(9000.0) is True
        assert second.sent[-1] == "SPD\r\n"
    finally:
        link.close()


def test_recover_does_not_confirm_stop_when_the_wheels_are_moving():
    """A fresh link is never trusted blind: recovery must time out rather than
    hand back a chassis whose last command may still be running."""
    moving = "SPD LF 480 RF 476 LR 482 RR 479 OUT 20 20 19 20\r\n"
    first = MemoryTransport()
    second = MemoryTransport()
    second.feed(moving)
    # A stepping clock: the STOP-confirm loop is bounded by the clock, so a
    # frozen one would spin forever instead of reaching its deadline.
    link, _handed, _clock, _lock = make_link([first, second], clock=Clock(step=1.0))
    link.open()
    try:
        with pytest.raises(RuntimeError, match="reconnect failed"):
            link.recover(OSError("link error"))
    finally:
        link.close()


def test_recover_gives_up_within_the_deadline():
    clock = Clock(step=25.0)
    link, _handed, _clock, _lock = make_link(clock=clock, recover_timeout_s=90.0)
    link.open()
    try:
        def exploding_factory(_path, _baud):
            raise OSError("no node")

        link.transport_factory = exploding_factory
        with pytest.raises(RuntimeError, match="reconnect failed"):
            link.recover(OSError("link error"))
        assert not link.connected
    finally:
        link.close()


def test_recover_reports_how_long_the_wire_was_silent():
    """The number that tells an idle teardown apart from the peer losing power."""
    messages: list[str] = []
    first = MemoryTransport()
    second = MemoryTransport()
    second.feed(STOPPED_SPD)
    link, _handed, clock, _lock = make_link([first, second], log=messages.append)
    link.open()
    try:
        clock.now = 1000.0 + 16.0
        link.recover(OSError("Input/output error"))
        assert any("16.0s before the error" in message for message in messages)
    finally:
        link.close()


# ------------------------------------------------------------ the real thread


def test_the_heartbeat_thread_actually_drives_the_tick():
    """The only test that starts the thread.  Polled to a deadline rather than
    joined, so a wedged heartbeat fails the test instead of hanging the suite."""
    transport = MemoryTransport()
    link = ChassisLink(
        "/dev/fake-chassis",
        9600,
        port_lock=FakePortLock(),
        transport_factory=lambda _path, _baud: transport,
        log=lambda _m: None,
        wait_for_device=lambda *_a, **_k: None,
        heartbeat_s=1.0,
        heartbeat_quiet_s=0.0,
        heartbeat_poll_s=0.05,
    )
    link.open()
    try:
        deadline = time.monotonic() + 3.0
        while link.heartbeat_sends == 0 and time.monotonic() < deadline:
            time.sleep(0.02)
        assert link.heartbeat_sends >= 1
        assert "SPD\r\n" in transport.sent
    finally:
        link.close()
    assert link._thread is None


# ------------------------------------------------------------- the predicate


def test_speed_reply_is_stopped_accepts_only_every_channel_at_zero():
    from rg_runtime.protocols import parse_chassis_reply

    assert speed_reply_is_stopped(parse_chassis_reply("SPD LF 0 RF 0 LR 0 RR 0 OUT 0 0 0 0"))
    assert not speed_reply_is_stopped(parse_chassis_reply("SPD LF 480 RF 0 LR 0 RR 0 OUT 0 0 0 0"))
    assert not speed_reply_is_stopped(parse_chassis_reply("ENC LF 0 RF 0 LR 0 RR 0"))
    assert not speed_reply_is_stopped(parse_chassis_reply("SPD LF 0 RF 0 LR 0"))
