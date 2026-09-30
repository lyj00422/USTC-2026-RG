"""Print the first N telemetry rows of a run: time, state, sensor mask.

Answers "what does the bar read at this pose" from recorded data, so a mask of 0
in a fresh probe can be compared against the same starting pose in an earlier run
rather than guessed at.
"""
import json
import sys

path = sys.argv[1] if len(sys.argv) > 1 else "logs/route_v2_j3_20260930_004918.jsonl"
count = int(sys.argv[2]) if len(sys.argv) > 2 else 12


def bits(mask):
    if mask is None:
        return "--------"
    return "".join(str((mask >> (7 - i)) & 1) for i in range(8))


with open(path, encoding="utf-8") as fh:
    for i, line in enumerate(fh):
        if i >= count:
            break
        try:
            row = json.loads(line)
        except ValueError:
            continue
        mask = row.get("sensor_mask", row.get("mask"))
        print(f"{row.get('t', 0):9.2f}  {str(row.get('state'))[:34]:34}  "
              f"mask {mask if mask is not None else '--':>4}  {bits(mask)}")
