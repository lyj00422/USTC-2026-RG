"""Read-only: the full vision block during the orange search.

Dumps the raw vision record for a handful of rows spread across
PICKUP_2_VISION_ONLY, so the diagnostics the detector publishes (candidate
counts, holds, faults, frame freshness) can be read directly instead of
inferred from the phase alone.
"""

import glob
import json

TELEM_GLOB = "/home/pi/robogame-runtime/logs/route_v2_full_orange_*.jsonl"
STATE = "PICKUP_2_VISION_ONLY"
PICK = 6


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
    step = max(1, len(hits) // PICK)
    print(f"file: {path}\n")
    for row in hits[::step][:PICK + 2]:
        print(f"--- t+{row['t'] - t0:6.2f}  phase={row.get('pickup_phase')} "
              f"lateral={row.get('lateral_cm')} mask={row['mask']}")
        print(f"    vision = {json.dumps(row.get('vision'), ensure_ascii=False)}")
        for key in ("prescan", "pickup_phase", "pickup_result", "pickup_reason",
                    "pickup_action", "pickup_count", "round", "blocks"):
            if key in row:
                print(f"    {key} = {json.dumps(row[key], ensure_ascii=False)}")
    print("\nkeys present on a row:")
    print(sorted(hits[len(hits) // 2].keys()))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
