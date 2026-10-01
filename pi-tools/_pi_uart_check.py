"""Post-wire-swap sanity check: does the UART config still load and wire up?

Run ON the Pi via _pi_run_file.py.  Read-only: it loads config, imports the
modules that used to take a transport factory, and reports what the chassis
service would open.  It never opens the port and never commands motion.

The point is the *removed* fields.  The wire swap deleted
chassis_transport / chassis_bluetooth_mac / chassis_spp_channel /
chassis_keepalive_* from RuntimeConfig and chassis_transport_factory from
chassis_link, so anything still reaching for them raises AttributeError at
import or first use -- this finds that in one shot instead of at the launch
gate on the field.
"""
import sys

sys.path.insert(0, "/home/pi/robogame-runtime")

from rg_runtime.app_support import load_runtime_config   # noqa: E402

cfg = load_runtime_config("/home/pi/robogame-runtime/config/runtime.yaml")
print("device   =", cfg.chassis_device)
print("baudrate =", cfg.chassis_baudrate)
print("heartbeat_ms =", cfg.heartbeat_ms)
for gone in ("chassis_transport", "chassis_bluetooth_mac", "chassis_spp_channel",
             "chassis_keepalive_enabled", "chassis_keepalive_s"):
    print(f"  {gone}: {'STILL PRESENT' if hasattr(cfg, gone) else 'removed (expected)'}")

import rg_runtime.chassis_link as cl                                # noqa: E402
print("chassis_transport_factory:",
      "STILL PRESENT" if hasattr(cl, "chassis_transport_factory") else "removed (expected)")
print("SocketTransport in chassis_link:",
      "yes" if hasattr(cl, "SocketTransport") else "no (expected)")

import rg_runtime.transports as tr                                  # noqa: E402
print("SocketTransport in transports:",
      "yes" if hasattr(tr, "SocketTransport") else "no (expected)")

from control_hub.services.chassis_service import ChassisService     # noqa: E402
from control_hub.state import HubState                              # noqa: E402
from control_hub.services.event_log import EventLog                 # noqa: E402
service = ChassisService(HubState(), EventLog())
print("ChassisService: opened without a transport factory;",
      "keepalive_tick", "STILL PRESENT" if hasattr(service, "keepalive_tick") else "removed (expected)")
print("  ports() ->", service.ports())

import run_route_v2                                                 # noqa: E402,F401
import control_hub.server                                           # noqa: E402,F401
print("imports OK: run_route_v2, control_hub.server")
