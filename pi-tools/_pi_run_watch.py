"""Poll a running full route and EXIT when something worth waking up for happens.

Read-only: it fetches the tail of the live telemetry over SFTP and reads ``ps``.
It never sends anything to the chassis or the arm.

Why this exists: the route's failure mode is a SILENT HOLD -- the car stops and
the state machine keeps re-issuing the same intent forever, with nothing in
``full_*.out`` until you kill it (route_v2.md section 6).  Polling by hand every
minute is how you notice; this script is that poll, with the exit conditions
written down:

  * the route process is gone                       -> RUN ENDED
  * FAULT appears in the newest full_*.out           -> FAULT
  * the state has not changed for --stuck-s seconds  -> WEDGE (the documented
    while the intent is stop                            silent-hold signature)

States where a long stop is CORRECT are excluded from the wedge test: BUILD_ACTION
and any PICKUP_*_VISION_ONLY (an action package owns the chassis; the route is
right to send nothing) -- the same exclusion _pi_run_status.py uses.

Exits with one summary line, so it is usable as a background waiter.

Usage:
    python _pi_run_watch.py [--poll-s 15] [--stuck-s 90] [--max-s 540]
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

# A long stop in these is the design, not a wedge.
STOP_IS_EXPECTED = ("BUILD_ACTION", "PICKUP_VISION_ONLY", "PICKUP_2_VISION_ONLY",
                    "PURPLE_PRESCAN")
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
        if not line:
            continue
        try:
            rows.append(json.loads(line))
        except json.JSONDecodeError:
            pass  # the partial first line of the tail, or one mid-write
    return rows


def route_alive(client):
    _stdin, stdout, _err = client.exec_command(
        "pgrep -f 'run_route_v[2]\\.py' | head -1", timeout=30)
    return bool(stdout.read().decode().strip())


def newest_out_text(client):
    _stdin, stdout, _err = client.exec_command(
        f"ls -t {ROOT}/logs/full_*.out | head -1", timeout=30)
    newest = stdout.read().decode().strip()
    if not newest:
        return "", ""
    _stdin, stdout, _err = client.exec_command(f"cat {newest}", timeout=30)
    return newest, stdout.read().decode("utf-8", "replace")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--poll-s", type=float, default=15.0)
    parser.add_argument("--stuck-s", type=float, default=90.0)
    parser.add_argument("--max-s", type=float, default=540.0,
                        help="give up waiting after this long and say so, so the "
                             "caller can re-arm")
    args = parser.parse_args()

    client = paramiko.SSHClient()
    client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    client.connect(HOST, username=USER, password=os.environ["RG_PI_PW"],
                   timeout=15, allow_agent=False, look_for_keys=False)
    deadline = time.time() + args.max_s
    last_state = None
    state_since = time.time()
    last_report = 0.0
    try:
        while True:
            rows = read_tail(client)
            alive = route_alive(client)
            now = time.time()

            state = rows[-1].get("state") if rows else "?"
            intent = rows[-1].get("intent") if rows else "?"
            t_end = rows[-1].get("t") if rows else 0.0
            if state != last_state:
                last_state, state_since = state, now

            if alive and now - last_report >= 60.0:
                held = now - state_since
                print(f"[watch] t={t_end:.0f} state={state} intent={intent} "
                      f"held={held:.0f}s rows={len(rows)}", flush=True)
                last_report = now

            if not alive:
                out_path, text = newest_out_text(client)
                print(f"\n[watch] RUN ENDED (no route process). last state={state}")
                print(f"[watch] {out_path}: {text.strip().splitlines()[-1] if text.strip() else '(empty)'}")
                return 0

            out_path, text = newest_out_text(client)
            if "FAULT" in text:
                line = [l for l in text.splitlines() if "FAULT" in l]
                print(f"\n[watch] FAULT in {out_path}: {line[-1] if line else text.strip()}")
                print(f"[watch] last state={state} intent={intent} t={t_end:.0f}")
                return 1

            if (now - state_since >= args.stuck_s and intent == "stop"
                    and not any(state.startswith(p) for p in STOP_IS_EXPECTED)):
                print(f"\n[watch] WEDGE: state {state} has held {now - state_since:.0f}s "
                      f"with intent=stop (t={t_end:.0f})")
                return 2

            if now >= deadline:
                print(f"\n[watch] still running after {args.max_s:.0f}s: state={state} "
                      f"intent={intent} t={t_end:.0f} (re-arm to keep watching)")
                return 3
            time.sleep(args.poll_s)
    finally:
        client.close()


if __name__ == "__main__":
    raise SystemExit(main())
