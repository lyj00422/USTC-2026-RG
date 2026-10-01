"""ONE `D`, over the rfcomm socket, and report whether the wheels actually moved.

Run ON the Pi via _pi_run_file.py:

    python pi-tools\\_pi_run_file.py pi-tools\\_pi_d_socket.py /home/pi/robogame-runtime 120
    python pi-tools\\_pi_run_file.py pi-tools\\_pi_d_socket.py /home/pi/robogame-runtime 120 -- 5 0 0 20

This is `_pi_d_wheels.py` for the socket transport.  That tool cannot be used any
more: it opens `/dev/robogame-chassis`, and under `rfcomm_socket` that node does
not exist -- verified absent, so `_pi_d_wheels` / `_pi_chassis_ok` /
`_pi_cmd_syntax` / `_pi_halt` all fail on connect rather than reporting anything.

Default command is `D 0 0 90 80`: the exact command run route_v2_full_20261001_120140
dispatched once at t=632.32 and which the car did not answer in any way.  It is also
the route's own planned first action for a `--from JUNCTION_3_TURN_LEFT` start, so it
is a motion the car was going to make anyway.

Answers, in order of what they rule out:
  * does the firmware reply at all (DONE / ERR ...), and how long it takes
  * do the four wheel counters change, and by how much (yaw degrees via the route's
    own 37.1 counts/deg, so a 90 deg command should read ~90)
  * do the wheel SPEEDS go non-zero while it runs

A D that answers DONE with zero wheel movement is a mechanical/driver problem; a D
that never answers is the firmware never having taken the command; wheel movement
with no reply is a reply-path problem.  Those need three different fixes, which is
why all three are reported.
"""
import sys
import time
from pathlib import Path

ROOT = Path(sys.argv[1]) if len(sys.argv) > 1 else Path("/home/pi/robogame-runtime")
sys.path.insert(0, str(ROOT))

from rg_runtime.app_support import load_runtime_config  # noqa: E402
from rg_runtime.devices import ChassisDevice           # noqa: E402
from rg_runtime.transports import SocketTransport      # noqa: E402

args = [a for a in sys.argv[2:] if a.strip()]
if len(args) >= 4:
    forward, right, rotate, speed = (int(a) for a in args[:4])
else:
    forward, right, rotate, speed = 0, 0, 90, 80
WATCH_S = 14.0
YAW_COUNTS_PER_DEG = 37.1

runtime = load_runtime_config(str(ROOT / "config" / "runtime.yaml"))
print(f"command = D {forward} {right} {rotate} {speed}")
print(f"transport = {runtime.chassis_transport}  mac = {runtime.chassis_bluetooth_mac}")

t0 = time.monotonic()
transport = SocketTransport(runtime.chassis_bluetooth_mac,
                            runtime.chassis_spp_channel)
print(f"connected in {time.monotonic() - t0:.2f}s")
device = ChassisDevice(transport)


def query_before():
    """One clean request/reply, sent ALONE.

    The firmware answers only the FIRST command in a burst, so the encoder read
    cannot be pipelined behind anything else -- it has to be its own exchange.
    """
    device.request_encoder()
    time.sleep(0.5)
    replies = device.poll()
    for reply in replies:
        print(f"  before rx {reply.kind} {reply.value!r}")
    return replies


print("\n--- before ---")
before = query_before()

print(f"\n--- sending D {forward} {right} {rotate} {speed} ---")
device.run_distance(forward, right, rotate, speed)
sent_at = time.monotonic()
print(f"  sent at +{sent_at - t0:.2f}s")

done_at = None
last_speeds = None
try:
    while time.monotonic() - sent_at < WATCH_S:
        time.sleep(0.5)
        for reply in device.poll():
            when = time.monotonic() - t0
            print(f"  [{when:6.2f}s] rx {reply.kind} {reply.value!r}")
            if reply.kind == "done" and done_at is None:
                done_at = time.monotonic()
        if done_at is not None:
            break
        device.request_speed()
        time.sleep(0.3)
        for reply in device.poll():
            when = time.monotonic() - t0
            if reply.kind == "speed" and reply.value != last_speeds:
                last_speeds = reply.value
                print(f"  [{when:6.2f}s] speed {reply.value!r}")
except Exception as exc:                                          # noqa: BLE001
    print(f"  ERROR {type(exc).__name__}: {exc}")

if done_at is None:
    print(f"  NO DONE within {WATCH_S}s")
else:
    print(f"  DONE after {done_at - sent_at:.2f}s")

print("\n--- after ---")
after = query_before()

print("\n--- verdict ---")
print(f"  reply: {'DONE' if done_at else 'none'}")
print("  compare the two encoder readings above; a 90 deg command should move the")
print(f"  yaw projection by ~90 deg ({YAW_COUNTS_PER_DEG} counts/deg = 3338 counts).")

try:
    device.stop()
except Exception:                                                 # noqa: BLE001
    pass
transport.close()
