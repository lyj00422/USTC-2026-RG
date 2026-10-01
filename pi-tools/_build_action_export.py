"""Assemble a route_v2_actions tree from the repo's `action/` export.

The operator's export under `action/` is the only place the taught packages
exist inside the project: `data/` and `action/` are both .gitignore'd, so a
"version" of the action packages means a directory, never a commit.

The mapping from route role to export package is not invented here -- it is
read from the 09-29 catalog the Pi itself wrote, which points every role at
`动作包_20260926_224809/000X` + `复位动作/0002_复位_final`.  Using that table
rather than matching the Chinese names by eye is the whole point: two of the
build packages are easy to swap.

The output keeps the live directory names (`build_three_latest`, ...) because
catalog.json's `path` fields are already identical between that era and now, so
only the contents change.

Usage:
    python _build_action_export.py <output_dir>
"""
import json
import pathlib
import shutil
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
EXPORT = ROOT / "action"
A26 = EXPORT / "action" / "动作包_20260926_224809"
# NOT `action/复位动作`: that export is from 2026-09-21 (its five positions are
# byte-identical to route_capture/arm/动作包_20260921_202303/0002_复位_final,
# id1 1500 / id0 670) and it is older than the 09-26 packages sitting next to it
# in the same folder.  The 09-29 deployment -- and the handoff doc's v3 baseline
# -- use id1 1520 / id0 1840, which is this one.  Chosen 2026-10-01.
RESET = ROOT / "local-materials/new-pkgs-20260929/reset_final_latest"

# role -> (source dir, live dir name).  Read off the 09-29 catalog; see the
# docstring for why this is not re-derived from the names.
MAPPING = {
    "reset":              (RESET, "reset_final"),
    "purple_pickup":      (A26 / "0001_吸紫色到左边", "purple_pickup_latest"),
    "orange_left":        (A26 / "0002_吸橙色放左边", "orange_left_latest"),
    "orange_right":       (A26 / "0003_吸橙色放右边", "orange_right_latest"),
    "orange_hold":        (A26 / "0005_吸橙色吸住-正确版", "orange_hold_latest"),
    "build_base":         (A26 / "0007_搭最底下的两层橙色", "build_base_latest"),
    "top_suction_purple": (A26 / "0008_已经有底下两层橙色 用吸盘的橙色和左边的紫色往上搭一橙一紫", "top_suction_purple_latest"),
    "cap_purple":         (A26 / "0009_已经有底下的两层橙色 用左边的紫色搭第三层紫色封顶", "cap_purple_latest"),
    "top_right_purple":   (A26 / "0010_已经有底下的两层橙色 用右边的橙色和左边的紫色往上搭一橙一紫", "top_right_purple_latest"),
    "build_three":        (A26 / "0011_搭三层橙色", "build_three_latest"),
    "build_two":          (A26 / "0012_搭两层橙色", "build_two_latest"),
    "place_orange":       (A26 / "0013_搭一层", "place_orange_latest"),
}
# Place_purple shares cap_purple; that alias is in the catalog, not a package.
SHARED = {"place_purple": "cap_purple"}


def main() -> int:
    out = pathlib.Path(sys.argv[1] if len(sys.argv) > 1 else "route_v2_actions")
    src_catalog = (ROOT / "pi-tools/_pulled/pi-pull-20261001-1930/actions/"
                   "route_v2_actions.pi-original/catalog.json")
    live = json.loads(src_catalog.read_text(encoding="utf-8"))["catalog"]

    if out.exists():
        shutil.rmtree(out)
    out.mkdir(parents=True)

    new_catalog = {}
    for role, (src, dirname) in MAPPING.items():
        if not (src / "action.json").is_file():
            print(f"  MISSING {role}: {src}")
            return 1
        dest = out / dirname
        dest.mkdir(parents=True, exist_ok=True)
        n = 0
        for f in sorted(src.iterdir()):
            if f.is_file():
                shutil.copy2(f, dest / f.name)
                n += 1
            elif f.is_dir():
                shutil.copytree(f, dest / f.name, dirs_exist_ok=True)
                n += 1
        doc = json.loads((src / "action.json").read_text(encoding="utf-8"))
        try:
            rel = src.relative_to(EXPORT).as_posix()
            source = f"action/{rel}"
        except ValueError:                       # the reset comes from outside action/
            source = src.relative_to(ROOT).as_posix()
        new_catalog[role] = {
            "path": dirname,
            "name": doc.get("name", dirname),
            "source": source,
            "window_required": live.get(role, {}).get("window_required", False),
        }
        print(f"  {role:20s} <- {source:70s} ({n} files)")

    for role, target in SHARED.items():
        new_catalog[role] = dict(new_catalog[target])
        print(f"  {role:20s} <- (shares {target})")

    # Preserve the unused v1 packages so nothing that referenced them breaks.
    for extra in ("purple_pickup_v1", "purple_place_v1"):
        src = (ROOT / "pi-tools/_pulled/pi-pull-20261001-1930/actions/"
               "route_v2_actions.pi-original" / extra)
        if src.is_dir():
            shutil.copytree(src, out / extra, dirs_exist_ok=True)
            print(f"  {extra:20s} <- (carried over unchanged)")

    (out / "catalog.json").write_text(
        json.dumps({"catalog": new_catalog}, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8", newline="\n")
    print(f"\nwrote {out}  ({len(new_catalog)} roles)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
