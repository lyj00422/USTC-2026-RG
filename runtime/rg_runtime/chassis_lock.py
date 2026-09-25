"""Exclusive access to the Bluetooth chassis serial port.

The RFCOMM TTY is created and held open by the ``robogame-chassis-rfcomm``
systemd service.  The runtime control hub and ``run_route_v2.py`` both open that
same TTY, and two readers steal each other's replies.  Worse, the maintainer
service used to run ``rfcomm release`` underneath a live consumer, which gave
the consumer ``[Errno 5] Input/output error`` and left the kernel RFCOMM session
busy, after which every reconnect failed with ``Device or resource busy``.

All three sides coordinate through one advisory lock file:

* a consumer holds ``LOCK_EX`` for as long as it has the port open;
* the maintainer only releases and reconnects when it can take ``LOCK_EX``
  itself, i.e. when no application is using the port.

On hosts without ``fcntl`` (the Windows development machines) the lock degrades
to a no-op: the chassis link only exists on the Raspberry Pi.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

DEFAULT_LOCK_PATH = "/run/lock/robogame-chassis.lock"


class ChassisPortBusy(RuntimeError):
    """Another process is already driving the chassis serial port."""


class ChassisPortLock:
    def __init__(self, path: str | os.PathLike[str] | None = None) -> None:
        self.path = Path(path or os.environ.get("ROBOGAME_CHASSIS_LOCK") or DEFAULT_LOCK_PATH)
        self.degraded = False
        self._handle = None

    @property
    def held(self) -> bool:
        return self._handle is not None

    def _open(self):
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            return self.path.open("a+")
        except OSError:
            # The lock file may be root-owned and 0644 (created by the service).
            # flock works on a read-only descriptor too.
            return self.path.open("r")

    def acquire(self) -> None:
        if self._handle is not None:
            return
        try:
            import fcntl
        except ImportError:  # pragma: no cover - Windows development hosts
            self.degraded = True
            return
        try:
            handle = self._open()
        except OSError as exc:
            # Infrastructure problem, not contention: keep working rather than
            # bricking the operator console over a missing lock directory.
            print(f"chassis port lock unavailable ({exc}); continuing unlocked", file=sys.stderr)
            self.degraded = True
            return
        try:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as exc:
            handle.close()
            raise ChassisPortBusy(
                f"chassis serial port is already in use ({self.path}); "
                "stop the other runtime/route program first"
            ) from exc
        self._handle = handle

    def release(self) -> None:
        handle, self._handle = self._handle, None
        if handle is None:
            return
        try:
            import fcntl

            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
        except Exception:
            pass
        finally:
            handle.close()

    def __enter__(self) -> "ChassisPortLock":
        self.acquire()
        return self

    def __exit__(self, *_exc) -> bool:
        self.release()
        return False
