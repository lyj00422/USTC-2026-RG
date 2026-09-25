"""Load a route config ON THE PI and print the speed parameters it yields.

Uploading a file proves the bytes arrived; it does not prove the route will read
the values out of it.  This runs the real loader with the real validator, so a
config that would be refused at launch is refused here instead.
"""
import sys

# The script is shipped to /tmp and run from there, and Python puts the SCRIPT's
# directory on sys.path rather than the working directory, so the runtime root has
# to be added explicitly (same as _pi_ready.py).
sys.path.insert(0, "/home/pi/robogame-runtime")

from route_v2.config import load_route_v2_config  # noqa: E402

# _pi_run_file.py does not forward arguments to the remote script, so the path is
# fixed here rather than taken from argv.
path = "config/route_v2.yaml"
cfg = load_route_v2_config(path, require_visual_calibration=True)

keys = [
    "forward_speed", "pickup_speed", "turn_speed", "tag2_strafe_speed",
    "lateral_limit", "yaw_limit", "kp", "ki", "kd", "integral_limit",
    "junction_2_seek_line_vy", "junction_3_seek_line_vy", "pickup_seek_line_vy",
    "pickup_2_seek_line_vy", "purple_place_seek_line_vy",
    "seek_line_fallback_speed", "align_speed", "align_forward_speed",
]
print(f"loaded {path} with require_visual_calibration=True")
for key in keys:
    print(f"  {key:<28} {getattr(cfg, key)}")
print("block-recognition strafes (must be unchanged):")
for area in ("purple", "orange"):
    spec = cfg.vision.pickup_areas[area]
    print(f"  {area:<8} search={spec.search_speed} coarse={spec.coarse_speed} "
          f"fine={spec.fine_speed}")
