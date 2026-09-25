"""Is the periodic RFCOMM drop an *idle* disconnect, or is the link dying anyway?

The chassis link drops every ~10-20s while nothing is talking to it.  Those are
two very different problems:

  * idle disconnect   -> a read-only keepalive fixes it, and route v2 (which
                         talks to the chassis constantly) never sees it
  * link dies anyway  -> the operator's "keep the chassis moving" plan will not
                         help either, and a full run cannot finish

This holds the port lock and polls SPD (a read-only query -- it commands no
motion) once a second for 90s, recording every gap.  Nothing is commanded.
"""
import fcntl
import os
import select
import termios
import time

DEV = "/dev/robogame-chassis"
LOCK = "/run/lock/robogame-chassis.lock"
DURATION = 90.0
POLL = 1.0

lock_fd = os.open(LOCK, os.O_RDWR | os.O_CREAT, 0o666)
try:
    fcntl.flock(lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
except BlockingIOError:
    raise SystemExit("chassis lock is held by another process; refusing to run")

# The maintainer releases and recreates /dev/rfcomm0 on every reconnect, so the
# alias blinks in and out for a few seconds at a time.  Wait for a window.
for _ in range(300):
    if os.path.exists(DEV):
        break
    time.sleep(0.2)
else:
    raise SystemExit(f"{DEV} never appeared within 60s")

fd = os.open(DEV, os.O_RDWR | os.O_NOCTTY | os.O_NONBLOCK)
attrs = termios.tcgetattr(fd)
attrs[0] = 0
attrs[1] = 0
attrs[2] = termios.CLOCAL | termios.CREAD | termios.CS8
attrs[3] = 0
attrs[4] = termios.B9600
attrs[5] = termios.B9600
termios.tcsetattr(fd, termios.TCSANOW, attrs)
termios.tcflush(fd, termios.TCIOFLUSH)


def read_for(seconds):
    deadline = time.time() + seconds
    buf = b""
    while time.time() < deadline:
        r, _, _ = select.select([fd], [], [], 0.1)
        if r:
            try:
                buf += os.read(fd, 256)
            except BlockingIOError:
                pass
    return buf.decode("utf-8", "replace").strip()


started = time.time()
ok = 0
fail = 0
gaps = []
last_ok = started
print(f"t     | reply")
print("-" * 60)
while time.time() - started < DURATION:
    t = time.time() - started
    try:
        os.write(fd, b"SPD\n")
        reply = read_for(0.6)
    except OSError as exc:
        reply = f"<OSError {exc.errno}: {exc.strerror}>"
    if "SPD" in reply:
        ok += 1
        gap = time.time() - last_ok - POLL
        if gap > 3.0:
            gaps.append((round(t, 1), round(gap, 1)))
            print(f"{t:6.1f} | RECOVERED after {gap:.1f}s gap")
        last_ok = time.time()
        print(f"{t:6.1f} | {reply}")
    else:
        fail += 1
        print(f"{t:6.1f} | FAILED: {reply!r}")

print("-" * 60)
print(f"duration      : {time.time() - started:.1f}s   polls={ok + fail}")
print(f"ok / failed   : {ok} / {fail}")
print(f"gaps > 3s     : {gaps if gaps else 'none'}")
print(f"longest gap   : {max([g for _, g in gaps], default=0.0):.1f}s")
os.close(fd)
