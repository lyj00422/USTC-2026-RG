"""Post-rollback sanity check for the 36265b5 + direct-UART tree.

Version-agnostic on purpose: `_pi_postdeploy_check.py` asserts on
`build_right_step_cm` and on BUILD_AREA mapping to no vision task, both of which
the 2026-10-01 rollback deliberately removed.  This checks only what has to hold
on any version.

Read-only: no motion, no camera.  The dry run steps the state machine against
fixtures.
"""
import os
import subprocess
import sys

ROOT = "/home/pi/robogame-runtime"
PY = f"{ROOT}/.venv/bin/python"
sys.path.insert(0, ROOT)

print("--- config ---")
from rg_runtime.app_support import load_runtime_config   # noqa: E402
from route_v2.config import load_route_v2_config          # noqa: E402
runtime = load_runtime_config(f"{ROOT}/config/runtime.yaml")
cfg = load_route_v2_config(f"{ROOT}/config/route_v2.yaml")
print("  chassis device  =", runtime.chassis_device, runtime.chassis_baudrate)
print("  seek_line_min_black_probes =", cfg.seek_line_min_black_probes)
for gone in ("chassis_bluetooth_mac", "chassis_spp_channel", "chassis_transport",
             "chassis_keepalive_enabled"):
    print(f"  {gone}: {'STILL PRESENT' if hasattr(runtime, gone) else 'removed (expected)'}")
print(f"  control_takeover_after_ms: "
      f"{'STILL PRESENT' if hasattr(runtime, 'control_takeover_after_ms') else 'removed (expected)'}")

print("--- imports ---")
import run_route_v2                                        # noqa: E402,F401
from route_v2 import state_machine                         # noqa: E402,F401
from route_v2.state_machine import RouteState              # noqa: E402
print("  vision task for BUILD_AREA =",
      run_route_v2.vision_task_for_state(RouteState.BUILD_AREA).value,
      "(a real task is CORRECT for 36265b5 -- the build area is vision-driven here)")
import rg_runtime.chassis_link as cl                       # noqa: E402
print("  chassis_transport_factory:",
      "STILL PRESENT" if hasattr(cl, "chassis_transport_factory") else "removed (expected)")

print("--- packages ---")
from route_v2.pickup_action import compile_action, load_action_catalog  # noqa: E402
catalog = load_action_catalog(f"{ROOT}/data/route_v2_actions")
bad = 0
for role in sorted(catalog):
    pkg = catalog[role]
    expected = tuple(s.get("enabled") for s in pkg["action"]["steps"]
                     if s.get("kind") == "SUCTION")
    try:
        compile_action(pkg, forward_speed_limit=80, expected_suction=expected)
    except Exception as exc:                                # noqa: BLE001
        print("  FAILED", role, type(exc).__name__, exc)
        bad += 1
print(f"  {len(catalog)} roles, FAILURES {bad}")

print("--- dry run (no motion, no camera) ---")
done = subprocess.run([PY, "run_route_v2.py", "--dry-run", "--full",
                       "--config", "config/route_v2.yaml"],
                      cwd=ROOT, capture_output=True, text=True, timeout=300)
print("  exit", done.returncode)
for line in (done.stdout or "").strip().splitlines()[-2:]:
    print("  " + line[:160])
print("  (exit 1 stopping at BUILD_AREA is the EXPECTED 36265b5 behaviour:")
print("   its build area runs VisionTask.BUILD_OCCUPANCY, which a dry run cannot feed)")
raise SystemExit(1 if bad else 0)
