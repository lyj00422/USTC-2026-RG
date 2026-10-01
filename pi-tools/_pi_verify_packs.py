"""Load and compile every action package on the Pi with the route's own code.

This is the exact validation `run_route_v2.py` does at launch.  Run it after any
package deploy: if it passes, the route cannot fail at startup on the packages.

Usage (from pi-tools, via _pi_run_file.py):
    python _pi_run_file.py _pi_verify_packs.py
"""
import os
import sys
from pathlib import Path

ROOT = Path("/home/pi/robogame-runtime")
sys.path.insert(0, str(ROOT))

from route_v2.pickup_action import compile_action, load_action_catalog  # noqa: E402

catalog_root = ROOT / "data" / "route_v2_actions"
try:
    catalog = load_action_catalog(catalog_root)
except Exception as exc:                                          # noqa: BLE001
    print("FAILED load_action_catalog: %s: %s" % (type(exc).__name__, exc))
    raise SystemExit(1)
print("OK load_action_catalog: %d roles" % len(catalog))

bad = 0
for role in sorted(catalog):
    package = catalog[role]
    expected = tuple(s.get("enabled") for s in package["action"]["steps"]
                     if s.get("kind") == "SUCTION")
    try:
        compiled = compile_action(package, forward_speed_limit=80,
                                  expected_suction=expected)
    except Exception as exc:                                      # noqa: BLE001
        print("  FAILED %-22s %s: %s" % (role, type(exc).__name__, exc))
        bad += 1
        continue
    id1 = [s.position for s in compiled if s.kind == "servo" and s.servo_id == 1]
    print("  ok %-22s %-24s %2d steps  id1=%s"
          % (role, package["action"]["name"], len(compiled), id1))

print("FAILED %d roles" % bad if bad else "OK every role loads and compiles")
raise SystemExit(1 if bad else 0)
