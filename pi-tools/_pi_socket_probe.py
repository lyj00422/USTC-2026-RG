"""Open the chassis over an RFCOMM socket and hold it: the rfcomm_socket field test.

Read-only in effect -- it sends SPD (a query) and one STOP, never a motion
command.  Prints every byte the firmware answers, and how long the session
survives, which is the number the whole transport change is about.

Run on the Pi via _pi_run_file.py:  _pi_socket_probe.py /home/pi/robogame-runtime
"""
import socket
import sys
import time
from pathlib import Path

ROOT = Path(sys.argv[1]) if len(sys.argv) > 1 else Path("/home/pi/robogame-runtime")
sys.path.insert(0, str(ROOT))

from rg_runtime.app_support import load_runtime_config  # noqa: E402
from rg_runtime.devices import ChassisDevice           # noqa: E402
from rg_runtime.transports import SocketTransport      # noqa: E402

HOLD_S = float(sys.argv[2]) if len(sys.argv) > 2 else 40.0

runtime = load_runtime_config(str(ROOT / "config" / "runtime.yaml"))
print("transport = %s" % runtime.chassis_transport)
print("mac       = %s channel %s" % (runtime.chassis_bluetooth_mac,
                                     runtime.chassis_spp_channel))

t0 = time.monotonic()
try:
    transport = SocketTransport(runtime.chassis_bluetooth_mac,
                                runtime.chassis_spp_channel)
except Exception as exc:                                          # noqa: BLE001
    print("FAILED to connect: %s: %s" % (type(exc).__name__, exc))
    raise SystemExit(1)
print("connected in %.2fs" % (time.monotonic() - t0))

# Drive it through ChassisDevice, not raw strings: the firmware needs the
# command terminated (every send is "...\r\n") and only the real formatter and
# reply parser prove the wire format survived the transport swap.
device = ChassisDevice(transport)

errors = 0
lines = 0
try:
    while time.monotonic() - t0 < HOLD_S:
        try:
            device.request_speed()
        except Exception as exc:                                  # noqa: BLE001
            errors += 1
            print("[%6.2fs] SEND FAILED %s: %s"
                  % (time.monotonic() - t0, type(exc).__name__, exc))
            break
        time.sleep(0.5)
        try:
            for reply in device.poll():
                lines += 1
                print("[%6.2fs] rx %s %r"
                      % (time.monotonic() - t0, reply.kind, reply.value))
        except Exception as exc:                                  # noqa: BLE001
            errors += 1
            print("[%6.2fs] READ FAILED %s: %s"
                  % (time.monotonic() - t0, type(exc).__name__, exc))
            break
finally:
    try:
        device.stop()
    except Exception:                                             # noqa: BLE001
        pass
    transport.close()

held = time.monotonic() - t0
print("\nheld %.1fs, %d reply lines, %d errors" % (held, lines, errors))
print("VERDICT: %s" % ("link held for the whole window"
                       if errors == 0 else "link died -- see the error above"))
raise SystemExit(0 if errors == 0 else 1)
