"""Owner of the Bluetooth chassis TTY: port lock, transport, heartbeat, reconnect.

Why this exists
---------------
The JDY-31 hangs up an idle SPP session after roughly 13-20 s.  The tty
``/dev/rfcomm0`` *is* that session: when it goes, the maintainer service rebuilds
the node, and whoever held the old descriptor gets a permanent ``[Errno 5]`` --
no retry revives that fd, only a reopen does.

Route v2 used to keep the link alive from inside its tick loop (the
``chassis_keepalive_s`` branch in ``run_route_v2.py``), so the guarantee only
existed *after the first tick*.  A run's opening 20-40 s is spent on line-sensor
warm-up, camera, arm and action-catalogue initialisation with the port already
open and nothing on the wire.  Measured over the 60 archived runs of
2026-09-15..29: every one of the 10 link drops falls inside the first 90 s, and
the three longest (22.5 / 20.4 / 18.2 s) sit at 23.0 / 25.4 / 27.2 s -- exactly
where the first tick finally meets the chassis.

So the heartbeat lives here instead: it starts the moment the port opens and runs
on its own thread, independent of whatever the control flow is doing.  That is
what the control hub has always done (``control_hub/server.py`` drives
``ChassisService.keepalive_tick`` from a dedicated loop), and it is the whole
reason the console holds this link while the route kept losing it.

Two rules this component exists to enforce
------------------------------------------
1. **Nothing goes on the wire outside ``self._lock``.**  ``SerialTransport`` and
   ``ChassisDevice`` have no synchronisation of their own; the heartbeat thread
   and the route's tick would otherwise interleave bytes mid-command.
2. **The heartbeat is quiet-windowed, not unconditional.**  The firmware answers
   only the *first* command of a back-to-back burst, so a keepalive landing on
   top of a motion command can swallow that ``V`` -- the car then does not move
   and it looks exactly like a dead robot.  ``heartbeat_quiet_s`` is the hub's
   proven value for this (``config/runtime.yaml`` ``keepalive_quiet_s: 2``).

Note what a heartbeat can and cannot do.  It defeats an *idle* teardown by the
peer.  It cannot defeat the peer losing power -- if the chassis and the arm share
a supply and a hard turn browns the chassis MCU out, the session dies regardless
of how recently we spoke.  ``recover`` therefore also reports how long the wire
had been silent when the EIO arrived, which is what distinguishes the two.
"""

from __future__ import annotations

import os
import re
import sys
import threading
import time
from typing import Callable

from .chassis_lock import ChassisPortLock
from .devices import ChassisDevice
from .transports import SerialTransport


def speed_reply_is_stopped(reply) -> bool:
    """True only for a parsed SPD reply reporting every channel at zero.

    The reconnect only hands a fresh chassis back to the route once this says the
    wheels are stopped -- the half-sent command that broke the old link may have
    reached the firmware, so the new link is never trusted blind.
    """
    if getattr(reply, "kind", None) != "speed":
        return False
    values = [int(value) for value in re.findall(r"[-+]?\d+", str(getattr(reply, "value", "")))]
    return len(values) >= 8 and all(value == 0 for value in values[:8])


def wait_for_chassis_device(path: str, *, timeout_s: float = 40.0) -> None:
    """Block until the RFCOMM node exists, then let the caller open it.

    The maintainer service drops and rebuilds the node whenever the link idles
    out: JDY-31 hangs up after roughly 20 s of silence, and nothing talks to it
    between runs.  Once a consumer is talking, the link holds.

    This must happen BEFORE the port lock is taken.  The maintainer only tests the
    lock at the top of its loop, so a lock held while the node is missing stops
    the one service that could rebuild it -- the deadlock that stranded the robot
    on 2026-09-14.
    """
    if os.name != "posix":
        # The chassis TTY and its maintainer service only exist on the Pi; on a
        # Windows development host this would block for the full timeout and then
        # fail a run that the tests deliberately drive with a fake chassis.
        return
    if os.path.exists(path):
        return
    print(f"waiting for {path} (the RFCOMM maintainer rebuilds it after the link idles out)")
    deadline = time.monotonic() + timeout_s
    while not os.path.exists(path):
        if time.monotonic() >= deadline:
            raise RuntimeError(
                f"{path} did not appear within {timeout_s:.0f}s; the RFCOMM maintainer is not "
                "rebuilding it -- check systemctl status robogame-chassis-rfcomm"
            )
        time.sleep(0.5)
    print(f"{path} is back")


