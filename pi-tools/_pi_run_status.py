"""One-shot READ-ONLY status poll for the running full route.

Fetches the live telemetry over SFTP, summarises it, and reports whether the
route process is still alive.  Sends nothing to the chassis or the arm.

The launcher (start_full_route.py) runs run_route_v2.py WITHOUT --log-telemetry,
so the telemetry lands on the default path, logs/route_v2_telemetry.jsonl --
which is why _pi_route_watch.py (it globs route_v2_full_*.jsonl) does not see
this run.

Usage:
    python _pi_run_status.py
"""

import json
import os
import re
import sys

import paramiko

HOST = os.environ.get("RG_PI_HOST", "172.20.10.11")
USER = os.environ.get("RG_PI_USER", "pi")
ROOT = "/home/pi/robogame-runtime"
TELEMETRY = f"{ROOT}/logs/route_v2_telemetry.jsonl"


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

        _stdin, stdout, _err = client.exec_command(
            "ps -eo pid,etimes,args | grep -F .venv/bin/python | grep -v grep", timeout=30)
        procs = [line.strip() for line in stdout.read().decode().splitlines() if line.strip()]

        _stdin, stdout, _err = client.exec_command(
            f"ls -t {ROOT}/logs/full_*.out | head -1", timeout=30)
        newest_out = stdout.read().decode().strip()
        verdict = ""
        if newest_out:
            _stdin, stdout, _err = client.exec_command(f"cat {newest_out}", timeout=30)
            verdict = stdout.read().decode("utf-8", "replace").strip()
    finally:
        client.close()

    rows = []
    for line in raw.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            rows.append(json.loads(line))
        except json.JSONDecodeError:
            pass  # a line still being written; the next poll will have it

    print("=" * 72)
    if not procs:
        print("ROUTE PROCESS: GONE")
    else:
        for line in procs:
            print("PROC:", line)
    if newest_out:
        print("out:", newest_out, "|", verdict.splitlines()[-1] if verdict else "(empty)")
    if not rows:
        print("no telemetry rows yet")
        return 0

    def seq_of(all_rows):
        out = []
        for row in all_rows:
            if not out or out[-1][0] != row["state"]:
                out.append([row["state"], 1, row["intent"]])
            else:
                out[-1][1] += 1
                out[-1][2] = row["intent"]
        return out

    print(f"ticks: {len(rows)}   uptime t: {rows[0]['t']:.1f} -> {rows[-1]['t']:.1f} "
          f"({rows[-1]['t'] - rows[0]['t']:.1f}s)")
    seq = seq_of(rows)
    print("states (last 8 of %d):" % len(seq))
    for state, n, intent in seq[-8:]:
        print(f"   {state:<36} {n:>5} ticks  last intent={intent}")

    masks = [r["mask"] for r in rows if r.get("mask") is not None]
    if masks:
        print(f"mask: {sum(1 for m in masks if m >= 4)}/{len(masks)} ticks >=4 black probes")
    speeds = [r["actual_speed"] for r in rows if r.get("actual_speed") is not None]
    if speeds:
        print(f"actual_speed: min {min(speeds):.0f} max {max(speeds):.0f}")
    travels = [r["travel_cm"] for r in rows if r.get("travel_cm") is not None]
    if travels:
        print(f"travel_cm: {travels[0]:.1f} -> {travels[-1]:.1f} (max {max(travels):.1f})")
    encs = [r["encoder"][0] for r in rows if r.get("encoder")]
    if encs:
        print(f"encoder[0]: {encs[0]} -> {encs[-1]}  (moved={encs[0] != encs[-1]})")

    last = rows[-1]
    print("LAST:", json.dumps({k: last.get(k) for k in
          ("state", "intent", "mask", "line_error", "actual_speed", "issued",
           "travel_cm", "lateral_cm", "pickup_phase", "wall_contact")}, ensure_ascii=False))
    print("LAST vision:", json.dumps(last.get("vision"), ensure_ascii=False))
    print("LAST camera:", json.dumps(last.get("camera"), ensure_ascii=False))

    # The documented wedge signature: intent stop with the state not changing.
    # BUILD_ACTION is excluded: it is a legitimate long stop -- the arm action
    # owns the chassis and the route is right to send nothing while it works, so
    # counting it fires a false alarm on every build.
    tail = rows[-max(1, len(rows) // 10):]
    if (tail and all(r["intent"] == "stop" for r in tail)
            and tail[-1]["state"] != "BUILD_ACTION"):
        print(f"!! WEDGE SIGNAL: last {len(tail)} ticks are all intent=stop")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
