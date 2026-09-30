"""Count every distinct `D` command issued in the newest run.

Reads the `issued` field of each telemetry row, so it shows what actually went
out on the wire rather than what the package file says should go out -- the two
differ whenever a deploy did not land.
"""
import glob
import json
import os
from collections import Counter

hits = [p for p in glob.glob("logs/route_v2_*.jsonl") if not p.endswith(".out")]
path = max(hits, key=os.path.getmtime)
print(f"log: {os.path.basename(path)}")

counts = Counter()
first_t = {}
for line in open(path, encoding="utf-8"):
    if not line.strip():
        continue
    d = json.loads(line)
    issued = d.get("issued")
    # Package moves are logged with an `ACTION ` prefix ("ACTION D 7 0 0 10");
    # route-level ones are bare ("D 0 0 90 80").  Matching only a leading "D "
    # silently hides every command an action package issued.
    if not issued:
        continue
    body = issued.split(" ", 1)[1] if issued.startswith("ACTION ") else issued
    if not body.startswith("D "):
        continue
    counts[issued] += 1
    first_t.setdefault(issued, d.get("t"))

for cmd, n in sorted(counts.items(), key=lambda kv: first_t[kv[0]]):
    print(f"  {cmd:<18} x{n:<4} first t={first_t[cmd]:.1f}")
