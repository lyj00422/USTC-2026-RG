"""Drive the Runtime console's own arm endpoints over loopback HTTP.

Runs ON the Pi, next to the console (127.0.0.1:8080), so the action goes through
exactly the path the /operate「动作 1」button uses:
    POST /api/arm/run {"routine": 1}   ->   ARM,RUN,1

Two modes, and the motion is opt-in in the argument so a typo cannot launch it:

    python _pi_arm_action1.py status   # acquire lease + auto-connect + report.  NO MOTION.
    python _pi_arm_action1.py run1     # status, then fire routine 1 and follow it to DONE.

Why the console API and not the serial port: the console holds the arm tty and
the chassis flock, so a second process on /dev/robogame-arm would fight it.
"""
import json
import os
import sys
import time
import urllib.error
import urllib.request

# Defaults to the Pi's loopback (run it there with _pi_run_file.py).  Override to
# drive the console from the laptop instead: the console binds 0.0.0.0, so
# RG_CONSOLE_BASE=http://10.101.79.137:8080 works and _pi_run_file.py cannot
# forward CLI args anyway.
BASE = os.environ.get("RG_CONSOLE_BASE", "http://127.0.0.1:8080").rstrip("/")
OWNER = "claude-cli"

# routine 1 walks all five axes to protocol midpoint 1500 (~3000 ms/step).  It
# is a firmware placeholder, not a calibrated HOME, so give it room and then
# report what actually came back rather than assuming it finished.
RUN_TIMEOUT_S = 90.0


def call(method, path, body=None, token=None):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(BASE + path, data=data, method=method)
    if data is not None:
        req.add_header("Content-Type", "application/json")
    if token:
        req.add_header("x-control-token", token)
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            return resp.status, json.loads(resp.read().decode() or "{}")
    except urllib.error.HTTPError as exc:
        raw = exc.read().decode()
        try:
            return exc.code, json.loads(raw or "{}")
        except json.JSONDecodeError:
            return exc.code, {"raw": raw}


def show(label, payload):
    print(f"  {label}: {json.dumps(payload, ensure_ascii=False)}")


def arm_line(status):
    return (f"mode={status.get('mode')} calibrated={status.get('calibrated')} "
            f"suction={status.get('suction_commanded')} "
            f"routine={status.get('routine')} step={status.get('step')}")


def prepare():
    """Acquire the lease and connect the fixed devices.  Sends no motion."""
    code, payload = call("POST", "/api/control/acquire", {"owner": OWNER})
    if code != 200:
        print(f"ABORT: acquire failed HTTP {code} {payload}")
        raise SystemExit(1)
    token = payload["token"]
    print(f"lease acquired (owner={OWNER})")

    code, payload = call("POST", "/api/devices/auto-connect", {}, token)
    print(f"auto-connect -> HTTP {code}")
    if code != 200:
        print(f"ABORT: auto-connect failed: {payload}")
        raise SystemExit(1)
    show("arm", payload.get("arm", {}))
    if payload.get("chassis"):
        show("chassis", payload["chassis"])

    code, arm = call("GET", "/api/arm/status", token=token)
    print(f"arm status -> HTTP {code}")
    show("arm", arm)

    # A LOCKED arm silently rejects RUN, so make READY an explicit precondition
    # rather than discovering it from a 409 later.
    if arm.get("mode") != "READY":
        print(f"arm is {arm.get('mode')}, sending ARM,ENABLE")
        code, payload = call("POST", "/api/arm/enable", {}, token)
        print(f"enable -> HTTP {code}")
        show("arm", payload)
        time.sleep(0.5)
        code, arm = call("GET", "/api/arm/status", token=token)
        show("arm", arm)

    code, system = call("GET", "/api/system/status", token=token)
    show("safety", system.get("safety", {}))
    show("lease", system.get("lease", {}))
    return token, arm


def main():
    mode = (sys.argv[1] if len(sys.argv) > 1 else "status").lower()
    if mode not in {"status", "run1"}:
        print(f"unknown mode {mode!r}; use 'status' or 'run1'")
        return 2

    print("=== prepare (no motion) ===")
    token, arm = prepare()

    if mode == "status":
        print("\nstatus-only mode: no motion command sent.")
        return 0

    if arm.get("mode") != "READY":
        print(f"\nABORT: refusing to run routine 1, arm is {arm.get('mode')} not READY")
        return 1
    if arm.get("calibrated") is not True:
        print(f"\nABORT: refusing to run routine 1, calibrated={arm.get('calibrated')}")
        return 1

    print("\n=== MOTION: ARM,RUN,1 (all five axes -> 1500, ~3000 ms/step) ===")
    print(f"  before: {arm_line(arm)}")
    code, payload = call("POST", "/api/arm/run", {"routine": 1}, token)
    print(f"  run(1) -> HTTP {code}")
    if code != 200:
        print(f"  ABORT: console refused the command: {payload}")
        return 1
    show("arm", payload)

    deadline = time.monotonic() + RUN_TIMEOUT_S
    last = None
    while time.monotonic() < deadline:
        time.sleep(0.5)
        call("POST", "/api/control/heartbeat", {}, token)
        code, cur = call("GET", "/api/arm/status", token=token)
        line = arm_line(cur)
        if line != last:
            print(f"  t+{RUN_TIMEOUT_S - (deadline - time.monotonic()):5.1f}s  {line}")
            last = line
        # routine goes back to None and mode leaves BUSY once EVENT,DONE lands
        if cur.get("mode") != "BUSY" and cur.get("routine") is None:
            print("  routine finished (mode left BUSY, routine cleared)")
            break
    else:
        print(f"  WARNING: still not finished after {RUN_TIMEOUT_S:.0f}s -- read state above")

    code, final = call("GET", "/api/arm/status", token=token)
    print(f"\n  after: {arm_line(final)}")
    show("arm", final)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
