"""The measured speed profile of the purple pickup, tick by tick.

    _pi_pickup_speed.py

`actual_speed` is what the chassis REPORTED (SPD), not what was commanded, so
this shows the motion the operator actually sees.  One line per change of
(command, measured speed), which is enough to tell "two moves at two speeds"
from "one move that ramps".
"""
import glob
import json
import os

STATE = "PICKUP_VISION_ONLY"
hits = [p for p in glob.glob("logs/route_v2_*.jsonl") if not p.endswith(".out")]
path = max(hits, key=os.path.getmtime)
print(f"log: {os.path.basename(path)}\n")

rows = []
for line in open(path, encoding="utf-8"):
    if not line.strip():
        continue
    d = json.loads(line)
    if d.get("state") == STATE:
        rows.append(d)

groups = []
for d in rows:
    if groups and d["t"] - groups[-1][-1]["t"] < 0.6:
        groups[-1].append(d)
    else:
        groups.append([d])

for i, group in enumerate(groups, 1):
    print(f"=== {STATE} visit {i}  t={group[0]['t']:.1f}..{group[-1]['t']:.1f} "
          f"({group[-1]['t'] - group[0]['t']:.1f} s)")
    key = None
    start = None
    for d in group:
        k = (d.get("issued"), d.get("actual_speed"), d.get("pickup_phase"))
        if k == key:
            continue
        if key is not None:
            print(f"        ...held {d['t'] - start:5.2f} s")
        key = k
        start = d["t"]
        print(f"  t={d['t']:9.2f}  issued={str(d.get('issued')):<18} "
              f"measured_speed={d.get('actual_speed')!s:>6}  "
              f"phase={d.get('pickup_phase')}  travel={d.get('travel_cm')}")
    print()
