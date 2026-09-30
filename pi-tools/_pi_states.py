"""Summarise a route telemetry JSONL: the state sequence and where time went.

Run ON the Pi:  python pi-tools/_pi_run_file.py pi-tools/_pi_states.py <telemetry.jsonl>

Prints one line per state CHANGE (state, wall-clock seconds spent in the previous
state, issued command at entry) plus a per-state total, so a run that is racing
through states without moving is obvious at a glance: many states, ~0 s each,
`issued` never a motion command.
"""
import json
import sys


def newest(pattern: str) -> str:
    import glob
    hits = glob.glob(pattern)
    if not hits:
        raise SystemExit(f"no file matches {pattern}")
    return max(hits, key=lambda p: __import__("os").path.getmtime(p))


def main(path: str) -> int:
    rows = []
    with open(path, encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if line:
                try:
                    rows.append(json.loads(line))
                except ValueError:
                    pass
    if not rows:
        print("no telemetry rows")
        return 1

    print(f"{len(rows)} rows   t {rows[0]['t']:.1f} .. {rows[-1]['t']:.1f} "
          f"({rows[-1]['t'] - rows[0]['t']:.1f} s)")

    totals = {}
    counts = {}
    prev = None
    for row in rows:
        state = row.get("state")
        totals[state] = totals.get(state, 0.0)
        counts[state] = counts.get(state, 0) + 1
        if prev is None or state != prev[0]:
            if prev is not None:
                print(f"  {prev[0]:<28} {row['t'] - prev[1]:7.2f} s   "
                      f"issued={prev[2]!r}  mask={prev[3]}")
            prev = (state, row["t"], row.get("issued"), row.get("mask"))
    if prev is not None:
        print(f"  {prev[0]:<28} {rows[-1]['t'] - prev[1]:7.2f} s   "
              f"issued={prev[2]!r}  mask={prev[3]}")

    print("\nper-state totals:")
    for state, seconds in sorted(totals.items(), key=lambda kv: -kv[1]):
        print(f"  {state:<28} {seconds:7.2f} s  ({counts[state]} ticks)")

    last = rows[-1]
    print("\nlast row:")
    for key in ("t", "state", "intent", "mask", "line_error", "actual_speed",
                "travel_cm", "lateral_cm", "issued", "wall_contact",
                "suction_latch", "distance_timeouts"):
        print(f"  {key} = {last.get(key)!r}")
    action = last.get("pickup_action")
    if action:
        print(f"  pickup_action = {action}")
    return 0


if __name__ == "__main__":
    # No argument (the usual case: _pi_run_file.py cannot pass one) -> the run
    # that is happening now.
    raise SystemExit(main(sys.argv[1] if len(sys.argv) > 1 else
                          newest("logs/route_v2_*.jsonl")))
