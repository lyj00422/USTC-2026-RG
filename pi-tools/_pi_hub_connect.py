"""Take the console's control lease and run its own auto-connect, then report.

Run ON the Pi via _pi_run_file.py.

Why: after the 2026-09-30 reboot the OS-level links were both healthy --
`rfcomm0 -> 6E:53:BD:74:00:A7 channel 1 connected`, `/dev/robogame-chassis`,
`/dev/robogame-arm -> ttyUSB1` (CH340 1a86:7523) -- yet /api/system/status
still said chassis and arm DISCONNECTED.  The console never connects devices
on its own: /api/devices/auto-connect needs a control lease first, and the
browser is what normally takes one.  This drives the same two calls the page's
buttons do, so the answer is the console's own, not a guess.

No motion: auto-connect opens the ports and (arm) probes + ENABLEs.  The
chassis side only opens the link.
"""
import json
import urllib.error
import urllib.request

BASE = "http://127.0.0.1:8080"


def call(path, payload=None, token=None, timeout=30):
    data = None if payload is None else json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(BASE + path, data=data, method="POST" if data is not None else "GET")
    if data is not None:
        req.add_header("Content-Type", "application/json")
    if token:
        req.add_header("Authorization", "Bearer " + token)
        req.add_header("X-Control-Token", token)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as response:
            return response.status, json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", "replace")
        try:
            return exc.code, json.loads(body)
        except ValueError:
            return exc.code, {"raw": body}
    except Exception as exc:                                  # noqa: BLE001
        return None, {"error": str(exc)}


def short(status):
    if not status:
        return "--"
    if not status.get("connected"):
        return "DISCONNECTED (%s)" % (status.get("error") or status.get("mode") or status.get("state"))
    return "connected %s" % status.get("device")


print("--- status before ---")
_, before = call("/api/system/status")
print("   arm     : %s" % short(before.get("arm")))
print("   chassis : %s" % short(before.get("chassis")))

print("--- POST /api/control/acquire ---")
code, acquired = call("/api/control/acquire", {"owner": "link-check"})
print("   http %s  %s" % (code, acquired))
token = acquired.get("token")
if not token:
    raise SystemExit("ABORT: no control token; auto-connect cannot be attempted")

print("--- POST /api/devices/auto-connect ---")
code, result = call("/api/devices/auto-connect", {}, token=token, timeout=60)
print("   http %s  ok=%s" % (code, result.get("ok")))
if not result.get("ok"):
    print("   error: %s" % (result.get("error") or result))

print("--- status after ---")
_, after = call("/api/system/status")
print("   arm     : %s" % short(after.get("arm")))
print("   chassis : %s" % short(after.get("chassis")))
print("   line    : %s" % short(after.get("line")))
print("   safety  : %s" % after.get("safety"))

print("--- POST /api/control/release ---")
print("   %s" % (call("/api/control/release", {}, token=token)[1],))

print("--- serial traffic the console logged ---")
for line in (after.get("arm", {}).get("last_reply"), after.get("chassis", {}).get("last_reply")):
    print("   %r" % (line,))
