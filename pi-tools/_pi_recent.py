"""The last N telemetry rows, compactly -- what the route is doing right now.

Run ON the Pi via _pi_run_file.py:

    python pi-tools\\_pi_run_file.py pi-tools\\_pi_recent.py /home/pi/robogame-runtime 120 -- 25

Distinct from _pi_run_status.py: that one summarises a whole run (timeline,
gate status).  This is the live view -- the per-tick columns that say whether a
state is MOVING, WAITING for the firmware, or holding.

The columns that answer "why is it not moving":
  issued   the command dispatched this tick.  `None` while a `d`/`D` is in flight
           is normal -- those are issued ONCE and then awaited, so a long run of
           `issued=None` in a turn state means the route is waiting on the
           firmware, not that it has stopped.
  d_done   the firmware's DONE latch.
  state / intent  where it is and what it asked for.
"""
import glob
import json
import os
import sys

ROOT = "/home/pi/robogame-runtime"
count = int(sys.argv[1]) if len(sys.argv) > 1 else 25

paths = [p for p in glob.glob(f"{ROOT}/logs/route_v2_*.jsonl")]
if not paths:
    print("NO_TELEMETRY")
    raise SystemExit(1)
path = max(paths, key=os.path.getmtime)
print(f"log {os.path.basename(path)}  ({os.path.getsize(path)} bytes)\n")

with open(path, encoding="utf-8", errors="replace") as handle:
    rows = [json.loads(ln) for ln in handle if ln.strip()]

print(f"rows={len(rows)}  first t={rows[0].get('t')}  last t={rows[-1].get('t')}")
print(f"elapsed {rows[-1].get('t', 0) - rows[0].get('t', 0):.1f}s\n")

# actual_speed / encoder_delta are the ones that say whether the WHEELS are
# moving -- `issued` alone cannot: the runner dispatches a `d` once and then
# reports None for every following tick while it waits on the firmware's DONE,
# so a turn in flight and a turn that never left both look quiet there.
keys = ("t", "state", "issued", "intent", "actual_speed", "encoder_delta",
        "wz", "travel_cm", "lateral_cm", "sensor_mask", "line_error")
present = [k for k in keys if any(k in r for r in rows[-count:])]
print("  ".join(f"{k}" for k in present))
for row in rows[-count:]:
    cells = []
    for k in present:
        v = row.get(k)
        if isinstance(v, float):
            v = f"{v:.2f}"
        cells.append("" if v is None else str(v)[:22])
    print("  ".join(cells))
