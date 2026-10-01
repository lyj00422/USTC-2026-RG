"""Where does the time actually go inside one action package?

Read-only.  Walks a run's telemetry, finds the window where an action package is
executing, and prints every step transition with the wall-clock gap since the
previous one.  A step that takes ~10 s is a `D` that never reported DONE; a step
that takes exactly the configured settle is a deliberate hold.

    python _pi_run_file.py pi-tools\\_pi_action_timing.py /home/pi/robogame-runtime \
        -- route_v2_full_20261001_234216.jsonl
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

# What does the action metadata look like?
seen_shape = None
rows = []
for line in open(path, encoding="utf-8", errors="replace"):
    if not line.strip():
        continue
    try:
        r = json.loads(line)
    except Exception:
        continue
    meta = r.get("pickup_action")
    if isinstance(meta, dict) and meta:
        if seen_shape is None:
            seen_shape = sorted(meta)
        rows.append((r.get("t"), r.get("state"), meta))

print("pickup_action 元数据的键:", seen_shape, "\n")
if not rows:
    print("这份日志里没有动作包执行记录")
    raise SystemExit(0)

# Group consecutive rows that carry the same (action_ref, step_index/step kind)
prev_key = None
last_t = None          # time of the previous TRANSITION, not of the previous row
print(f"{'t':>10s} {'dt':>7s}  state                     step  action")
for t, state, meta in rows:
    key = (meta.get("selected_action"), meta.get("step_index"))
    if key == prev_key:
        continue
    dt = "" if last_t is None else f"{t - last_t:7.2f}"
    print(f"{t:10.2f} {dt:>7s}  {str(state):24s}  {str(key[1]):>4s}  {key[0]}")
    prev_key, last_t = key, t
