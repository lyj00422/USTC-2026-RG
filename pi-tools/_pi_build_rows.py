"""What the build-area occupancy detector actually reported, per BUILD_AREA visit.

    _pi_build_rows.py

Reads the `build_vision` object added to the telemetry on 2026-09-30.  Before
that these values existed only inside the tick, so a slide that ran past a
structure and capped on bare floor could not be told apart from a detector that
saw the structure and mis-centred it.

`rejected` names the geometry gate that threw each candidate away, which is the
thing that says WHICH gate is too strict -- not just that one is.
"""
import glob
import json
import os

hits = [p for p in glob.glob("logs/route_v2_*.jsonl") if not p.endswith(".out")]
path = max(hits, key=os.path.getmtime)
print(f"log: {os.path.basename(path)}")

rows = []
for line in open(path, encoding="utf-8"):
    if not line.strip():
        continue
    d = json.loads(line)
    if d.get("build_vision") is None:
        continue
    rows.append(d)

if not rows:
    raise SystemExit("\nno build_vision rows in this run (old code still deployed?)")

groups = []
for d in rows:
    if groups and d["t"] - groups[-1][-1]["t"] < 0.6:
        groups[-1].append(d)
    else:
        groups.append([d])

print(f"{len(rows)} rows, {len(groups)} BUILD_AREA visits\n")
for i, group in enumerate(groups, 1):
    first, last = group[0], group[-1]
    loop = last.get("loop") or {}
    print(f"=== visit {i}  t={first['t']:.1f}..{last['t']:.1f} "
          f"({last['t'] - first['t']:.1f} s)  lat_end={last.get('lateral_cm')}  "
          f"blobs_passed={loop.get('blobs_passed')}  plan={loop.get('plan')}")
    key = None
    for d in group:
        bv = d["build_vision"]
        k = (bv.get("visible"), bv.get("center_error"), tuple(bv.get("rejected") or ()))
        if k == key:
            continue
        key = k
        print(f"    t={d['t']:9.2f} lat={d.get('lateral_cm')!s:>8} "
              f"visible={bv.get('visible')!s:>5} accepted={bv.get('n_accepted')} "
              f"rejected={bv.get('n_rejected')} "
              f"center_err={bv.get('center_error')!s:>8} "
              f"center_px={bv.get('center_px')!s:>7} area={bv.get('area')!s:>8} "
              f"box={bv.get('box')} why={bv.get('rejected')}")
    print()
