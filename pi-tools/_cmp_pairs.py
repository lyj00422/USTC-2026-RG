"""Side-by-side step listing of one new recording against one deployed package."""
import json
import sys
from pathlib import Path

NEW = Path(r"D:\xwechat_files\wxid_9us2thiolf0622_58d0\msg\file\2026-10"
           r"\动作包_20260930_232712\动作包_20260930_232712")
OLD = Path(r"C:\Users\LJY\Desktop\RG\local-materials\pi-packs-20261001")

PAIRS = [
    ("0001_吸紫色到左边", "purple_pickup_latest"),
    ("0002_吸橙色到右边", "orange_right_latest"),
    ("0003_吸橙色到左边", "orange_left_latest"),
    ("0004_吸橙色吸住", "orange_hold_latest"),
    ("0005_从吸盘和右侧搭两层", "build_base_latest"),
    ("0006_从左侧吸紫色搭三层", "cap_purple_latest"),
    ("0007_从右侧和左侧取橙色和紫色搭三四层", "top_right_purple_latest"),
    ("0008_从吸盘和左侧用橙色和紫色搭三四层", "top_suction_purple_latest"),
    ("0009_吸盘_左侧_右侧搭三层", "build_three_latest"),
    ("0010_从左右侧搭两层", "build_two_latest"),
    ("0011_从左侧搭一层", "place_orange_latest"),
]


def load(p):
    return json.loads(Path(p).read_text(encoding="utf-8"))


def fmt(s):
    k = s.get("kind")
    if k == "SERVO":
        return "SERVO %d @%d t%d" % (s["id"], s["position"], s.get("time_ms", 0))
    if k == "SUCTION":
        return "SUCTION %s" % ("on" if s.get("enabled") else "off")
    if k == "CHASSIS":
        c = s.get("command")
        if c == "velocity":
            return "CHASSIS v x=%s y=%s w=%s" % (s.get("vx"), s.get("vy"), s.get("wz"))
        return "CHASSIS %s" % c
    if k == "WAIT":
        return "WAIT %s" % s.get("seconds")
    return str(k)


def cols(steps):
    out = []
    for i, s in enumerate(steps):
        out.append("%3d %s" % (i, fmt(s)))
    return out


def main():
    only = sys.argv[1:] or None
    for new_name, old_name in PAIRS:
        if only and old_name not in only and new_name not in only:
            continue
        n = load(NEW / new_name / "action.json")
        o = load(OLD / old_name / "action.json")
        ns, os_ = n.get("steps", []), o.get("steps", [])
        print("=" * 100)
        print("NEW %-40s steps=%d   |   OLD %-26s steps=%d" % (new_name, len(ns), old_name, len(os_)))
        print("  NEW name=%s      OLD name=%s" % (n.get("name"), o.get("name")))
        ln, lo = cols(ns), cols(os_)
        for i in range(max(len(ln), len(lo))):
            a = ln[i] if i < len(ln) else " " * 24
            b = lo[i] if i < len(lo) else ""
            print("  %-26s | %s" % (a, b))
        print()


if __name__ == "__main__":
    main()
