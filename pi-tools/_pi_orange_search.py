"""Read-only: the orange pickup search timeline, phase by phase.

For PICKUP_2_VISION_ONLY prints every phase change with the lateral odometer,
the commanded vy, the line mask, and the vision frame id -- so a search that
ran out of distance can be told apart from one that ran out of time, and from
one whose direction was simply wrong.
"""

import glob
import json

TELEM_GLOB = "/home/pi/robogame-runtime/logs/route_v2_full_orange_*.jsonl"
STATE = "PICKUP_2_VISION_ONLY"


def main():
    path = sorted(glob.glob(TELEM_GLOB))[-1]
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
    if not hits:
        print(f"no rows in {STATE}")
        return 1
    t0 = hits[0]["t"]
    print(f"file  : {path}")
    print(f"rows  : {len(hits)}   span {hits[-1]['t'] - t0:.1f}s\n")
    print("  t+     phase                   lateral   travel     vy    mask  vframe")
    prev = object()
    for row in hits:
        vision = row.get("vision") or {}
        key = (row.get("pickup_phase"), row.get("vy"), row.get("pickup_action") is not None)
        if key == prev:
            continue
        prev = key
        act = "  <ACTION>" if row.get("pickup_action") else ""
        print(f"  {row['t'] - t0:6.2f} {str(row.get('pickup_phase')):<22} "
              f"{row.get('lateral_cm'):>8} {row.get('travel_cm'):>8} "
              f"{str(row.get('vy')):>5}  {row['mask']:>4}  "
              f"{str(vision.get('result_frame_id')):>6}{act}")

    lat = [r.get("lateral_cm") for r in hits if r.get("lateral_cm") is not None]
    print(f"\nlateral_cm range over the state: {min(lat):.2f} .. {max(lat):.2f} "
          f"(span {max(lat) - min(lat):.2f} cm)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
