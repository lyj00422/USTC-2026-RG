"""Where did a distance timeout happen, and which `D` was it?

Run ON the Pi via _pi_run_file.py.  Reads the newest run and, for every increase
of `distance_timeouts`, prints the state, the action package and step, and the
`D` commands issued in the seconds before it -- so the lost move can be named
rather than merely counted.

A lost `D` is NOT a fault: the executor gives up after `distance_timeout_s` (10 s
since 2026-09-29) and the package advances anyway, which leaves the car short of
where the step expected it.  That is why the command matters.
"""
import glob
import json

TAG = "logs/route_v2_*.jsonl"


def main() -> None:
    hits = [p for p in glob.glob(TAG) if not p.endswith(".out")]
    if not hits:
        print("NO_LOG")
        return
    import os
    path = max(hits, key=os.path.getmtime)
    print(f"log: {path}")

    rows = []
    with open(path, encoding="utf-8") as handle:
        for line in handle:
            if line.strip():
                rows.append(json.loads(line))

    seen = 0
    for i, d in enumerate(rows):
        count = d.get("distance_timeouts") or 0
        if count <= seen:
            continue
        seen = count
        action = d.get("pickup_action") or {}
        print(f"\n--- timeout #{count} at t={d['t']:.1f}  state={d.get('state')}")
        print(f"    action={action.get('action_ref')} step {action.get('step_index')}"
              f"/{action.get('step_count')}  result={action.get('result')}")
        print(f"    travel_cm={d.get('travel_cm')}  lateral_cm={d.get('lateral_cm')}"
              f"  mask={d.get('mask')}  wall_contact={d.get('wall_contact')}")
        # Only what went out in the seconds immediately before the deadline
        # expired.  60 s of a 20 Hz bus is unreadable and buries the one thing
        # that matters: whether anything was sent BETWEEN the `D` and the
        # timeout, which is what would explain a missing DONE.
        window = [r for r in rows[:i] if r["t"] >= d["t"] - 14.0]
        issued = []
        for r in window:
            value = r.get("issued")
            if value and (not issued or issued[-1][1] != value):
                issued.append((r["t"], value))
        print(f"    on the wire in the {14.0:.0f} s before the deadline:")
        for when, value in issued:
            print(f"      t={when:.1f}  (+{when - (d['t'] - 14.0):5.1f}s)  {value}")
        if issued:
            last_t, last_v = issued[-1]
            print(f"    last command was {last_v!r} at t={last_t:.1f},"
                  f" {d['t'] - last_t:.1f} s before the deadline")

    if seen == 0:
        print("\nno distance timeouts in this run")


main()
