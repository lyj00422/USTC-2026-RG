"""READ-ONLY: every servo position each deployed action package contains.

Run ON the Pi via _pi_run_file.py.  Opens no device and sends nothing.

Why it exists: the deployed packages are field data, not repo data, and the
numbers in them get hand-tuned on site.  Two checks need the CURRENT values,
and neither the handoff table nor the repo copy is allowed to stand in for
them:

  * before replacing/retargeting a servo -- which packages drive it, and to
    what positions (the handoff says "read the current values on the Pi and
    compare, do not overwrite from the table")
  * before any re-deploy -- id 0 is the one whose hand-tuned offsets a
    re-deploy silently reverts

`大臂` (the big arm / axis 2) is servo id 1 -- see `arm_lift_servo_id` in
route_v2.yaml, which is also the id the settle rules key on.  Its positions
are what layer height and reach actually are, so they are printed per package
in order rather than collapsed to a set.
"""
import json
from pathlib import Path

ROOT = Path("/home/pi/robogame-runtime")
PACKS = ROOT / "data" / "route_v2_actions"
LIFT_ID = 1


def load(path):
    return json.loads(path.read_text(encoding="utf-8"))


def servo_positions(steps):
    """id -> ordered positions, for every SERVO step in a package."""
    by_id = {}
    for step in steps:
        if step.get("kind") != "SERVO":
            continue
        by_id.setdefault(int(step["id"]), []).append(int(step["position"]))
    return by_id


def main() -> int:
    if not PACKS.is_dir():
        print(f"FAILED: {PACKS} is not a directory -- packages are field data")
        return 1

    catalog_path = PACKS / "catalog.json"
    catalog = load(catalog_path) if catalog_path.exists() else {}
    roles = catalog.get("roles", catalog) if isinstance(catalog, dict) else {}

    print(f"packages under {PACKS}\n")

    for path in sorted(PACKS.glob("*/action.json")):
        by_id = servo_positions(load(path).get("steps", []))
        lift = by_id.get(LIFT_ID, [])
        others = " ".join(
            "id%d=%s" % (i, ",".join(str(p) for p in by_id[i]))
            for i in sorted(by_id) if i != LIFT_ID
        )
        print("%-26s 大臂(id1)=%-46s | %s" % (path.parent.name, lift or "-", others))

    print("\nrole -> package (catalog.json)")
    for role in sorted(roles):
        entry = roles[role]
        target = entry.get("path") if isinstance(entry, dict) else entry
        print("  %-22s %s" % (role, target))

    print("\n大臂(id1) 全部出现过的值: %s" % sorted({
        p
        for path in PACKS.glob("*/action.json")
        for p in servo_positions(load(path).get("steps", [])).get(LIFT_ID, [])
    }))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
