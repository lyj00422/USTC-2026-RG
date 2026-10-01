"""Where the route is right now: processes, the newest .out, and the last states.

Run ON the Pi via _pi_run_file.py.  Read-only.

Matches BOTH process names on purpose.  `start_full_route.py` runs its readiness
gates for a while before it spawns anything, and it retries a run that died in the
startup window -- so a check that only looks for `run_route_v2` reports "nothing
running" during the gates and during a retry gap, which is exactly when somebody
asks.  The launcher's own gate 2 uses the same idea.

Reading the tail of the run: `state` is where the machine is, `pickup_phase` and
`FAULT_SAFE` are what tell "waiting" from "gave up".  A silent STOP hold looks
identical to a working car on the field, so those two columns are the answer to
"why is it not moving".
"""
import glob
import json
import os
import subprocess

ROOT = "/home/pi/robogame-runtime"


def shell(cmd):
    return subprocess.run(cmd, capture_output=True, text=True).stdout.strip()


print("--- processes ---")
found = []
for pattern in ("start_full_route", "run_route_v2"):
    out = shell(["pgrep", "-af", pattern])
    for line in out.splitlines():
        if line not in found:
            found.append(line)
print("\n".join(found) if found else "(nothing running)")

outs = [p for p in glob.glob(f"{ROOT}/logs/full_*.out") if not p.endswith(".jsonl")]
if not outs:
    print("--- no full_*.out found ---")
    raise SystemExit(0)

out_path = max(outs, key=os.path.getmtime)
print(f"\n--- newest .out: {os.path.basename(out_path)}"
      f"  ({os.path.getsize(out_path)} bytes) ---")
with open(out_path, encoding="utf-8", errors="replace") as handle:
    lines = [ln.rstrip() for ln in handle if ln.strip()]
for line in lines[-12:]:
    print("  " + line[:170])
if not lines:
    print("  (empty -- the run has not printed anything yet)")

tele = sorted(glob.glob(f"{ROOT}/logs/route_v2_*.jsonl"),
              key=os.path.getmtime)
if tele:
    path = tele[-1]
    age = os.path.getmtime(path)
    import time
    print(f"\n--- telemetry {os.path.basename(path)}  "
          f"({os.path.getsize(path)} bytes, {time.time() - age:.1f}s old) ---")
    with open(path, encoding="utf-8", errors="replace") as handle:
        rows = [json.loads(ln) for ln in handle if ln.strip()]
    if not rows:
        print("  (no rows yet)")
        raise SystemExit(0)
    last = rows[-1]
    print(f"  t={last.get('t')}  state={last.get('state')}  "
          f"issued={last.get('issued')}")
    print(f"  travel_cm={last.get('travel_cm')}  lat={last.get('lateral_cm')}  "
          f"abs_lat={last.get('absolute_lateral_cm')}  mask={last.get('sensor_mask')}")
    for key in ("pickup_phase", "pickup_reason", "loop"):
        if key in last:
            print(f"  {key}={json.dumps(last[key], ensure_ascii=False)[:200]}")

    # The state timeline is what a reader actually wants: when it changed and how
    # long each one lasted.
    print("\n--- state timeline (changed rows only) ---")
    previous = None
    start_t = None
    for row in rows:
        state = row.get("state")
        if state == previous:
            continue
        if previous is not None:
            print(f"  {start_t:>8}  {previous:<26} "
                  f"({row.get('t', 0) - start_t:.1f}s)")
        previous, start_t = state, row.get("t", 0)
    print(f"  {start_t:>8}  {previous:<26} (still running)")
