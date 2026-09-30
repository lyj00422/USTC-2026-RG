"""What was actually put ON THE WIRE, per state, in the newest run.

Run ON the Pi via _pi_run_file.py.  The telemetry's `issued` field is the bus
command the tick sent, so this is the ground truth for a speed change: a config
value that never reached the chassis shows up here as the old number.

Distinct consecutive values only -- a line-following leg re-issues `V 80 0 0`
twenty times a second and that is one fact, not twenty.
"""
import glob
import json
import os

TAG = "logs/route_v2_*.jsonl"

# States worth naming in full; everything else is summarised by count.
INTERESTING = ("DIRECT_ORANGE_D330", "JUNCTION_3_TO_PICKUP",
               "JUNCTION_PICKUP_2_TO_AREA", "JUNCTION_PICKUP_3_TO_AREA",
               "BUILD_AREA")


def main() -> None:
    hits = [p for p in glob.glob(TAG) if not p.endswith(".out")]
    if not hits:
        print("NO_LOG")
        return
    path = max(hits, key=os.path.getmtime)
    print(f"log: {path}\n")

    order = []
    per_state = {}
    with open(path, encoding="utf-8") as handle:
        for line in handle:
            if not line.strip():
                continue
            d = json.loads(line)
            state = d.get("state")
            value = d.get("issued")
            if state not in per_state:
                per_state[state] = []
                order.append(state)
            rows = per_state[state]
            if value and (not rows or rows[-1][1] != value):
                rows.append((d["t"], value))

    for state in order:
        rows = per_state[state]
        if not rows and state not in INTERESTING:
            continue
        print(f"=== {state}")
        if not rows:
            print("    (no command issued)")
        for when, value in rows:
            print(f"    t={when:9.1f}  {value}")
        print()


main()
