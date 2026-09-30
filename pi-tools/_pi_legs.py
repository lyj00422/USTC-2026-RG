"""How far each leg ACTUALLY travelled, per state, in the newest run.

Run ON the Pi via _pi_run_file.py.

`travel_cm` and `lateral_cm` are re-baselined at every state change, so the last
row of a state is that state's own displacement -- which is how an odometer gate
is checked against its configured value.  A gate of 320 that stops at 329 means
the gate is not what stopped the car (the wall contact or the timeout was).
"""
import glob
import json
import os

TAG = "logs/route_v2_*.jsonl"


def main() -> None:
    hits = [p for p in glob.glob(TAG) if not p.endswith(".out")]
    path = max(hits, key=os.path.getmtime)
    print(f"log: {os.path.basename(path)}\n")

    order = []
    visits = {}
    with open(path, encoding="utf-8") as handle:
        for line in handle:
            if not line.strip():
                continue
            d = json.loads(line)
            state = d.get("state")
            # A visit ends when the state changes; the loop repeats states, so
            # keep one entry per VISIT rather than per state.
            if not order or order[-1][0] != state:
                order.append([state, d["t"], d.get("travel_cm"),
                              d.get("lateral_cm"), d.get("wall_contact")])
            else:
                order[-1][1] = d["t"]
                order[-1][2] = d.get("travel_cm")
                order[-1][3] = d.get("lateral_cm")
                order[-1][4] = order[-1][4] or d.get("wall_contact")

    for state, end_t, travel, lateral, wall in order:
        if travel is None and lateral is None:
            continue
        mark = "  <== WALL" if wall else ""
        print(f"  {state:<28} travel={travel!s:>9}  lateral={lateral!s:>9}{mark}")


main()
