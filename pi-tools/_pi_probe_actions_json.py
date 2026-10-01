"""Where does a given servo value live in the console's recording index?

`data/actions.json` is the ActionRecorder's append-only history: every package
the operator ever saved through the console lands here.  It is NOT what the
route reads -- the route goes through `data/route_v2_actions/catalog.json` --
so a value that only appears here is history, not behaviour.

Run ON the Pi via _pi_run_file.py:

    python _pi_run_file.py pi-tools\\_pi_probe_actions_json.py /home/pi/robogame-runtime 120 -- 1520
"""
import json
import sys

ROOT = "/home/pi/robogame-runtime"
needle = int(sys.argv[1]) if len(sys.argv) > 1 else 1520

rows = json.load(open(f"{ROOT}/data/actions.json", encoding="utf-8"))
print(f"{len(rows)} 条录制记录\n")

hits = []
for rec in rows:
    for s in rec.get("steps", []):
        if s.get("kind") == "SERVO" and s.get("position") == needle:
            hits.append((rec.get("name"), rec.get("started_at"), s.get("id")))
            break

print(f"含 id 位置 == {needle} 的录制：{len(hits)} 条")
for name, started, sid in hits:
    print(f"  {str(started)[:19]:22s} servo id={sid}  {name}")

# The comparison that matters: what the ACTIVE packages say for the same axis.
import glob
import os

print(f"\n活动包 (route_v2_actions) 里各轴 {needle} 的出现次数：")
tally = {}
for path in glob.glob(f"{ROOT}/data/route_v2_actions/*/action.json"):
    doc = json.load(open(path, encoding="utf-8"))
    n = sum(1 for s in doc["steps"]
            if s.get("kind") == "SERVO" and s.get("position") == needle)
    if n:
        tally[os.path.basename(os.path.dirname(path))] = n
print("  ", tally or "0 次 —— 活动包里根本没有这个值")
