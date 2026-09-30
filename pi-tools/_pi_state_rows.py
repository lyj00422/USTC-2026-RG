"""Dump every visit of one state, compactly, one line per change.

    _pi_state_rows.py <state> [run.jsonl]

Rows are collapsed to the ones where (pickup_phase, issued, mask, vision.task,
vision.ready) actually change, so a state that ran for minutes prints as a dozen
lines.  `pickup_phase` is the PickupMovement's own phase name: SEEK_OUT /
SEEK_BACK / ALIGN / DONE / FAULT are the search's real progress, which the car's
motion alone does not reveal.
"""
import glob
import json
import os
import sys

state = sys.argv[1] if len(sys.argv) > 1 else "JUNCTION_3_TURN_LEFT"
path = sys.argv[2] if len(sys.argv) > 2 else None
if path is None:
    hits = [p for p in glob.glob("logs/route_v2_*.jsonl") if not p.endswith(".out")]
    path = max(hits, key=os.path.getmtime)

print(f"log: {os.path.basename(path)}  state={state}")

visits = []
for line in open(path, encoding="utf-8"):
    if not line.strip():
        continue
    d = json.loads(line)
    if d.get("state") != state:
        continue
    visits.append(d)

if not visits:
    raise SystemExit("state not present in this run")

groups = []
for d in visits:
    if groups and d["t"] - groups[-1][-1]["t"] < 0.6:
        groups[-1].append(d)
    else:
        groups.append([d])

for i, group in enumerate(groups, 1):
    first, last = group[0], group[-1]
    phases = []
    for d in group:
        p = d.get("pickup_phase")
        if not phases or phases[-1] != p:
            phases.append(p)
    print(f"\n=== visit {i}  t={first['t']:.2f}..{last['t']:.2f} "
          f"({last['t'] - first['t']:.1f} s)  travel={last.get('travel_cm')} "
          f"lat={last.get('lateral_cm')}")
    print(f"    phases: {' -> '.join(str(p) for p in phases)}")
    key = None
    for d in group:
        v = d.get("vision") or {}
        k = (d.get("pickup_phase"), d.get("issued"), d.get("mask"),
             v.get("task"), v.get("ready"), v.get("fault_reason"))
        if k == key:
            continue
        key = k
        print(f"    t={d['t']:9.2f} mask={d.get('mask')!s:>4} "
              f"travel={d.get('travel_cm')!s:>8} lat={d.get('lateral_cm')!s:>8} "
              f"phase={d.get('pickup_phase')} intent={d.get('intent')} "
              f"issued={d.get('issued')} task={v.get('task')} "
              f"ready={v.get('ready')} reason={v.get('fault_reason')}")
