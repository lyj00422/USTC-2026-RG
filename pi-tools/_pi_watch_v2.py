"""One-shot status read of the newest route-v2 run on the Pi.  Read-only.

Prints liveness, the last telemetry row's decision fields, the state sequence so
far, and whether the run looks wedged in a silent hold.  Route v2 never retries
and never exits on its own when it fails -- it just holds with intent "stop" --
so "state unchanged with intent stop across many ticks" is the thing to look for.

Usage (on the Pi, via _pi_run_file.py):
    .venv/bin/python /tmp/_pi_watch_v2.py [glob]
"""

import collections
import glob
import json
import os
import sys

DEFAULT_GLOB = "/home/pi/robogame-runtime/logs/route_v2_full_orange_*.jsonl"
HOLD_TICKS = 60  # ~3 s at the 0.05 s poll period


def running_pids():
    """PIDs whose cmdline is run_route_v2.py, read straight from /proc."""
    pids = []
    for entry in os.listdir("/proc"):
        if not entry.isdigit():
            continue
        try:
            with open(f"/proc/{entry}/cmdline", "rb") as handle:
                cmdline = handle.read().decode("utf-8", "replace")
        except OSError:
            continue
        if "run_route_v2.py" in cmdline:
            pids.append((int(entry), cmdline.replace("\x00", " ").strip()))
    return pids


def main():
    pattern = sys.argv[1] if len(sys.argv) > 1 else DEFAULT_GLOB
    matches = sorted(glob.glob(pattern))
    if not matches:
        print(f"no telemetry file matching {pattern}")
        return 1
    path = matches[-1]
    print(f"file      : {path}")

    pids = running_pids()
    if pids:
        for pid, cmdline in pids:
            print(f"ALIVE     : pid {pid}")
    else:
        print("ALIVE     : no run_route_v2.py process")

    rows = []
    with open(path, encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if line:
                try:
                    rows.append(json.loads(line))
                except json.JSONDecodeError:
                    pass  # the runner is mid-write on the last line
    if not rows:
        print("ticks     : file has no complete rows yet")
        return 0

    t0, t1 = rows[0]["t"], rows[-1]["t"]
    print(f"ticks     : {len(rows)}   elapsed {t1 - t0:.1f}s")

    seq = []
    for row in rows:
        if not seq or seq[-1][0] != row["state"]:
            seq.append([row["state"], 1, row["t"]])
        else:
            seq[-1][1] += 1
    print("states    :")
    for name, count, start in seq:
        print(f"    {name:<32} {count:>5} ticks   from t+{start - t0:7.2f}s")

    last = rows[-1]
    print("last row  :")
    for key in ("t", "state", "intent", "mask", "line_error", "actual_speed",
                "issued", "travel_cm", "lateral_cm", "wall_contact",
                "pickup_phase", "pickup_result", "pickup_action"):
        if key in last:
            print(f"    {key:<14} {last[key]}")
    if isinstance(last.get("vision"), dict):
        print(f"    vision         {last['vision']}")

    tail = rows[-HOLD_TICKS:]
    if (len(tail) >= HOLD_TICKS
            and all(r.get("intent") == "stop" for r in tail)
            and len({r["state"] for r in tail}) == 1):
        print(f"\n*** SILENT HOLD: intent=stop and state {tail[0]['state']} "
              f"unchanged for the last {len(tail)} ticks ***")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
