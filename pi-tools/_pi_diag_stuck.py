"""Summarise one route telemetry file: what was issued, in which state, and when.

Read-only.  Run on the Pi via _pi_run_file.py:
    python _pi_run_file.py _pi_diag_stuck.py /home/pi/robogame-runtime <telemetry.jsonl>
"""
import json
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(sys.argv[1]) if len(sys.argv) > 1 else Path("/home/pi/robogame-runtime")
name = sys.argv[2] if len(sys.argv) > 2 else None
if name is None:
    logs = sorted((ROOT / "logs").glob("route_v2_*.jsonl"),
                  key=lambda p: p.stat().st_mtime)
    path = logs[-1]
else:
    path = Path(name) if name.startswith("/") else ROOT / "logs" / name

rows = []
for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
    line = line.strip()
    if not line:
        continue
    try:
        rows.append(json.loads(line))
    except ValueError:
        pass

print("file   : %s" % path)
print("rows   : %d" % len(rows))
if not rows:
    raise SystemExit(0)
print("t first/last: %.1f -> %.1f  (span %.1f s)"
      % (rows[0].get("t", 0), rows[-1].get("t", 0),
         (rows[-1].get("t", 0) - rows[0].get("t", 0)) / 1000.0))

print("\n=== rows per state ===")
for state, count in Counter(r.get("state") for r in rows).most_common():
    print("  %-32s %5d" % (state, count))

print("\n=== issued per state ===")
pairs = Counter((r.get("state"), r.get("issued")) for r in rows)
for (state, issued), count in sorted(pairs.items(), key=lambda kv: str(kv[0])):
    print("  %-32s issued=%-8s %5d" % (state, issued, count))

print("\n=== last 12 rows ===")
for r in rows[-12:]:
    print("  t=%9.1f %-30s intent=%-6s issued=%-8s mask=%-4s enc=%s "
          "travel=%.1f lat=%.1f spd=%.1f suct=%s"
          % (r.get("t", 0), r.get("state"), r.get("intent"), r.get("issued"),
             r.get("mask"), r.get("encoder"), r.get("travel_cm", 0),
             r.get("lateral_cm", 0), r.get("actual_speed", 0),
             r.get("suction_latch")))

print("\n=== transitions ===")
prev = None
for r in rows:
    if r.get("state") != prev:
        prev = r.get("state")
        print("  t=%9.1f -> %s" % (r.get("t", 0), prev))

# Non-stop intents are the motion the route actually asked for.
print("\n=== every non-stop intent ===")
shown = 0
for r in rows:
    if r.get("intent") not in (None, "stop"):
        shown += 1
        if shown <= 40:
            print("  t=%9.1f %-30s intent=%-8s issued=%s"
                  % (r.get("t", 0), r.get("state"), r.get("intent"),
                     r.get("issued")))
print("  (total %d)" % shown)

keys = set()
for r in rows:
    keys.update(r)
print("\nfields: %s" % ", ".join(sorted(keys)))
