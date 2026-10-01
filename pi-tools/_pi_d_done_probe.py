"""Send one `D` and record EVERY reply the firmware sends afterwards.

The route can only report "no DONE arrived".  This shows whether the firmware
actually sends `DONE D` when nothing else is on the wire, which is the one fact
that separates the two remaining explanations:

  * the firmware DOES send it -> the route's own polling swallowed it (the
    heartbeat's `SPD` is the prime suspect: the firmware only answers the FIRST
    command of a back-to-back burst, so an SPD landing on the D can eat the DONE)
  * the firmware does NOT send it -> the D never completed on its own, and the
    turn is being stopped by something else

Nothing but the one `D` goes out during the wait -- no SPD, no ENC, no STOP --
so the reply stream is the firmware's own behaviour and nothing we did.

Run on the Pi via _pi_run_file.py:
    _pi_d_done_probe.py /home/pi/robogame-runtime [rotate_deg] [watch_s]
"""
import sys
import time
from pathlib import Path

ROOT = Path(sys.argv[1]) if len(sys.argv) > 1 else Path("/home/pi/robogame-runtime")
sys.path.insert(0, str(ROOT))

from rg_runtime.app_support import load_runtime_config     # noqa: E402
from rg_runtime.chassis_link import chassis_transport_factory  # noqa: E402
from rg_runtime.devices import ChassisDevice               # noqa: E402

ROTATE = int(sys.argv[2]) if len(sys.argv) > 2 else -90
WATCH_S = float(sys.argv[3]) if len(sys.argv) > 3 else 8.0

runtime = load_runtime_config(str(ROOT / "config" / "runtime.yaml"))
factory, _wait = chassis_transport_factory(runtime)

# The JDY-31 needs a moment to release the last session before it will answer a
# new page, and it goes quiet entirely when the chassis side is unhappy, so a
# single connect attempt reports "the radio" when it means "not yet".
transport = None
last = None
deadline = time.monotonic() + 45.0
while transport is None:
    try:
        transport = factory(runtime.chassis_device, runtime.chassis_baudrate)
    except Exception as exc:                                      # noqa: BLE001
        last = exc
        if time.monotonic() >= deadline:
            print("FAILED to connect within 45s: %s: %s"
                  % (type(exc).__name__, exc))
            raise SystemExit(1)
        print("connect failed (%s), retrying" % type(exc).__name__)
        time.sleep(3)

device = ChassisDevice(transport)
print("transport = %s (mac %s ch %s)"
      % (runtime.chassis_transport, runtime.chassis_bluetooth_mac,
         runtime.chassis_spp_channel))

# The FIRST command after a socket connect is always answered
# `ERR: use V, M, D, SEQ or STOP` -- measured three times on 2026-10-01, with
# SPD, with D, whatever.  `ChassisLink.open()` primes with a STOP, which is what
# absorbs it and is why the route's own D is accepted.  Reproduce that here, or
# the D under test is the one that gets eaten and the run says nothing about
# DONE.
time.sleep(0.5)
device.stop()
time.sleep(0.4)
stale = [str(getattr(r, "value", r)) for r in device.poll()]
print("priming STOP answered: %s" % (stale or "nothing"))

def encoders():
    """The firmware's own wheel counts -- the witness for 'did it actually move'."""
    device.request_encoder()
    time.sleep(0.25)
    for reply in device.poll():
        if getattr(reply, "kind", None) == "encoder":
            return getattr(reply, "value", None)
    return None


print("ENC before: %s" % encoders())

print("\n--- sending D 0 0 %d 80, then NOTHING for %.0fs ---" % (ROTATE, WATCH_S))
t0 = time.monotonic()
device.run_distance(0, 0, ROTATE, 80)

seen = []
while time.monotonic() - t0 < WATCH_S:
    el = time.monotonic() - t0
    # RAW lines, not `device.poll()`: poll() drops anything `parse_chassis_reply`
    # cannot classify, so a mangled `DONE D` would be invisible.  This prints the
    # bytes as they came off the wire, parsed or not.
    for line in transport.read_lines():
        seen.append(line)
        print("  +%6.3fs  RAW %r" % (el, line))
    time.sleep(0.02)

print("\n--- done; %.0fs elapsed ---" % (time.monotonic() - t0))
print("kinds seen: %s" % (seen or "NOTHING AT ALL"))
print("ENC after : %s" % encoders())

# Leave the car stopped whatever happened.
try:
    device.stop()
    time.sleep(0.3)
    device.poll()
except Exception:                                                 # noqa: BLE001
    pass
transport.close()

print()
if any("DONE" in line for line in seen):
    print("VERDICT: the firmware DOES send DONE on its own when nothing else is")
    print("         on the wire -- so the route's polling is eating it.")
elif seen:
    print("VERDICT: raw lines arrived but none was a DONE -- the move did not")
    print("         complete on its own (or DONE is mangled past recognition).")
else:
    print("VERDICT: NOTHING came back at all -- the firmware never answered the D.")
