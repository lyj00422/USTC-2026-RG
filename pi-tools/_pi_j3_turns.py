"""Every turn in every run still on the Pi, with the duty the chassis reported.

Run ON the Pi via _pi_run_file.py (no arguments: it globs logs/).

The question it answers: did ANY turn complete today, and what did the SPD reply
say while it was running.  A turn that advances in ~40 ticks is a DONE that
arrived; a turn that sits for hundreds of ticks and then holds is the wedge.  The
`OUT` column next to it is the duty the firmware commanded -- the same 80-speed
turn reads ~76-82 on a healthy battery and 99 when the pack is sagging.
"""
import glob
import json
import os
import re

TURNS = {
    "JUNCTION_2_TURN_LEFT", "JUNCTION_3_TURN_LEFT", "PICKUP_2_TURN_RIGHT",
    "PICKUP_3_TURN_RIGHT", "TAG_2_TURN_RIGHT", "BUILD_TURN_LEFT",
    "PICKUP_2_TURN_LEFT",
}

files = sorted(
    (p for p in glob.glob("logs/route_v2_*.jsonl") if not p.endswith(".out")),
    key=os.path.getmtime,
)[-14:]

for path in files:
    rows = []
    for line in open(path, errors="replace"):
        try:
            rows.append(json.loads(line))
        except Exception:
            pass
    if not rows:
        continue
    segs = []
    cur = None
    for r in rows:
        if cur is None or r["state"] != cur[0]:
            cur = [r["state"], 1, 0.0]
            segs.append(cur)
        else:
            cur[1] += 1
        cur[2] = max(cur[2], r.get("actual_speed") or 0.0)
    out = []
    for i, s in enumerate(segs):
        if s[0] in TURNS:
            nxt = segs[i + 1][0] if i + 1 < len(segs) else "EOF"
            tag = "WEDGE" if s[1] > 200 and nxt == "EOF" else ("HELD" if s[1] > 200 else "ok")
            out.append("    %-22s %4dt duty=%-5.0f -> %-24s %s"
                       % (s[0], s[1], s[2], nxt, tag))
    speeds = [r.get("actual_speed") or 0.0 for r in rows if r.get("actual_speed")]
    top = sorted(speeds)[-1] if speeds else 0.0
    print("%s rows=%-6d t=%.0f..%.0f  max_duty_overall=%.0f"
          % (os.path.basename(path), len(rows), rows[0]["t"], rows[-1]["t"], top))
    for line in out:
        print(line)
