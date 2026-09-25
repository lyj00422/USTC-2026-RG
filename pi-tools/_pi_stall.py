"""Summarise a telemetry jsonl: is the run progressing, or frozen in one leg?

Read-only.  Prints what differs across records, which is what separates
"car driving, odometry dead" from "car not moving at all".

Usage:  python _pi_stall.py [jsonl path]
"""
import json
import os
import sys

LOGS = "/home/pi/robogame-runtime/logs"


def newest_log():
    """See _pi_sample.py: argv is not forwarded, so default to the live log."""
    files = [os.path.join(LOGS, n) for n in os.listdir(LOGS)
             if n.endswith(".jsonl")]
    return max(files, key=os.path.getmtime)


PATH = sys.argv[1] if len(sys.argv) > 1 else newest_log()

records = []
with open(PATH) as fh:
    for line in fh:
        line = line.strip()
        if line:
            records.append(json.loads(line))

if not records:
    print("EMPTY")
    raise SystemExit(0)

first, last = records[0], records[-1]
print(f"records      {len(records)}")
print(f"t span       {first['t']:.1f} -> {last['t']:.1f}  "
      f"({last['t'] - first['t']:.1f} s of telemetry)")
if len(records) > 1:
    dt = (last['t'] - first['t']) / (len(records) - 1)
    print(f"rate         {1.0 / dt:.1f} Hz  ({dt * 1000:.0f} ms/record)")

print(f"states seen  {sorted({r['state'] for r in records})}")
print(f"intents seen {sorted({r['intent'] for r in records})}")

masks = sorted({r['mask'] for r in records})
print(f"masks seen   {['0x%02X' % m for m in masks]}")

travels = [r['travel_cm'] for r in records if r['travel_cm'] is not None]
if travels:
    print(f"travel_cm    {min(travels):.2f} .. {max(travels):.2f}  "
          f"(nonzero in {sum(1 for v in travels if v != 0)} of {len(travels)})")
else:
    print("travel_cm    never populated")

deltas = {r['encoder_delta'] for r in records}
print(f"enc_delta    {len(deltas)} distinct value(s): "
      f"{sorted(deltas)[:6]}{' ...' if len(deltas) > 6 else ''}")

speeds = {r['actual_speed'] for r in records}
print(f"actual_speed {len(speeds)} distinct: "
      f"{sorted(speeds)[:8]}{' ...' if len(speeds) > 8 else ''}")

encs = {tuple(r['encoder']) if r['encoder'] else None for r in records}
print(f"encoder      {len(encs)} distinct tuple(s)")
print(f"  first      {first['encoder']}")
print(f"  last       {last['encoder']}")

issued = {r['issued'] for r in records}
print(f"issued       {sorted(i for i in issued if i)}")
