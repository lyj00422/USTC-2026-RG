"""ChassisLink: open, recover, and the STOP-readback confirmation.

Everything here is synchronous and driven by an injected clock, which is how the
project already tests this kind of logic.

There was a second subject until 2026-10-01 -- a link-level keepalive whose only
job was to stop the JDY-31 dropping an idle Bluetooth session.  The chassis is
on a wire now (see ``rg_runtime/chassis_link.py``), so those tests are gone with
the mechanism and the tests that remain are the parts of the class that still
have work to do.
"""
from __future__ import annotations

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


def test_open_primes_with_stop():
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
    # A lock left held here would keep the next open -- the route's own reconnect
    # or the operator console -- from taking the port, which is the deadlock that
    # stranded the robot on 2026-09-14.
    assert not lock.held
    assert lock.releases == 1


def test_every_wire_call_pushes_the_silence_clock_out():
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
    """The number that tells a brownout apart from a program that simply stopped
    talking to the chassis."""
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


# ------------------------------------------------------------- the predicate


def test_speed_reply_is_stopped_accepts_only_every_channel_at_zero():
    from rg_runtime.protocols import parse_chassis_reply

    assert speed_reply_is_stopped(parse_chassis_reply("SPD LF 0 RF 0 LR 0 RR 0 OUT 0 0 0 0"))
    assert not speed_reply_is_stopped(parse_chassis_reply("SPD LF 480 RF 0 LR 0 RR 0 OUT 0 0 0 0"))
    assert not speed_reply_is_stopped(parse_chassis_reply("ENC LF 0 RF 0 LR 0 RR 0"))
    assert not speed_reply_is_stopped(parse_chassis_reply("SPD LF 0 RF 0 LR 0"))
