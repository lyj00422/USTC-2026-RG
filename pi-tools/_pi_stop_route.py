"""Stop a running route the way the project requires: SIGTERM, never SIGKILL.

Run ON the Pi via _pi_run_file.py.

    python pi-tools\\_pi_run_file.py pi-tools\\_pi_stop_route.py

SIGKILL skips the route's cleanup and leaks the pigpio GPIO24 claim, which then
blocks the NEXT launch -- so a stuck run must be terminated politely.

The launcher goes first.  `start_full_route.py` retries a run that died in its
startup window, so killing only the child can have it start a fresh one behind
you; killing the parent first means nothing is left to retry.

Self-match is avoided deliberately (see the route-launch-guard note): the command
line is filtered to the route scripts and everything whose own cmdline carries
this script's name is skipped, so the shell that shipped this file cannot be the
thing that gets killed.
"""
import os
import signal
import subprocess
import time


def pid_lines(pattern):
    out = subprocess.run(["pgrep", "-af", pattern],
                         capture_output=True, text=True).stdout
    rows = []
    for line in out.splitlines():
        pid, _, cmd = line.partition(" ")
        if not pid.isdigit():
            continue
        if "_pi_stop_route" in cmd or "_pi_run_file" in cmd:
            continue                      # our own delivery shell / temp script
        if int(pid) in (os.getpid(), os.getppid()):
            continue
        rows.append((int(pid), cmd))
    return rows


targets = []
for pattern in ("start_full_route", "run_route_v2"):
    for pid, cmd in pid_lines(pattern):
        if all(pid != other for other, _ in targets):
            targets.append((pid, cmd))

if not targets:
    print("nothing running -- no route to stop")
    raise SystemExit(0)

# Parent (the launcher) first, so it cannot retry behind us.
targets.sort(key=lambda item: "start_full_route" not in item[1])
for pid, cmd in targets:
    try:
        os.kill(pid, signal.SIGTERM)
        print(f"TERM {pid}  {cmd[:100]}")
    except ProcessLookupError:
        print(f"gone {pid}")

for _ in range(20):
    time.sleep(0.5)
    left = [(pid, cmd) for pid, cmd in targets
            if subprocess.run(["kill", "-0", str(pid)],
                              capture_output=True).returncode == 0]
    if not left:
        break
print("stopped" if not left else f"STILL ALIVE: {[p for p, _ in left]}")
