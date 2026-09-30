"""One compact line describing the newest route run's LAST telemetry row.

Run ON the Pi (via _pi_run_file.py, which cannot pass arguments, so the log is
found by globbing the run tag).  Designed for a polling watcher: pipe-delimited
so the caller can dedupe on the state + action step without parsing JSON in
PowerShell.

    STATE|t=..|issued=..|mask=..|speed=..|travel=..|lat=..|suction=..|dto=..|wall=..|act=ref:step/count
"""
import glob
import json
import os

TAG = "logs/route_v2_*.jsonl"


def main() -> None:
    hits = [p for p in glob.glob(TAG) if not p.endswith(".out")]
    if not hits:
        print("NO_LOG")
        return
    path = max(hits, key=os.path.getmtime)
    last = ""
    with open(path, encoding="utf-8") as handle:
        for line in handle:
            if line.strip():
                last = line.strip()
    if not last:
        print("EMPTY")
        return
    d = json.loads(last)
    a = d.get("pickup_action") or {}
    vision = d.get("vision") or {}
    fields = [
        str(d.get("state")),
        "t=%.1f" % d.get("t", 0.0),
        "issued=%s" % d.get("issued"),
        "mask=%s" % d.get("mask"),
        "speed=%s" % d.get("actual_speed"),
        "travel=%s" % d.get("travel_cm"),
        "lat=%s" % d.get("lateral_cm"),
        "suction=%s" % d.get("suction_latch"),
        "dto=%s" % d.get("distance_timeouts"),
        "wall=%s" % d.get("wall_contact"),
        "vtask=%s" % vision.get("task"),
        "act=%s:%s/%s" % (a.get("action_ref"), a.get("step_index"), a.get("step_count")),
    ]
    print("|".join(fields))


main()
