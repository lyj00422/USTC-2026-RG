import sys

import pytest

from rg_runtime.chassis_lock import ChassisPortBusy, ChassisPortLock

posix_only = pytest.mark.skipif(sys.platform == "win32", reason="flock is POSIX-only")


@posix_only
def test_second_holder_is_refused_until_the_first_releases(tmp_path):
    path = tmp_path / "chassis.lock"
    first = ChassisPortLock(path)
    first.acquire()
    second = ChassisPortLock(path)
    with pytest.raises(ChassisPortBusy):
        second.acquire()
    first.release()
    second.acquire()
    second.release()


@posix_only
def test_release_is_idempotent_and_acquire_is_reentrant_safe(tmp_path):
    lock = ChassisPortLock(tmp_path / "chassis.lock")
    lock.acquire()
    lock.acquire()
    assert lock.held is True
    lock.release()
    lock.release()
    assert lock.held is False


def test_context_manager_holds_and_releases(tmp_path):
    with ChassisPortLock(tmp_path / "chassis.lock") as lock:
        assert lock.held is True or lock.degraded is True
    assert lock.held is False
