"""Summarise a route telemetry JSONL into the transitions that matter.

The raw file is one line per 50-75 ms tick -- over a hundred lines per run, and
almost all of them identical.  This prints only the rows where something
changed, keeping the timestamp, and adds the three orthogonal projections of the
four encoder counts.

The projections matter because this chassis's wheels are mounted mirrored, so
the four raw counts and their mean say nothing on their own (see
route_v2.md section 5.1):

    forward  (-LF + RF - LR + RR) / 4      58.8 counts/cm
    lateral  ( LF + RF - LR - RR) / 4      56.8 counts/cm, positive = left
    yaw      ( LF + RF + LR + RR) / 4      the SUM; positive = left rotation

The yaw term is the sum.  A previous version used (-LF+RF+LR-RR)/4, which is
orthogonal to all three real modes and therefore identically zero for any
motion the chassis can make -- every "yaw" number it produced was encoder noise.

Usage: python3 _pi_telem.py logs/pid_tune4.jsonl
"""

import json
import sys

KEYS = ("state", "intent", "mask", "issued", "vy", "wz")


def projections(enc):
    if not enc or len(enc) != 4:
        return None
    lf, rf, lr, rr = enc
    return ((-lf + rf - lr + rr) / 4.0, (lf + rf - lr - rr) / 4.0, (lf + rf + lr + rr) / 4.0)


def cell(value, width, fmt=None):
    if value is None:
        text = "--"
    elif fmt:
        text = format(value, fmt)
    else:
        text = str(value)
    return f"{text:>{width}}"


def main():
    path = sys.argv[1]
    with open(path, encoding="utf-8") as handle:
        rows = [json.loads(line) for line in handle if line.strip()]
    if not rows:
        print(f"{path} is empty")
        return 1

    print(f"{len(rows)} ticks from {path}, {rows[-1]['t'] - rows[0]['t']:.2f}s\n")
    print(f"{'t':>8}  {'state':<26} {'intent':<6} {'mask':>4} {'error':>8} {'vy':>4} {'wz':>4} "
          f"{'travel':>7} {'forward':>9} {'lateral':>9} {'yaw':>7}   issued")

    previous = None
    for row in rows:
        encoder = tuple(row.get("encoder") or ())
        key = tuple(row.get(name) for name in KEYS) + (encoder,)
        if key == previous:
            continue
        previous = key
        projection = projections(row.get("encoder"))
        counters = (f"{projection[0]:9.1f} {projection[1]:9.1f} {projection[2]:7.1f}   "
                    if projection else f"{'--':>9} {'--':>9} {'--':>7}   ")
        print(f"{row['t'] - rows[0]['t']:8.2f}  {row['state']:<26} {row['intent']:<6} "
              f"{cell(row.get('mask'), 4)} {cell(row.get('line_error'), 8, '.3f')} "
              f"{cell(row.get('vy'), 4)} {cell(row.get('wz'), 4)} "
              f"{cell(row.get('travel_cm'), 7, '.1f')} {counters}{row.get('issued') or ''}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
