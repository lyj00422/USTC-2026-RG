"""Print one raw BUILD_AREA telemetry row, whole, to see what IS recorded."""
import glob
import json
import os

hits = [p for p in glob.glob("logs/route_v2_*.jsonl") if not p.endswith(".out")]
path = max(hits, key=os.path.getmtime)
print(f"log: {os.path.basename(path)}\n")

shown = 0
for line in open(path, encoding="utf-8"):
    if not line.strip():
        continue
    d = json.loads(line)
    if d.get("state") != "BUILD_AREA":
        continue
    if shown == 0:
        print("--- all top-level keys:")
        print(sorted(d.keys()))
        print()
    print(f"--- row t={d['t']:.2f}")
    print("vision =", json.dumps(d.get("vision"), ensure_ascii=False, indent=1))
    print("loop   =", json.dumps(d.get("loop"), ensure_ascii=False))
    shown += 1
    if shown >= 2:
        break
