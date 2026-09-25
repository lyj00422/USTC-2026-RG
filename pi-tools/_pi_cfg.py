"""Print the deployed route_v2 motion parameters, loaded from the YAML.

Read-only.  Exists because the same check as a shell `grep -E` breaks on the way
through PowerShell: embedded double quotes get mangled in native-command
argument passing, the filename is torn off the end, and grep then sits waiting
on stdin until the SSH call times out.  Loading the config here shows the values
the runner will ACTUALLY use, which a grep of the file only approximates.

Usage:  python _pi_cfg.py        (argv is not forwarded by _pi_run_file.py)
"""
import sys
from pathlib import Path

sys.path.insert(0, "/home/pi/robogame-runtime")

from route_v2.config import load_route_v2_config  # noqa: E402

CFG = Path("/home/pi/robogame-runtime/config/route_v2.yaml")

KEYS = (
    "forward_speed", "pickup_speed", "turn_speed", "align_speed",
    "tag2_strafe_speed", "lateral_limit", "yaw_limit",
    "junction_2_seek_line_vy", "junction_3_seek_line_vy",
    "pickup_seek_line_vy", "pickup_2_seek_line_vy",
    "seek_line_fallback_speed",
    "pickup_3_min_travel_cm", "pickup_3_max_travel_cm",
)

cfg = load_route_v2_config(CFG)
print(f"loaded {CFG}")
for key in KEYS:
    print(f"  {key:26} {getattr(cfg, key)}")
