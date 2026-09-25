"""Sample a telemetry jsonl twice, five seconds apart, to tell a live run from a wedged one.

Read-only.  Why this exists: the runner holds /dev/robogame-chassis, so the only
honest way to ask "is it still going?" is to watch the telemetry file itself
grow.  Two SSH calls seconds apart are not enough on their own -- if the runner
has stopped writing, two samples far apart in wall time both show the same `t`.

Usage:  python _pi_sample.py [jsonl path]      (argv optional; _pi_run_file.py
                                               does not forward argv)
"""
import json
import os
import subprocess
import sys
import time

LOGS = "/home/pi/robogame-runtime/logs"


def newest_log():
    """The log being written right now.

    _pi_run_file.py does not forward argv, so an argv-supplied path silently
    falls back to whatever is hard-coded here -- which is how a "the run is
    frozen" reading once turned out to be the previous, killed run's log.
    Picking the newest file is the only default that cannot lie.
    """
    files = [os.path.join(LOGS, n) for n in os.listdir(LOGS)
             if n.endswith(".jsonl")]
    return max(files, key=os.path.getmtime)


PATH = sys.argv[1] if len(sys.argv) > 1 else newest_log()


def last():
    try:
        out = subprocess.run(["tail", "-n", "1", PATH],
                             capture_output=True, text=True, timeout=10).stdout
        d = json.loads(out)
    except Exception as exc:
        return f"UNREADABLE {exc.__class__.__name__}: {exc}"
    return (f"t={d['t']:.1f}  {d['state']}  travel={d['travel_cm']}  "
            f"lat={d['lateral_cm']}  mask=0x{int(d['mask']):02X}  "
            f"enc_delta={d['encoder_delta']}  issued={d['issued']}")


def main():
    if not os.path.exists(PATH):
        print(f"ABSENT {PATH}")
        return 1
    print(f"file  {PATH}")
    for label in ("now", "+5s"):
        size = os.path.getsize(PATH)
        with open(PATH) as fh:
            n = sum(1 for _ in fh)
        print(f"{label:<4} {size} bytes  {n} lines")
        print(f"     {last()}")
        if label == "now":
            time.sleep(5)

    alive = subprocess.run(["pgrep", "-af", "run_route_v[2]"],
                           capture_output=True, text=True).stdout.strip()
    print(f"runner: {alive or 'NOT RUNNING'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
