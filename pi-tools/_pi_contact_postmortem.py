"""READ-ONLY post-mortem of the last run's telemetry: why did the wall never fire?

Reports the state sequence, the travel_cm / encoder_delta / wall_contact history
on the area-approach legs, and the exact commands issued in the final ticks.
Sends nothing to the chassis.
"""

import json
import glob
import os
from pathlib import Path

LOGS = Path("/home/pi/robogame-runtime/logs")


def main() -> None:
    path = LOGS / "route_v2_telemetry.jsonl"
    rows = []
    for line in path.read_text(errors="replace").splitlines():
        line = line.strip()
        if line:
            try:
                rows.append(json.loads(line))
            except json.JSONDecodeError:
                pass
    print("telemetry:", path.name, "rows:", len(rows))
    if not rows:
        return
    print("t: %.1f -> %.1f (%.1fs)" % (rows[0]["t"], rows[-1]["t"], rows[-1]["t"] - rows[0]["t"]))

    seq = []
    for r in rows:
        if not seq or seq[-1][0] != r["state"]:
            seq.append([r["state"], 1, r["intent"]])
        else:
            seq[-1][1] += 1
            seq[-1][2] = r["intent"]
    print("\nall states, in order (%d):" % len(seq))
    for s, n, i in seq:
        print("   %-36s %6d ticks  last intent=%s" % (s, n, i))

    wc = [r for r in rows if r.get("wall_contact")]
    print("\nwall_contact True ticks: %d" % len(wc))
    if wc:
        print("   first at t=%.1f state=%s" % (wc[0]["t"], wc[0]["state"]))
        print("   last  at t=%.1f state=%s" % (wc[-1]["t"], wc[-1]["state"]))

    print("\n--- tail: last 60 ticks ---")
    print("%8s %-32s %-7s %-6s %9s %9s %7s %s" %
          ("t", "state", "intent", "wall", "travel_cm", "enc_delta", "speed", "issued"))
    for r in rows[-60:]:
        tr = r.get("travel_cm")
        ed = r.get("encoder_delta")
        print("%8.1f %-32s %-7s %-6s %9s %9s %7s %s" % (
            r["t"], r["state"], r["intent"], r.get("wall_contact"),
            ("%.1f" % tr) if tr is not None else "None",
            ("%.1f" % ed) if ed is not None else "None",
            "%.0f" % r.get("actual_speed", -1) if r.get("actual_speed") is not None else "None",
            r.get("issued")))

    print("\n--- .out verdicts (newest 3) ---")
    outs = sorted(glob.glob(str(LOGS / "full_*.out")), key=os.path.getmtime)[-3:]
    for o in outs:
        text = Path(o).read_text(errors="replace").strip()
        print(" ", os.path.basename(o), "->", text.splitlines()[-1] if text else "(empty)")


if __name__ == "__main__":
    main()
