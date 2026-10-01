"""Fingerprint every package tree on the Pi, live and backed up.

The `.bak-<stamp>` names are the deploy tool's, and each one is the state
*before* a push at that stamp -- so they are a dated timeline of what was
actually live, which is the only way to answer "what was deployed at commit X"
for data that .gitignore keeps out of the repository.

Read-only: it opens JSON files and prints.  Touches no device.

    python _pi_run_file.py pi-tools\\_pi_fingerprint_backups.py /home/pi/robogame-runtime
"""
import json
import os

ROOT = "/home/pi/robogame-runtime/data"
PROBE = (
    ("build_three_latest", "build_three"),
    ("orange_left_latest", "orange_left"),
    ("purple_pickup_latest", "purple_pickup"),
    ("reset_final", "reset"),
)


def steps(path):
    try:
        with open(os.path.join(path, "action.json"), encoding="utf-8") as fh:
            return json.load(fh)["steps"]
    except Exception:
        return None


def main() -> int:
    trees = sorted(d for d in os.listdir(ROOT) if d.startswith("route_v2_actions"))
    for name in trees:
        base = os.path.join(ROOT, name)
        if not os.path.isdir(base):
            continue
        print(f"--- {name}   (mtime {os.path.getmtime(base):.0f})")
        for pkg, label in PROBE:
            st = steps(os.path.join(base, pkg))
            if st is None:
                print(f"      {label:14s} --")
                continue
            ch = sum(1 for s in st if s.get("kind") == "CHASSIS")
            id0 = [s["position"] for s in st if s.get("kind") == "SERVO" and s.get("id") == 0]
            id1 = [s["position"] for s in st if s.get("kind") == "SERVO" and s.get("id") == 1]
            print(f"      {label:14s} {len(st):3d}步/CH{ch:<3d} id0={id0} id1={id1}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
