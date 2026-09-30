"""Confirm the Pi is running the config and action packages we just pushed."""
import json
import sys

sys.path.insert(0, "/home/pi/robogame-runtime")

from route_v2.config import load_route_v2_config  # noqa: E402

ROOT = "/home/pi/robogame-runtime"
cfg = load_route_v2_config(f"{ROOT}/config/route_v2.yaml")

print("pickup_3_arrived_distance_cm =", cfg.pickup_3_arrived_distance_cm)
print("build_cap_seek_max_cm        =", cfg.build_cap_seek_max_cm)
print("build_find_confirm_frames    =", cfg.build_find_confirm_frames)
print("build_target_lost_frames     =", cfg.build_target_lost_frames)
print("build_occupancy.max_roi_fill =",
      cfg.vision.block_profiles["build_occupancy"].max_roi_fill)
print()

for pkg in ("orange_left_latest", "orange_right_latest", "orange_hold_latest",
            "purple_pickup_latest"):
    doc = json.load(open(f"{ROOT}/data/route_v2_actions/{pkg}/action.json",
                         encoding="utf-8"))
    steps = doc["steps"]
    chassis = [(i, s.get("forward_cm"), s.get("speed"))
               for i, s in enumerate(steps) if s["kind"] == "CHASSIS"]
    print(f"{pkg:22} {len(steps):>2} steps  chassis={chassis}")
