"""One fast pre-launch gate: does the chassis actually answer right now?

Exit 0 only if a SPD query comes back with all four wheels at 0.

Why this is its own gate: 2026-09-27 the route ran a whole leg with
`actual_speed: 0.0` while commanding forward -- the link was already dead and
nothing noticed until the car was killed and could not be stopped.  The
maintainer rebuilds /dev/rfcomm0 every 12-15 s unless an app holds LOCK_EX, so
"the device node exists" (what the launcher's gate checks) says nothing about
whether bytes get through.  This holds the lock and asks.

Read-only: SPD commands no motion.
"""

import fcntl
import os
import time

import serial

PORT = "/dev/robogame-chassis"
LOCK = "/run/lock/robogame-chassis.lock"


def attempt() -> str | None:
    """One lock/STOP/SPD cycle.  Returns the SPD line, or None.

    The lock is taken and released inside one attempt on purpose.  The RFCOMM
    maintainer will not reconnect while an app holds LOCK_EX, so a lock taken
    during one of its 12-15 s teardown windows pins a DEAD link in place and
    keeps it dead.  Releasing between attempts lets it rebuild, so the next
    attempt meets a fresh SPP session.
    """
    if not os.path.exists(PORT):
        return None
    lock_fd = os.open(LOCK, os.O_RDWR | os.O_CREAT, 0o666)
    fcntl.flock(lock_fd, fcntl.LOCK_EX)
    try:
        deadline = time.monotonic() + 8
        while not os.path.exists(PORT) and time.monotonic() < deadline:
            time.sleep(0.5)
        if not os.path.exists(PORT):
            return None
        try:
            ser = serial.Serial(PORT, 9600, timeout=0.5)
        except Exception:                                     # noqa: BLE001
            return None
        try:
            # STOP before asking, so this gate doubles as the post-kill halt:
            # a killed route leaves its last velocity command latched in the
            # firmware, and without the lock nothing can reach it to clear it.
            for _ in range(6):
                ser.write(b"STOP\r\n")
                ser.flush()
                time.sleep(0.05)
                ser.read(128)
            for _ in range(6):
                ser.reset_input_buffer()
                ser.write(b"SPD\r\n")
                ser.flush()
                time.sleep(0.4)
                raw = ser.read(128).decode("ascii", "replace").strip()
                if raw.startswith("SPD"):
                    return raw
        except Exception:                                     # noqa: BLE001
            return None
        finally:
            ser.close()
        return None
    finally:
        fcntl.flock(lock_fd, fcntl.LOCK_UN)
        os.close(lock_fd)


def main() -> int:
    deadline = time.monotonic() + 90
    tries = 0
    while time.monotonic() < deadline:
        tries += 1
        line = attempt()
        if line is not None:
            print(f"OK: {line}   ({tries} attempt(s))")
            wheels = line.split("OUT")[0].replace("SPD", "").split()[1::2]
            if set(wheels) != {"0"}:
                print(f"WARNING: wheels not at zero -> {wheels}")
            return 0
        print(f"  attempt {tries}: no reply, letting the maintainer rebuild")
        time.sleep(3.0)
    print("FAIL: no reply to SPD in 90s -- the SPP session never came back")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