class ChassisLink:
    """The route's whole view of the chassis: a live tty, or a raised OSError.

    Exposes exactly the ``ChassisDevice`` surface the route calls --
    ``set_velocity`` / ``run_distance`` / ``stop`` / ``poll`` / ``request_speed``
    / ``request_encoder`` -- so it is a drop-in for the device the route already
    drove.  ``heartbeat_tick`` is deliberately a plain synchronous method with an
    injectable clock: the project's whole test style for this kind of logic is a
    direct call with a fake clock (see ``control_hub/tests/test_chassis_service.py``
    for the keepalive tests it mirrors), and a thread would make that untestable.
    The thread below is a thin driver over it and nothing more.
    """

    def __init__(
        self,
        device: str,
        baudrate: int,
        *,
        port_lock: ChassisPortLock | None = None,
        transport_factory: Callable[..., object] | None = None,
        clock: Callable[[], float] = time.monotonic,
        sleeper: Callable[[float], None] = time.sleep,
        log: Callable[[str], None] = print,
        wait_for_device: Callable[..., None] | None = None,
        open_timeout_s: float = 40.0,
        heartbeat_enabled: bool = True,
        heartbeat_s: float = 5.0,
        heartbeat_quiet_s: float = 2.0,
        heartbeat_log_every_s: float = 60.0,
        heartbeat_poll_s: float = 0.2,
        recover_timeout_s: float = 90.0,
        recover_retry_s: float = 1.0,
        stop_confirm_timeout_s: float = 5.0,
    ) -> None:
        self.device = device
        self.baudrate = baudrate
        self.port_lock = port_lock if port_lock is not None else ChassisPortLock()
        self.transport_factory = transport_factory or (
            lambda path, baud: SerialTransport(path, baud, timeout_s=0.0)
        )
        self.wait_for_device = wait_for_device or wait_for_chassis_device
        self.open_timeout_s = open_timeout_s

        self.heartbeat_enabled = bool(heartbeat_enabled)
        # Mirror ChassisService's floor: a sub-second period would turn the
        # heartbeat into the 0.15 s query stream the D-wait exclusion exists to
        # avoid, and would collide with in-flight commands far more often.
        self.heartbeat_s = max(1.0, float(heartbeat_s))
        self.heartbeat_quiet_s = max(0.0, float(heartbeat_quiet_s))
        self.heartbeat_log_every_s = max(0.0, float(heartbeat_log_every_s))
        self.heartbeat_poll_s = max(0.05, float(heartbeat_poll_s))
        self.recover_timeout_s = float(recover_timeout_s)
        self.recover_retry_s = float(recover_retry_s)
        self.stop_confirm_timeout_s = float(stop_confirm_timeout_s)

        self._clock = clock
        self._sleep = sleeper
        self._log = log

        # One lock for everything that touches the wire, held across the
        # check-send-stamp sequence so a heartbeat cannot slot in between the
        # tick's decision to move and the bytes it writes.
        self._lock = threading.RLock()
        self._device: ChassisDevice | None = None
        self._transport = None
        self._last_tx_at: float | None = None
        self._next_heartbeat_at: float | None = None
        self._heartbeat_sends = 0
        self._heartbeat_defers = 0
        self._last_heartbeat_log_at: float | None = None
        self._reconnects = 0

        self._stop = threading.Event()
        self._stop.set()
        self._thread: threading.Thread | None = None

    # ------------------------------------------------------------------ state

    @property
    def connected(self) -> bool:
        with self._lock:
            return self._device is not None

    @property
    def heartbeat_sends(self) -> int:
        return self._heartbeat_sends

    @property
    def heartbeat_defers(self) -> int:
        """Ticks that were due but stood down for a live command."""
        return self._heartbeat_defers

    @property
    def reconnects(self) -> int:
        return self._reconnects

    def seconds_since_last_tx(self) -> float | None:
        with self._lock:
            if self._last_tx_at is None:
                return None
            return self._clock() - self._last_tx_at

    # -------------------------------------------------------------- lifecycle

    def open(self) -> "ChassisLink":
        """Wait for the node, take the port lock, open it, prime, start beating."""
        self.wait_for_device(self.device, timeout_s=self.open_timeout_s)
        self.port_lock.acquire()
        try:
            self._transport = self.transport_factory(self.device, self.baudrate)
            self._device = ChassisDevice(self._transport)
            # Prime: also the first byte on the wire, so the heartbeat's quiet
            # window is measured from a real transmit rather than from boot.
            self._device.stop()
        except Exception:
            self.port_lock.release()
            self._transport = None
            self._device = None
            raise
        now = self._clock()
        self._last_tx_at = now
        self._next_heartbeat_at = now + self.heartbeat_s
        self._start_heartbeat()
        return self

    def close(self, *, timeout_s: float = 2.0) -> None:
        self._stop_heartbeat(timeout_s=timeout_s)
        with self._lock:
            try:
                if self._transport is not None:
                    self._transport.close()
            except Exception:
                pass
            self._transport = None
            self._device = None
        self.port_lock.release()

    def __enter__(self) -> "ChassisLink":
        return self.open()

    def __exit__(self, *_exc) -> bool:
        self.close()
        return False

    # ------------------------------------------------------------ heartbeat

    def heartbeat_tick(self, now: float | None = None) -> bool:
        """Send one read-only ``SPD`` if the link has been idle long enough.

        Returns True when a keepalive byte actually went out.  Called by the
        heartbeat thread; called directly with a fake clock by the tests.
        """
        if not self.heartbeat_enabled:
            return False
        with self._lock:
            if self._device is None:
                return False
            now = self._clock() if now is None else now
            if self._next_heartbeat_at is None or now < self._next_heartbeat_at:
                return False
            # Re-arm before the quiet check, exactly as ChassisService does: a
            # deferred tick waits a full period rather than firing the instant
            # the window closes.
            self._next_heartbeat_at = now + self.heartbeat_s
            if self._last_tx_at is not None and now - self._last_tx_at < self.heartbeat_quiet_s:
                self._heartbeat_defers += 1
                return False
            try:
                self._device.request_speed()
            except Exception as exc:
                self._handle_error(exc)
                raise
            self._last_tx_at = now
            self._heartbeat_sends += 1
            if (
                self._last_heartbeat_log_at is None
                or now - self._last_heartbeat_log_at >= self.heartbeat_log_every_s
            ):
                self._last_heartbeat_log_at = now
                self._log(
                    f"chassis heartbeat: {self._heartbeat_sends} sends, "
                    f"{self._heartbeat_defers} deferred"
                )
            return True

    def _start_heartbeat(self) -> None:
        if not self.heartbeat_enabled:
            return
        self._stop.clear()
        self._thread = threading.Thread(
            target=self._heartbeat_loop, name="chassis-heartbeat", daemon=True
        )
        self._thread.start()

    def _stop_heartbeat(self, *, timeout_s: float = 2.0) -> None:
        self._stop.set()
        thread, self._thread = self._thread, None
        if thread is not None and thread.is_alive():
            # Never join without a timeout: a heartbeat blocked on a wedged tty
            # must not be able to hang the run's cleanup.
            thread.join(timeout=timeout_s)

    def _heartbeat_loop(self) -> None:
        while not self._stop.wait(self.heartbeat_poll_s):
            try:
                self.heartbeat_tick()
            except Exception as exc:
                # Do not die silently on a daemon thread, and do not raise into
                # it either -- the broken fd raises for the route's own next call
                # anyway, which is where recover() is triggered.  Log once, stop
                # beating, and let the run find out the normal way.
                self._log(f"chassis heartbeat stopped: {exc}")
                self._stop.set()
                return

    # ------------------------------------------------------------- wire API

    def set_velocity(self, vx: int, vy: int, wz: int) -> None:
        self._send(lambda device: device.set_velocity(vx, vy, wz))

    def run_distance(self, forward_cm: int, right_cm: int, rotate_deg: int, speed: int) -> None:
        self._send(lambda device: device.run_distance(forward_cm, right_cm, rotate_deg, speed))

    def stop(self) -> None:
        self._send(lambda device: device.stop())

    def request_speed(self) -> None:
        self._send(lambda device: device.request_speed())

    def request_encoder(self) -> None:
        self._send(lambda device: device.request_encoder())

    def poll(self) -> list:
        # A read still takes the lock: SerialTransport.read_lines mutates a shared
        # rx buffer that the heartbeat thread's replies land in too.
        with self._lock:
            device = self._require_device()
            try:
                return device.poll()
            except Exception as exc:
                self._handle_error(exc)
                raise

    def _send(self, action) -> None:
        with self._lock:
            device = self._require_device()
            try:
                action(device)
            except Exception as exc:
                self._handle_error(exc)
                raise
            self._last_tx_at = self._clock()

    def _require_device(self) -> ChassisDevice:
        if self._device is None:
            raise RuntimeError("chassis link is not open")
        return self._device

    def _handle_error(self, exc: Exception) -> None:
        # Deliberately does not close anything.  The route's run loop catches the
        # OSError and calls recover(), which owns the whole teardown -- closing
        # here would race the recovery and could release a lock mid-reconnect.
        if isinstance(exc, OSError):
            self._stop.set()

    # ------------------------------------------------------------- recovery

    def recover(self, exc: OSError) -> "ChassisLink":
        """Rebuild the link after a broken handle, and return ``self``.

        Returning ``self`` matters: the run loop assigns the result back onto its
        chassis handle (``self.chassis = self.recover_chassis(exc)``), and this
        object *is* that handle -- the device was swapped inside it.
        """
        self._stop_heartbeat(timeout_s=2.0)
        with self._lock:
            idle = None if self._last_tx_at is None else self._clock() - self._last_tx_at
            self._log(f"CHASSIS_RECONNECT link error: {exc}; releasing route lock")
            if idle is not None:
                # The whole point of this line: ~13-20 s means the peer dropped an
                # idle session (what the heartbeat is for).  Something far shorter
                # means the session died while we were actively talking -- look at
                # power, not at keepalives.
                self._log(f"CHASSIS_RECONNECT last byte on the wire {idle:.1f}s before the error")
            try:
                if self._transport is not None:
                    self._transport.close()
            except Exception:
                pass
            self._transport = None
            self._device = None
            # The maintainer is forbidden from touching a tty a run is holding, so
            # the lock must be dropped before it can run its rfcomm cycle.
            self.port_lock.release()

        last_error: Exception | None = None
        deadline = self._clock() + self.recover_timeout_s
        while self._clock() < deadline:
            candidate_transport = None
            acquired = False
            try:
                self.wait_for_device(
                    self.device,
                    timeout_s=min(self.recover_retry_s, max(0.1, deadline - self._clock())),
                )
                self.port_lock.acquire()
                acquired = True
                candidate_transport = self.transport_factory(self.device, self.baudrate)
                candidate = ChassisDevice(candidate_transport)
                candidate.stop()
                stop_deadline = self._clock() + min(
                    self.stop_confirm_timeout_s, max(0.5, deadline - self._clock())
                )
                confirmed = False
                while self._clock() < stop_deadline:
                    candidate.request_speed()
                    self._sleep(0.1)
                    if any(speed_reply_is_stopped(reply) for reply in candidate.poll()):
                        confirmed = True
                        break
                if not confirmed:
                    raise RuntimeError("reconnected chassis did not confirm zero speed")
                with self._lock:
                    self._transport = candidate_transport
                    self._device = candidate
                    now = self._clock()
                    self._last_tx_at = now
                    self._next_heartbeat_at = now + self.heartbeat_s
                self._reconnects += 1
                self._log("CHASSIS_RECONNECT connected and STOP confirmed")
                self._start_heartbeat()
                return self
            except Exception as reconnect_error:
                last_error = reconnect_error
                if candidate_transport is not None:
                    try:
                        candidate_transport.close()
                    except Exception:
                        pass
                if acquired:
                    self.port_lock.release()
                self._sleep(min(self.recover_retry_s, max(0.05, deadline - self._clock())))
        raise RuntimeError(
            f"chassis reconnect failed within {self.recover_timeout_s:.0f}s: {last_error}"
        ) from last_error
