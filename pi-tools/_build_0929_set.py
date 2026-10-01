"""Assemble a route_v2_actions tree from local-materials/new-pkgs-20260929.

That archive is the pre-servo-replacement set: every package returns 大臂
(id 1) to **1520**, and the base (id 0) is already on the new 630 / 1020 / 1840
baseline.  The 2026-09-30 大臂 servo swap shifted the whole family by -520
(1520 -> 1000), which is why the set that ran later that day looks unrelated if
you only compare one value.

The mapping comes from the live catalog's role->path table, which is unchanged
between the two generations; `place_purple` shares `cap_purple_latest`.

    python _build_0929_set.py <output_dir>
"""
import json
import pathlib
import shutil
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
SRC = ROOT / "local-materials/new-pkgs-20260929"
# role -> (source dir name, live dir name)
MAPPING = {
    "reset":              ("reset_final_latest", "reset_final"),
    "purple_pickup":      ("purple_pickup_latest", "purple_pickup_latest"),
    "orange_left":        ("orange_left_latest", "orange_left_latest"),
    "orange_right":       ("orange_right_latest", "orange_right_latest"),
    "orange_hold":        ("orange_hold_latest", "orange_hold_latest"),
    "build_base":         ("build_base_latest", "build_base_latest"),
    "cap_purple":         ("cap_purple_latest", "cap_purple_latest"),
    "top_right_purple":   ("top_right_purple_latest", "top_right_purple_latest"),
    "top_suction_purple": ("top_suction_purple_latest", "top_suction_purple_latest"),
    "build_three":        ("build_three_latest", "build_three_latest"),
    "build_two":          ("build_two_latest", "build_two_latest"),
    "place_orange":       ("place_orange_latest", "place_orange_latest"),
}
SHARED = {"place_purple": "cap_purple"}


def main() -> int:
    out = pathlib.Path(sys.argv[1])
    if out.exists():
        shutil.rmtree(out)
    out.mkdir(parents=True)
    catalog = {}
    for role, (srcdir, dstdir) in MAPPING.items():
        src = SRC / srcdir
        if not (src / "action.json").is_file():
            print(f"  MISSING {role}: {src}")
            return 1
        dst = out / dstdir
        dst.mkdir(parents=True, exist_ok=True)
        n = 0
        for f in sorted(src.iterdir()):
            if f.is_file():
                shutil.copy2(f, dst / f.name)
                n += 1
        doc = json.loads((src / "action.json").read_text(encoding="utf-8"))
        id1 = [s["position"] for s in doc["steps"]
               if s.get("kind") == "SERVO" and s.get("id") == 1]
        catalog[role] = {
            "path": dstdir,
            "name": doc.get("name", dstdir),
            "source": f"local-materials/new-pkgs-20260929/{srcdir}",
            "window_required": False,
        }
        print(f"  {role:20s} <- {srcdir:26s} ({n} files)  id1={id1}")
    catalog["reset"]["window_required"] = None
    for role, target in SHARED.items():
        catalog[role] = dict(catalog[target])
        print(f"  {role:20s} <- (shares {target})")
    (out / "catalog.json").write_text(
        json.dumps({"catalog": catalog}, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8", newline="\n")
    print(f"\nwrote {out}  ({len(catalog)} roles)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
