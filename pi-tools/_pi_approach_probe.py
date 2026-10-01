"""Read-only: fingerprint the live pickup packages and check no route is running.

Run ON the Pi via _pi_run_file.py, BEFORE pushing the approach change:

    python pi-tools\\_pi_run_file.py pi-tools\\_pi_approach_probe.py

Prints an md5 per package so the local stage the edit was built from can be proven
to be the deployed one (the merge stage, not the older pull), and lists any
`run_route_v2` process -- a live run has already loaded the catalog, so packages
must not be swapped under it.
"""
import hashlib
import os
import subprocess

ROOT = "/home/pi/robogame-runtime/data/route_v2_actions"
ROLES = ("orange_right_latest", "orange_left_latest", "orange_hold_latest",
         "purple_pickup_latest")

for role in ROLES:
    path = os.path.join(ROOT, role, "action.json")
    try:
        with open(path, "rb") as handle:
            raw = handle.read()
    except OSError as exc:
        print("%-22s MISSING %s" % (role, exc))
        continue
    print("%-22s md5=%s  %d bytes" % (role, hashlib.md5(raw).hexdigest(), len(raw)))

print("--- live route processes ---")
found = subprocess.run(["pgrep", "-af", "route_v2"],
                       capture_output=True, text=True).stdout.strip()
print(found if found else "(none -- safe to deploy)")
