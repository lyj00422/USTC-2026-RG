"""Every command the route actually DISPATCHED, in order, for the newest run.

Run ON the Pi via _pi_run_file.py.

The telemetry writes `issued` only on the tick a command goes out, so filtering
to the rows where it is non-None gives the exact chassis/arm command sequence.
That is the difference between "the route decided to turn and the car ignored it"
and "the route never got as far as sending anything", which look identical on the
field and identical in a state-only view.

Also reports the gaps: a long silence between two commands is the startup window,
or a step waiting on the firmware.
"""
import glob
import json
import os

ROOT = "/home/pi/robogame-runtime"
paths = glob.glob(f"{ROOT}/logs/route_v2_*.jsonl")
if not paths:
    print("NO_TELEMETRY")
    raise SystemExit(1)
path = max(paths, key=os.path.getmtime)

with open(path, encoding="utf-8", errors="replace") as handle:
    rows = [json.loads(ln) for ln in handle if ln.strip()]

print(f"log {os.path.basename(path)}")
first, last = rows[0].get("t", 0), rows[-1].get("t", 0)
print(f"rows={len(rows)}  t={first}..{last}  ({last - first:.1f}s)\n")

print(f"--- first 5 rows (startup) ---")
for row in rows[:5]:
    print(f"  t={row.get('t'):>9}  state={row.get('state'):<26} "
          f"intent={row.get('intent')}  issued={row.get('issued')}  "
          f"mask={row.get('mask')}  line_error={row.get('line_error')}")

print(f"\n--- every dispatched command ---")
previous = None
for row in rows:
    issued = row.get("issued")
    if not issued or issued == previous:
        continue
    print(f"  t={row.get('t'):>9}  {row.get('state'):<26} {issued}")
    previous = issued

sent = [r for r in rows if r.get("issued")]
print(f"\n{len(sent)} command ticks out of {len(rows)} rows")
if sent:
    print(f"first command at t={sent[0].get('t')}, last at t={sent[-1].get('t')}")
