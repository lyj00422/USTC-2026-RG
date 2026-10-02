"""Trace the odometer through BUILD_ACTION, so a package's velocity bursts can
be read back in centimetres.

A build package's chassis moves are recorded as `velocity` + `stop` pairs with
durations, so the JSON says nothing about how far the car went.  The runner's
own odometry does: `travel_cm` (forward) and `lateral_cm` (sideways) are
integrated from ENC every tick, including while an action package owns the
chassis.  This prints those two, compressed to the rows where they actually
moved, for every BUILD_ACTION segment of a telemetry log.

Run it ON the Pi:
    python _pi_run_file.py pi-tools/_pi_build_moves.py /home/pi/robogame-runtime 300 \
        -- /home/pi/robogame-runtime/logs/route_v2_full_XXXX.jsonl
"""
import json
import sys

STEP_CM = 0.5


def segments(path):
    rows = []
    with open(path, encoding="utf-8") as handle:
        for line in handle:
            try:
                row = json.loads(line)
            except ValueError:
                continue
            if row.get("state") != "BUILD_ACTION":
                continue
            rows.append((
                row.get("t"),
                row.get("travel_cm"),
                row.get("lateral_cm"),
                row.get("loop", {}).get("plan"),
            ))
    out, current = [], []
    for row in rows:
        if current and row[0] - current[-1][0] > 1.0:
            out.append(current)
            current = []
        current.append(row)
    if current:
        out.append(current)
    return out


def main():
    path = sys.argv[1]
    for number, seg in enumerate(segments(path), start=1):
        print(f"=== BUILD_ACTION #{number}: {seg[0][0]:.1f}s -> {seg[-1][0]:.1f}s "
              f"({len(seg)} ticks) plan={seg[0][3]}")
        last = None
        for t, forward, lateral, _plan in seg:
            if forward is None or lateral is None:
                continue
            if last is None or (abs(forward - last[0]) >= STEP_CM
                                or abs(lateral - last[1]) >= STEP_CM):
                delta = "" if last is None else (
                    f"  d_fwd={forward - last[0]:+7.2f}"
                    f"  d_lat={lateral - last[1]:+7.2f}")
                print(f"  t={t:8.2f}  fwd={forward:8.2f}  lat={lateral:8.2f}{delta}")
                last = (forward, lateral)


if __name__ == "__main__":
    main()
