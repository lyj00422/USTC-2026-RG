"""Post-deploy check on the Pi: config, imports, packages, and a dry run.

Run ON the Pi via _pi_run_file.py, AFTER a push and BEFORE any launch.

Nothing here commands motion: the dry run is `run_simulation`, which steps the
state machine against fixtures and never opens the chassis or the camera.  That is
the point -- it proves the pushed state_machine/config/run_route_v2 are coherent
on the Pi's own interpreter before the car is allowed to move.

The dry run is also the check that the vision dependency really is gone from
BUILD_AREA: the simulator feeds it no build reading at all.
"""
import os
import subprocess
import sys

ROOT = "/home/pi/robogame-runtime"
PY = f"{ROOT}/.venv/bin/python"
sys.path.insert(0, ROOT)

print("--- config ---")
from route_v2.config import load_route_v2_config          # noqa: E402
cfg = load_route_v2_config(f"{ROOT}/config/route_v2.yaml")
print("  build_right_step_cm   =", cfg.build_right_step_cm)
print("  build_slide_max_cm    =", cfg.build_slide_max_cm)
print("  pickup_3_speed        =", cfg.pickup_3_speed)
print("  seek_line_min_black   =", cfg.seek_line_min_black_probes)

print("--- imports ---")
import run_route_v2                                        # noqa: E402,F401
from route_v2 import state_machine                         # noqa: E402,F401
from route_v2.state_machine import RouteState              # noqa: E402
task = run_route_v2.vision_task_for_state(RouteState.BUILD_AREA)
print("  vision task for BUILD_AREA =", task.value,
      "(NONE is correct -- the build area runs no vision now)")
assert task.value == "NONE", "BUILD_AREA still maps to a vision task"

print("--- packages ---")
from route_v2.pickup_action import compile_action, load_action_catalog  # noqa: E402
catalog = load_action_catalog(f"{ROOT}/data/route_v2_actions")
bad = 0
for role in sorted(catalog):
    pkg = catalog[role]
    expected = tuple(s.get("enabled") for s in pkg["action"]["steps"]
                     if s.get("kind") == "SUCTION")
    try:
        compiled = compile_action(pkg, forward_speed_limit=80,
                                  expected_suction=expected)
    except Exception as exc:                                # noqa: BLE001
        print("  FAILED", role, type(exc).__name__, exc)
        bad += 1
        continue
print(f"  {len(catalog)} roles, FAILURES {bad}")

print("--- dry run (no motion, no camera) ---")
done = subprocess.run([PY, "run_route_v2.py", "--dry-run", "--full",
                       "--config", "config/route_v2.yaml"],
                      cwd=ROOT, capture_output=True, text=True, timeout=300)
tail = (done.stdout or "").strip().splitlines()
print("  exit", done.returncode)
for line in tail[-3:]:
    print("  " + line[:160])
if done.returncode != 0:
    print("  stderr:", (done.stderr or "")[-500:])
raise SystemExit(1 if (bad or done.returncode) else 0)
