"""READ-ONLY: how the two 2026-09-24 changes actually behaved in a field run.

Prints, from the live telemetry:

  * every PICKUP_2_RETURN_TO_LINE window -- the growing swing -- as the sequence
    of pickup_phase values with the lateral_cm the car was sitting at when each
    phase took over.  That shows how far out the search had to go before the line
    was confirmed, which is the only thing that tells us whether the 20/40/60/80
    ladder is the right size.

  * every DIRECT_ORANGE_D330 window -- the leg converted from a blind D to line
    following -- with its travel_cm at entry/exit and how its line_error and mask
    behaved, so a leg that silently fell back to driving straight is visible.

Sends nothing to the chassis or the arm.  Usage:

    python _pi_swing_trace.py
"""

import json
import os
import sys

import paramiko

HOST = os.environ.get("RG_PI_HOST", "172.20.10.11")
USER = os.environ.get("RG_PI_USER", "pi")
ROOT = "/home/pi/robogame-runtime"
TELEMETRY = f"{ROOT}/logs/route_v2_telemetry.jsonl"

WATCH = ("PICKUP_2_RETURN_TO_LINE", "DIRECT_ORANGE_D330", "BUILD_AREA",
         "PURPLE_RETURN_TO_LINE")


def windows(rows, state):
    """Contiguous runs of `state`, as (first_index, last_index) pairs."""
    out = []
    start = None
    for index, row in enumerate(rows):
        if row.get("state") == state:
            if start is None:
                start = index
        elif start is not None:
            out.append((start, index - 1))
            start = None
    if start is not None:
        out.append((start, len(rows) - 1))
    return out


def main() -> int:
    client = paramiko.SSHClient()
    client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    client.connect(HOST, username=USER, password=os.environ["RG_PI_PW"],
                   timeout=15, allow_agent=False, look_for_keys=False)
    try:
        sftp = client.open_sftp()
        with sftp.open(TELEMETRY, "r") as handle:
            raw = handle.read().decode("utf-8", "replace")
        sftp.close()
    finally:
        client.close()

    rows = []
    for line in raw.splitlines():
        line = line.strip()
        if line:
            try:
                rows.append(json.loads(line))
            except json.JSONDecodeError:
                pass

    print(f"{len(rows)} telemetry rows; t {rows[0]['t']:.1f} -> {rows[-1]['t']:.1f}"
          if rows else "no telemetry")
    for state in WATCH:
        for start, end in windows(rows, state):
            span = rows[start:end + 1]
            print("=" * 72)
            print(f"{state}  rows {start}..{end}  ({len(span)} ticks, "
                  f"{span[-1]['t'] - span[0]['t']:.1f}s)")

            if state == "DIRECT_ORANGE_D330":
                first, last = span[0], span[-1]
                print(f"  travel_cm {first.get('travel_cm')} -> {last.get('travel_cm')}")
                errs = [r.get("line_error") for r in span if r.get("line_error") is not None]
                masks = [r.get("mask") for r in span if r.get("mask") is not None]
                kinds = sorted({r.get("intent") for r in span})
                print(f"  intents seen: {kinds}")
                if errs:
                    print(f"  line_error min {min(errs):+.3f} max {max(errs):+.3f} "
                          f"({len(errs)}/{len(span)} ticks had one)")
                if masks:
                    print(f"  mask distinct: {sorted(set(masks))[:12]}")
                continue

            # The swing: print each pickup_phase change with the lateral_cm there.
            seen = None
            for row in span:
                phase = row.get("pickup_phase")
                if phase != seen:
                    print(f"  t={row['t']:.1f}  {str(seen):>15} -> {str(phase):<15} "
                          f"lateral_cm={row.get('lateral_cm')} "
                          f"intent={row.get('intent')} mask={row.get('mask')} "
                          f"line_error={row.get('line_error')}")
                    seen = phase
            lats = [r.get("lateral_cm") for r in span if r.get("lateral_cm") is not None]
            if lats:
                print(f"  lateral_cm range: {min(lats):+.1f} .. {max(lats):+.1f}")
            issued = [r.get("issued") for r in span if r.get("issued")]
            if issued:
                print(f"  chassis commands: {sorted(set(issued))}")
                print(f"  first 6 issued  : {issued[:6]}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
