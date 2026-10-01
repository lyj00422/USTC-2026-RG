"""Try to connect the JDY-31 and report exactly what the adapter says.

Run ON the Pi via _pi_run_file.py.

Two steps, because they fail for different reasons and the difference matters:
  1. `bluetoothctl connect` -- the CLASSIC profile/ACL path.
  2. the SPP socket the route itself uses -- if this connects while (1) fails, or
     vice versa, the problem is in one layer, not in the radio.

No sudo: a password prompt over the exec channel would hang instead of reporting.
"""
import subprocess
import sys
from pathlib import Path
import time

MAC = "6E:53:BD:74:00:A7"
ROOT = Path("/home/pi/robogame-runtime")


def run(cmd, timeout=40):
    try:
        done = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    except subprocess.TimeoutExpired:
        return "<TIMEOUT>"
    except Exception as exc:                                      # noqa: BLE001
        return f"<{type(exc).__name__}: {exc}>"
    return ((done.stdout or "") + (done.stderr or "")).strip()


print("=== bluetoothctl connect ===")
print(run(["bluetoothctl", "--timeout", "15", "connect", MAC]) or "(no output)")
time.sleep(1.5)

info = run(["bluetoothctl", "info", MAC], timeout=20)
for line in info.splitlines():
    if any(k in line for k in ("Connected", "Paired", "Trusted", "RSSI", "Blocked")):
        print("  " + line.strip())

print("\n=== the SPP socket the route uses ===")
sys.path.insert(0, str(ROOT))
from rg_runtime.app_support import load_runtime_config  # noqa: E402
from rg_runtime.devices import ChassisDevice           # noqa: E402
from rg_runtime.transports import SocketTransport      # noqa: E402

runtime = load_runtime_config(str(ROOT / "config" / "runtime.yaml"))
t0 = time.monotonic()
try:
    transport = SocketTransport(runtime.chassis_bluetooth_mac,
                                runtime.chassis_spp_channel)
except Exception as exc:                                          # noqa: BLE001
    print(f"  SOCKET CONNECT FAILED {type(exc).__name__}: {exc}")
    raise SystemExit(1)
print(f"  connected in {time.monotonic() - t0:.2f}s")

device = ChassisDevice(transport)
try:
    device.request_speed()
    time.sleep(0.6)
    got = list(device.poll())
    print(f"  {len(got)} reply line(s): "
          + ", ".join(f"{r.kind}={r.value!r}" for r in got))
    device.stop()
finally:
    transport.close()
print("  socket closed cleanly")
