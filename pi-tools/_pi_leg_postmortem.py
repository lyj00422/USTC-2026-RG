"""READ-ONLY post-mortem of one telemetry file, focused on the build-area leg.

Answers the question the 2026-09-24 change was made to answer: does
JUNCTION_PICKUP_3_TO_AREA now END on its distance gate (about 330 cm), instead
of grinding into the wall?

Usage:
    python _pi_leg_postmortem.py [telemetry_filename]      (default: the live one)
"""

import json
import os
import sys
from pathlib import Path

LOGS = Path("/home/pi/robogame-runtime/logs")
LEG = "JUNCTION_PICKUP_3_TO_AREA"


def main() -> int:
    name = sys.argv[1] if len(sys.argv) > 1 else "route_v2_telemetry.jsonl"
    path = LOGS / name
    if not path.exists():
        print("missing:", path)
        return 1

    rows = []
    for line in path.read_text(errors="replace").splitlines():
        line = line.strip()
        if line:
            try:
                rows.append(json.loads(line))
            except json.JSONDecodeError:
                pass
    print("file:", name)
    print("rows:", len(rows), " size:", os.path.getsize(path))
    if not rows:
        return 1
    print("t: %.1f -> %.1f (%.1fs)" % (rows[0]["t"], rows[-1]["t"], rows[-1]["t"] - rows[0]["t"]))

    seq = []
    for r in rows:
        if not seq or seq[-1][0] != r["state"]:
            seq.append([r["state"], 1])
        else:
            seq[-1][1] += 1
    print("\nstates (%d):" % len(seq))
    for s, n in seq:
        print("   %-36s %6d" % (s, n))

    leg = [r for r in rows if r["state"] == LEG]
    print("\n--- %s ---" % LEG)
    print("ticks on this leg:", len(leg))
    if leg:
        tr = [r["travel_cm"] for r in leg if r["travel_cm"] is not None]
        if tr:
            print("travel_cm: start %.1f  end %.1f  max %.1f" % (tr[0], tr[-1], max(tr)))
        print("wall_contact true on this leg:",
              sum(1 for r in leg if r.get("wall_contact")))
        nxt = None
        for r in rows[rows.index(leg[-1]) + 1:]:
            nxt = r["state"]
            break
        print("last tick: issued=%s travel_cm=%s speed=%s -> next state: %s"
              % (leg[-1].get("issued"), leg[-1].get("travel_cm"),
                 leg[-1].get("actual_speed"), nxt))

    reached = [s for s, _ in seq if s in ("BUILD_AREA", "BUILD_ACTION",
                                          "BUILD_RETURN_REVERSE", "FINISHED")]
    print("\nbuild states visited:", reached or "NONE")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
