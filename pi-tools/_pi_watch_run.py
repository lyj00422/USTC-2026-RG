"""Follow a route-v2 run on the Pi, printing one line per state change.

One SSH connection, reopened only if the WiFi link drops (which it does).  Each
line it prints is a state transition plus the telemetry that explains it, so the
output can be piped straight into a monitor.

Usage:
    python _pi_watch_run.py <remote_jsonl> [--interval 15] [--until STATE]
"""
import argparse
import json
import os
import sys
import time

import paramiko

HOST = os.environ.get("RG_PI_HOST", "172.20.10.11")
USER = os.environ.get("RG_PI_USER", "pi")
PROC = "run_route_v2.py"


def connect():
    client = paramiko.SSHClient()
    client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    client.connect(HOST, username=USER, password=os.environ["RG_PI_PW"], timeout=15)
    return client


def probe(client, path, debug=False):
    """Last telemetry row, and whether the run is still alive."""
    # `tail -n 1`, not `-c N`: the file is being appended to while it is read,
    # so a byte-counted tail can end on a half-written line.
    command = (
        f"tail -n 1 {path}; echo '@@@'; pgrep -c -f {PROC} || true"
    )
    _in, out, _err = client.exec_command(command, timeout=20)
    text = out.read().decode("utf-8", "replace")
    if debug or not text.split("@@@")[0].strip():
        # Silence here means the tail failed, and its stderr is the only clue.
        print(f"DEBUG raw={text[:300]!r}", flush=True)
        print(f"DEBUG err={_err.read().decode('utf-8', 'replace')[:300]!r}", flush=True)
    body, _, alive = text.rpartition("@@@")
    row = None
    for line in reversed(body.strip().splitlines()):
        try:
            row = json.loads(line)
            break
        except json.JSONDecodeError:
            continue
    return row, alive.strip() not in ("", "0")


def describe(row):
    return (
        f"{row.get('state')}  t={row.get('t'):.1f}  mask={row.get('mask', 0):08b}  "
        f"travel={row.get('travel_cm')}  lat={row.get('lateral_cm')}  "
        f"enc={row.get('encoder')}  d={row.get('encoder_delta')}  {row.get('intent')}"
    )


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("path")
    parser.add_argument("--interval", type=float, default=15.0)
    parser.add_argument("--until", default=None, help="exit once this state is reached")
    parser.add_argument("--debug", action="store_true")
    args = parser.parse_args()

    client = None
    last_state = None
    while True:
        try:
            if client is None:
                client = connect()
            row, alive = probe(client, args.path, debug=args.debug)
        except Exception as exc:  # the WiFi link drops without warning
            print(f"LINK LOST ({exc.__class__.__name__}) -- reconnecting", flush=True)
            client = None
            time.sleep(args.interval)
            continue
        if row is None:
            print("WAITING for the first telemetry row", flush=True)
        elif row.get("state") != last_state:
            last_state = row.get("state")
            print(describe(row), flush=True)
            if args.until and last_state == args.until:
                print(f"REACHED {last_state}", flush=True)
                return
        if not alive:
            print(f"RUN ENDED at {last_state}", flush=True)
            return
        time.sleep(args.interval)


if __name__ == "__main__":
    sys.exit(main())
