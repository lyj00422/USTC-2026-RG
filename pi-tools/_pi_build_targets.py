"""What did BUILD_AREA aim at, and where did it end up?

Read-only.  Prints one line per BUILD_AREA visit: the latched `target_cm`
(build_right_step_cm * (skip + 1)), how far right the car actually got, and
whether it committed.  This is the field check for the 2026-10-01 odometry rule
-- "1st structure -> 5 cm, 2nd -> 10 cm".
"""
import json
import os
import sys

ROOT = "/home/pi/robogame-runtime/logs"
name = sys.argv[1] if len(sys.argv) > 1 else None
if name is None:
    cands = [f for f in os.listdir(ROOT) if f.startswith("route_v2_full_")]
    name = max(cands, key=lambda f: os.path.getmtime(os.path.join(ROOT, f)))
path = os.path.join(ROOT, name)
print(f"log {name}  ({os.path.getsize(path)} bytes)\n")

visits = []
cur = None
for line in open(path, encoding="utf-8", errors="replace"):
    if not line.strip():
        continue
    try:
        row = json.loads(line)
    except Exception:
        continue
    if row.get("state") != "BUILD_AREA":
        if cur is not None:
            visits.append(cur)
            cur = None
        continue
    loop = row.get("build_loop") or row.get("loop") or {}
    src = loop if isinstance(loop, dict) and loop else row
    if cur is None:
        cur = {"t0": row.get("t"), "step": src.get("step_cm"),
               "target": None, "max_lat": None, "issued": set(),
               "b": src.get("building_count"), "c": src.get("cap_count"),
               "plan": src.get("plan")}
    if src.get("target_cm") is not None:
        cur["target"] = src["target_cm"]
    lat = row.get("lateral_cm")
    if isinstance(lat, (int, float)):
        cur["max_lat"] = lat if cur["max_lat"] is None else max(cur["max_lat"], lat)
    if row.get("issued"):
        cur["issued"].add(str(row["issued"])[:12])
    cur["t1"] = row.get("t")

if cur is not None:
    visits.append(cur)

print(f"BUILD_AREA 访问 {len(visits)} 次")
for i, v in enumerate(visits, 1):
    dur = (v.get("t1") or 0) - (v.get("t0") or 0)
    print(f"  第{i}次: 进区时 b={v['b']} c={v['c']} plan={v['plan']}  "
          f"step_cm={v['step']}  target_cm={v['target']}  "
          f"持续 {dur:.1f}s  发出过: {sorted(v['issued'])}")
