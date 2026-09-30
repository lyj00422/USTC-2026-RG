"""The last N telemetry rows of the newest run: state, mask, what was issued.

    _pi_now.py [n]

Answers "what is it doing right now and what does the bar read" without guessing
from the state name.
"""
import glob
import json
import os
import sys

count = int(sys.argv[1]) if len(sys.argv) > 1 else 18
hits = [p for p in glob.glob("logs/route_v2_*.jsonl") if not p.endswith(".out")]
path = max(hits, key=os.path.getmtime)
print(f"log: {os.path.basename(path)}\n")

rows = []
for line in open(path, encoding="utf-8"):
    if not line.strip():
        continue
    try:
        rows.append(json.loads(line))
    except ValueError:
        pass

prev = None
for d in rows[-count:]:
    key = (d.get("state"), d.get("issued"), d.get("mask"))
    mark = "" if key != prev else ""
    prev = key
    print(f"  t={d['t']:9.2f} {str(d.get('state'))[:26]:26} "
          f"issued={str(d.get('issued')):<20} mask={d.get('mask')!s:>4} "
          f"lat={d.get('lateral_cm')!s:>8} travel={d.get('travel_cm')!s:>8} "
          f"intent={d.get('intent')}{mark}")
