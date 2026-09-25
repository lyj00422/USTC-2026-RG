"""Read-only: what the orange (PICKUP_2_VISION_ONLY) state actually saw.

Prints the per-tick vision task / result frame id / pickup decision for that
state, the pickup action sequence, and the --capture-orange evidence index if a
recorder wrote one.  Touches nothing.
"""

import glob
import json
import os

TELEM_GLOB = "/home/pi/robogame-runtime/logs/route_v2_full_orange_*.jsonl"
EVID_GLOB = "/home/pi/robogame-runtime/logs/orange-evidence/*/index.jsonl"
STATE = "PICKUP_2_VISION_ONLY"


def col(row, *path):
    node = row
    for key in path:
        if not isinstance(node, dict):
            return None
        node = node.get(key)
    return node


def main():
    path = sorted(glob.glob(TELEM_GLOB))[-1]
    print(f"telemetry : {path}")
    rows = []
    with open(path, encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if line:
                try:
                    rows.append(json.loads(line))
                except json.JSONDecodeError:
                    pass

    hits = [r for r in rows if r.get("state") == STATE]
    print(f"rows in {STATE}: {len(hits)}")
    if hits:
        t0 = hits[0]["t"]
        print("\n  t+     mask  vtask                vframe  phase        result")
        prev = object()
        for row in hits:
            mark = (col(row, "vision", "task"), col(row, "vision", "result_frame_id"),
                    row.get("pickup_phase"), row.get("pickup_result"))
            if mark == prev:
                continue
            prev = mark
            print(f"  {row['t'] - t0:6.2f} {row['mask']:>5}  "
                  f"{str(col(row, 'vision', 'task')):<20} "
                  f"{str(col(row, 'vision', 'result_frame_id')):<7} "
                  f"{str(row.get('pickup_phase')):<12} {row.get('pickup_result')}")

        print("\npickup_action steps:")
        seen = set()
        for row in hits:
            act = row.get("pickup_action")
            if isinstance(act, dict):
                key = (act.get("action_ref"), act.get("step_index"), act.get("result"))
                if key not in seen:
                    seen.add(key)
                    print(f"  t+{row['t'] - t0:6.2f}  {act}")

    print("\ncapture-orange evidence:")
    dirs = sorted(glob.glob(os.path.dirname(EVID_GLOB) + "/*"))
    if not dirs:
        print("  none")
    for d in dirs:
        index = os.path.join(d, "index.jsonl")
        print(f"  {d}")
        if os.path.exists(index):
            for line in open(index, encoding="utf-8"):
                print(f"      {line.strip()[:300]}")
        else:
            print(f"      files: {sorted(os.listdir(d))[:10]}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
