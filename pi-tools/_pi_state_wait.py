"""Wait for a state to appear in the live telemetry, then dump its whole window.

Read-only: fetches the telemetry tail over SFTP, polls, and prints.  Sends
nothing to the chassis or the arm.

Written to watch a specific behaviour on the field without polling by hand: the
launch gates take ~15 s and the state under test may be minutes into the route,
so "poll until it happens, then show me everything about it" is the whole job.

    python _pi_state_wait.py BUILD_AREA [--timeout-s 240] [--poll-s 3]

Prints each row's t, intent, the command actually issued to the chassis, and the
odometers -- `issued` is what makes a lateral slide legible ("V 0 -12 0" is a
right strafe; positive vy would be left).
"""

import argparse
import json
import os
import time

import paramiko

HOST = os.environ.get("RG_PI_HOST", "172.20.10.11")
USER = os.environ.get("RG_PI_USER", "pi")
ROOT = "/home/pi/robogame-runtime"
TELEMETRY = f"{ROOT}/logs/route_v2_telemetry.jsonl"
TAIL_BYTES = 400_000


def read_tail(client):
    sftp = client.open_sftp()
    try:
        size = sftp.stat(TELEMETRY).st_size
        with sftp.open(TELEMETRY, "r") as handle:
            handle.seek(max(0, size - TAIL_BYTES))
            raw = handle.read().decode("utf-8", "replace")
    finally:
        sftp.close()
    rows = []
    for line in raw.splitlines():
        line = line.strip()
        if line:
            try:
                rows.append(json.loads(line))
            except json.JSONDecodeError:
                pass
    return rows


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("state")
    parser.add_argument("--timeout-s", type=float, default=240.0)
    parser.add_argument("--poll-s", type=float, default=3.0)
    parser.add_argument("--leave-frames", type=int, default=12,
                        help="keep printing this many rows after the state ends")
    args = parser.parse_args()

    client = paramiko.SSHClient()
    client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    client.connect(HOST, username=USER, password=os.environ["RG_PI_PW"],
                   timeout=15, allow_agent=False, look_for_keys=False)
    deadline = time.time() + args.timeout_s
    window, after, seen_end = [], 0, False
    try:
        while time.time() < deadline:
            rows = read_tail(client)
            window = [r for r in rows if r.get("state") == args.state]
            if window:
                # Everything from the first entry to now, then the rows after it.
                first = min(i for i, r in enumerate(rows) if r.get("state") == args.state)
                tail = rows[first:]
                if tail[-1].get("state") != args.state:
                    seen_end = True
                    after = sum(1 for r in tail if r.get("state") != args.state)
                    if after >= args.leave_frames:
                        break
            if window and seen_end:
                break
            time.sleep(args.poll_s)
    finally:
        client.close()

    if not window:
        print(f"[wait] {args.state} never appeared within {args.timeout_s:.0f}s")
        return 1

    span = window
    mask = []
    for r in span:
        mask.append(r)
    clear = [r for r in span if r.get("intent") == "strafe"]
    print(f"[wait] {args.state}: {len(span)} ticks, "
          f"t {span[0]['t']:.1f} -> {span[-1]['t']:.1f} "
          f"({span[-1]['t'] - span[0]['t']:.1f}s)")
    print(f"[wait] intents: {sorted({r.get('intent') for r in span})}")
    issued = [r.get("issued") for r in span if r.get("issued")]
    print(f"[wait] chassis commands issued (first 14): {issued[:14]}")
    lats = [r.get("lateral_cm") for r in span if r.get("lateral_cm") is not None]
    if lats:
        print(f"[wait] lateral_cm {lats[0]:+.2f} .. {lats[-1]:+.2f} "
              f"(min {min(lats):+.2f} max {max(lats):+.2f})")
    print(f"[wait] strafe-speed commands: "
          f"{sorted({r.get('issued') for r in clear if r.get('issued')})}")
    print("[wait] per-tick (sampled every 5th):")
    for index, r in enumerate(span):
        if index % 5 and index != len(span) - 1:
            continue
        print(f"   t={r['t']:8.1f} intent={str(r.get('intent')):7} "
              f"issued={str(r.get('issued')):14} travel={r.get('travel_cm')} "
              f"lateral={r.get('lateral_cm')} mask={r.get('mask')} "
              f"vision={json.dumps(r.get('vision'), ensure_ascii=False)[:110]}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
