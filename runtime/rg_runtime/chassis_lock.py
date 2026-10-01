"""Exclusive access to the chassis serial port.

The runtime control hub and ``run_route_v2.py`` both open
``/dev/robogame-chassis``, and two readers on one tty steal each other's
replies.  One advisory lock file is what keeps them apart: a consumer holds
``LOCK_EX`` for as long as it has the port open, and a second one fails fast
with ``ChassisPortBusy`` instead of silently corrupting the first one's stream.

Until 2026-10-01 there was a third party in this protocol: the
``robogame-chassis-rfcomm`` maintainer service, which rebuilt ``/dev/rfcomm0``
whenever the JDY-31 hung up and had to be kept away from a live consumer.  The
chassis is on a wired UART now and that service is gone, so the lock has one job
left -- route vs. console.

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
