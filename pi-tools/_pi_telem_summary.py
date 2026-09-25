"""Summarise a route-v2 telemetry JSONL run."""

import collections
import json
import sys

path = sys.argv[1] if len(sys.argv) > 1 else "/home/pi/robogame-runtime/logs/line_test.jsonl"
rows = [json.loads(line) for line in open(path, encoding="utf-8") if line.strip()]
print(f"file: {path}")
print(f"ticks: {len(rows)}")
if not rows:
    raise SystemExit(0)

t0, t1 = rows[0]["t"], rows[-1]["t"]
span = t1 - t0
print(f"duration: {span:.2f}s   mean tick: {span / max(1, len(rows) - 1) * 1000:.0f} ms")

print("states:", dict(collections.Counter(r["state"] for r in rows)))
print("top masks:", [(f"0x{m:02X}", c) for m, c in collections.Counter(r["mask"] for r in rows).most_common(8)])

errs = [r["line_error"] for r in rows if r["line_error"] is not None]
if errs:
    print(f"line_error: n={len(errs)} min={min(errs):+.3f} max={max(errs):+.3f} "
          f"mean={sum(errs) / len(errs):+.3f}")
else:
    print("line_error: never available")

print("issued kinds:", dict(collections.Counter((r["issued"] or "none").split()[0] for r in rows)))

vcmds = [r["issued"] for r in rows if r["issued"] and r["issued"].startswith("V ")]
if vcmds:
    vys = [int(c.split()[2]) for c in vcmds]
    wzs = [int(c.split()[3]) for c in vcmds]
    print(f"V commands: {len(vcmds)}   vy range [{min(vys)},{max(vys)}]   wz range [{min(wzs)},{max(wzs)}]")
    print("  first 5:", vcmds[:5])
    print("  last 5 :", vcmds[-5:])
print("lost-line ticks:", sum(1 for r in rows if r["intent"] == "v" and r["line_error"] is None))
print("STOP ticks:", [f"{r['t'] - t0:.2f}s" for r in rows if r["issued"] == "STOP"][:8])
print("D ticks:", [(f"{r['t'] - t0:.2f}s", r["issued"]) for r in rows if r["intent"] == "d"][:5])

transitions = [
    (rows[i]["t"] - t0, rows[i - 1]["state"], rows[i]["state"])
    for i in range(1, len(rows))
    if rows[i]["state"] != rows[i - 1]["state"]
]
print("state transitions:", [(f"{t:.2f}s", a, b) for t, a, b in transitions][:8])
print("actual_speed samples:", sorted({r["actual_speed"] for r in rows})[:6])
