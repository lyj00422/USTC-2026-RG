"""The last rows of a state, raw: t, travel and the per-tick step.

Settles "did the gate fire at its own number": the row that fires a gate is
labelled with the NEXT state (the state changes inside the tick), so the gate's
own tick is invisible in the state's own rows and the last visible travel is
always one tick short.  The per-tick step says how many cm that tick is worth.
"""
import glob
import json
import os

STATE = "JUNCTION_PICKUP_3_TO_AREA"
COUNT = 8

hits = [p for p in glob.glob("logs/route_v2_*.jsonl") if not p.endswith(".out")]
path = max(hits, key=os.path.getmtime)
print(f"log: {os.path.basename(path)}  state={STATE}\n")

rows = []
for line in open(path, encoding="utf-8"):
    if not line.strip():
        continue
    d = json.loads(line)
    if d.get("state") != STATE:
        continue
    rows.append(d)

print(f"{len(rows)} rows total; last {COUNT}:\n")
prev_t = prev_travel = None
for d in rows[-COUNT:]:
    t = d["t"]
    travel = d.get("travel_cm")
    dt = "" if prev_t is None else f" dt={t - prev_t:6.3f}"
    step = ""
    if prev_travel is not None and travel is not None:
        step = f" d_travel={travel - prev_travel:6.2f}"
    print(f"  t={t:9.3f}{dt}  travel={travel!s:>9}{step}  "
          f"speed={d.get('actual_speed')!s:>6}  wall={d.get('wall_contact')!s:>5}  "
          f"enc={d.get('encoder')}  issued={d.get('issued')}")
    prev_t, prev_travel = t, travel

walls = [d for d in rows if d.get("wall_contact")]
print(f"\nrows with wall_contact=True: {len(walls)}")
for d in walls[:4]:
    print(f"  t={d['t']:.3f} travel={d.get('travel_cm')}")
print(f"max travel in this state: {max((d.get('travel_cm') or 0) for d in rows)}")
