"""BUILD_AREA's vision diagnostics, one line per change.

    _pi_build_vision.py

Answers the only question that matters when the car slides to the ceiling:
WHICH of the four slide conditions was true -- `blobs_passed < skip`,
`not visible`, `center_error is None`, or `clipped`.

`build_vision` is written by run_route_v2's BUILD_OCCUPANCY branch.  Rows are
collapsed to the ones where the decision tuple changes, so a 74 s slide prints as
a handful of lines with `lat` showing how far it went between them.
"""
import glob
import json
import os

hits = [p for p in glob.glob("logs/route_v2_*.jsonl") if not p.endswith(".out")]
path = max(hits, key=os.path.getmtime)
print(f"log: {os.path.basename(path)}\n")

rows = []
for line in open(path, encoding="utf-8"):
    if not line.strip():
        continue
    d = json.loads(line)
    if d.get("state") != "BUILD_AREA":
        continue
    rows.append(d)

if not rows:
    raise SystemExit("no BUILD_AREA rows in this run")

groups = []
for d in rows:
    if groups and d["t"] - groups[-1][-1]["t"] < 0.6:
        groups[-1].append(d)
    else:
        groups.append([d])

for i, group in enumerate(groups, 1):
    print(f"=== BUILD_AREA visit {i}  t={group[0]['t']:.1f}..{group[-1]['t']:.1f} "
          f"({group[-1]['t'] - group[0]['t']:.1f} s)")
    key = None
    for d in group:
        bv = d.get("build_vision") or {}
        loop = d.get("loop") or {}
        k = (loop.get("blobs_passed"), bv.get("visible"), bv.get("clipped"),
             loop.get("find_frames"),
             None if bv.get("center_error") is None else round(bv["center_error"], 2))
        if k == key:
            continue
        key = k
        lat = d.get("lateral_cm")
        print(f"  t={d['t']:8.2f} lat={lat!s:>9} passed={loop.get('blobs_passed')} "
              f"skip={loop.get('cap_count')}/{loop.get('building_count')} "
              f"vis={bv.get('visible')} clip={bv.get('clipped')} "
              f"fill={bv.get('roi_fill')} find={loop.get('find_frames')} "
              f"acc={bv.get('n_accepted')} rej={bv.get('n_rejected')} "
              f"err={bv.get('center_error')} box={bv.get('box')} "
              f"exh={loop.get('slide_exhausted')}/{loop.get('cap_seek_exhausted')} "
              f"issued={d.get('issued')}")
    print()
