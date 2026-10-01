"""Owner of the chassis UART: port lock, transport, reconnect.

The chassis is on a wire.  STM32 USART3 lives on PB10/PB11 and is brought out to
a Raspberry Pi 4B's UART2 on GPIO0/GPIO1 (physical pins 27/28), 9600 8N1, plain
ASCII lines terminated with CR+LF.  Nothing about the wire protocol changed when
we moved off the JDY-31 Bluetooth module -- only what carried it.

What used to be here, and why it is gone
----------------------------------------
This file was written around a Bluetooth SPP session, and two thirds of it
existed to prop that session up:

* The **heartbeat thread**.  The JDY-31 tears down an idle SPP session after
  roughly 13-20 s of silence, and a run's opening 20-40 s is spent on line
  sensor warm-up, camera, arm and action-catalogue initialisation *with the port
  already open and nothing on the wire*.  Measured over the 60 archived runs of
  2026-09-15..29, all 10 link drops fell inside the first 90 s.  So a thread
  sent a read-only ``SPD`` every 5 s from the moment the port opened.  A UART
  has no session and nothing to idle out; the whole mechanism is dead weight.
  It was not free, either: the firmware answers only the *first* command of a
  back-to-back burst, so a heartbeat landing on top of a ``V`` could swallow
  that ``V`` and leave the car standing still, looking exactly like a dead
  robot.
* The **rfcomm tty wait**.  ``/dev/robogame-chassis`` used to be a udev alias for
  ``/dev/rfcomm0``, rebuilt by the ``robogame-chassis-rfcomm`` maintainer service
  every time the peer hung up.  There is no maintainer any more: the alias now
  points at a UART, which exists from boot and never goes away.

What is deliberately still here
-------------------------------
* **The port lock.**  It no longer coordinates with a maintainer, but the route
  and the control hub still must not both hold this tty -- two readers steal
  each other's replies.  ``ChassisPortLock`` is what stops that.
* **The reconnect.**  A wire does not flap, but the STM32 can reset or brown out
  and a tty writes into the void without complaining, so a failure still
  surfaces as an OSError on some later call.  ``recover`` rebuilds the handle
  and, before handing it back, requires a STOP readback that says every wheel is
  stopped -- the half-sent command that broke the old handle may have reached
  the firmware.
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
    """Block until the chassis tty exists, then let the caller open it.

    The node is not created by the program: ``dtoverlay=uart2`` in
    ``/boot/firmware/config.txt`` brings the UART up, and a udev rule
    (``/etc/udev/rules.d/99-robogame-chassis.rules``) gives it the stable
    ``/dev/robogame-chassis`` name.  Both are boot-time, so in practice this
    returns immediately -- it is here so that a missing overlay or a stale udev
    rule fails with that sentence instead of a bare
    ``[Errno 2] No such file or directory`` from pyserial.

    This must happen BEFORE the port lock is taken, so a held lock can never be
    the reason the node is missing.
    """
    if os.name != "posix":
        # The chassis tty only exists on the Pi; on a Windows development host
        # this would block for the full timeout and then fail a run that the
        # tests deliberately drive with a fake chassis.
        return
    if os.path.exists(path):
        return
    print(f"waiting for {path}")
    deadline = time.monotonic() + timeout_s
    while not os.path.exists(path):
        if time.monotonic() >= deadline:
            raise RuntimeError(
                f"{path} did not appear within {timeout_s:.0f}s -- the uart2 overlay or the "
                "udev rule that creates the alias is not in effect.  Check the "
                "dtoverlay=uart2 line in /boot/firmware/config.txt and "
                "/etc/udev/rules.d/99-robogame-chassis.rules"
            )
        time.sleep(0.5)
    print(f"{path} is back")


class ChassisLink:
    """The route's whole view of the chassis: a live tty, or a raised OSError.

    Exposes exactly the ``ChassisDevice`` surface the route calls --
    ``set_velocity`` / ``run_distance`` / ``stop`` / ``poll`` / ``request_speed``
    / ``request_encoder`` -- so it is a drop-in for the device the route already
    drove.
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

        self.recover_timeout_s = float(recover_timeout_s)
        self.recover_retry_s = float(recover_retry_s)
        self.stop_confirm_timeout_s = float(stop_confirm_timeout_s)

        self._clock = clock
        self._sleep = sleeper
        self._log = log

        # One lock for everything that touches the wire, held across the
        # check-send-stamp sequence so nothing can slot in between the tick's
        # decision to move and the bytes it writes.  Nothing else runs on its own
        # thread any more, but ``recover`` swaps the transport under this lock and
        # the reads still share one rx buffer.
        self._lock = threading.RLock()
        self._device: ChassisDevice | None = None
        self._transport = None
        self._last_tx_at: float | None = None
        self._reconnects = 0

    # ------------------------------------------------------------------ state

    @property
    def connected(self) -> bool:
        with self._lock:
            return self._device is not None

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
        """Wait for the node, take the port lock, open it, prime it."""
        self.wait_for_device(self.device, timeout_s=self.open_timeout_s)
        self.port_lock.acquire()
        try:
            self._transport = self.transport_factory(self.device, self.baudrate)
            self._device = ChassisDevice(self._transport)
            # Prime with a STOP: whatever the STM32 was doing before we attached,
            # it is not doing it after this.
            self._device.stop()
        except Exception:
            self.port_lock.release()
            self._transport = None
            self._device = None
            raise
        self._last_tx_at = self._clock()
        return self

    def close(self, *, timeout_s: float = 2.0) -> None:
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
        # rx buffer.
        with self._lock:
            device = self._require_device()
            return device.poll()

    def _send(self, action) -> None:
        with self._lock:
            device = self._require_device()
            action(device)
            self._last_tx_at = self._clock()

    def _require_device(self) -> ChassisDevice:
        if self._device is None:
            raise RuntimeError("chassis link is not open")
        return self._device

    # ------------------------------------------------------------- recovery

    def recover(self, exc: OSError) -> "ChassisLink":
        """Rebuild the link after a broken handle, and return ``self``.

        Returning ``self`` matters: the run loop assigns the result back onto its
        chassis handle (``self.chassis = self.recover_chassis(exc)``), and this
        object *is* that handle -- the device was swapped inside it.
        """
        with self._lock:
            idle = None if self._last_tx_at is None else self._clock() - self._last_tx_at
            self._log(f"CHASSIS_RECONNECT link error: {exc}; releasing route lock")
            if idle is not None:
                # On a wire there is no idle teardown to blame, so a *long* silent
                # window before the error is the informative case: it points at
                # the STM32 having reset or lost power rather than at our side.
                self._log(f"CHASSIS_RECONNECT last byte on the wire {idle:.1f}s before the error")
            try:
                if self._transport is not None:
                    self._transport.close()
            except Exception:
                pass
            self._transport = None
            self._device = None
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
                    self._last_tx_at = self._clock()
                self._reconnects += 1
                self._log("CHASSIS_RECONNECT connected and STOP confirmed")
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
