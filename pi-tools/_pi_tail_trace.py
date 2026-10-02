"""Print the last N telemetry rows with the fields the field work actually reads.

Run it ON the Pi:
    python _pi_run_file.py pi-tools/_pi_tail_trace.py /home/pi/robogame-runtime 300 \
        -- /home/pi/robogame-runtime/logs/route_v2_full_XXXX.jsonl 60
"""
import json
import sys


def main():
    path = sys.argv[1]
    count = int(sys.argv[2]) if len(sys.argv) > 2 else 60
    rows = []
    with open(path, encoding="utf-8") as handle:
        for line in handle:
            try:
                rows.append(json.loads(line))
            except ValueError:
                continue
    for row in rows[-count:]:
        vision = row.get("vision") or {}
        loop = row.get("loop") or {}
        print(
            f"t={row.get('t'):8.2f} {row.get('state'):<24}"
            f" {str(row.get('intent')):<7}"
            f" mask={row.get('mask'):>3}"
            f" lat={str(row.get('lateral_cm')):>8}"
            f" fwd={str(row.get('travel_cm')):>8}"
            f" vis={str(vision.get('task')):<14}"
            f" pl={loop.get('plan')}"
        )


if __name__ == "__main__":
    main()
