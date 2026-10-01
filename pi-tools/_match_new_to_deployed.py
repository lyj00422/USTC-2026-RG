"""Confirm the new-recording -> deployed-package mapping by CONTENT, not by name.

The 2026-09-29 round taught that these folders' titles can be a package out of
step, so the mapping is scored on the step list: for every (new, deployed) pair,
the arm-step signature (kind, id, position) is compared with difflib and the
ratio printed as a grid.  The intended mapping is the row-max.
"""
import difflib
import json
from pathlib import Path

NEW = Path(r"D:\xwechat_files\wxid_9us2thiolf0622_58d0\msg\file\2026-10"
           r"\动作包_20260930_232712\动作包_20260930_232712")
OLD = Path(r"C:\Users\LJY\Desktop\RG\local-materials\pi-packs-20261001")

DEPLOYED = [
    ("purple_pickup_latest", "抓取"),
    ("orange_right_latest", "抓取"),
    ("orange_left_latest", "抓取"),
    ("orange_hold_latest", "抓取"),
    ("build_base_latest", "搭建"),
    ("cap_purple_latest", "搭建"),
    ("top_right_purple_latest", "搭建"),
    ("top_suction_purple_latest", "搭建"),
    ("build_three_latest", "搭建"),
    ("build_two_latest", "搭建"),
    ("place_orange_latest", "搭建"),
]


def load(p):
    return json.loads(Path(p).read_text(encoding="utf-8"))


def arm_sig(steps, use_pos):
    out = []
    for s in steps:
        if s.get("kind") == "SERVO":
            out.append(("S", s["id"], s["position"] if use_pos else None))
        elif s.get("kind") == "SUCTION":
            out.append(("U", bool(s.get("enabled")), None))
    return out


def ratio(a, b):
    return difflib.SequenceMatcher(None, a, b).ratio()


def main():
    news = sorted(p for p in NEW.iterdir() if p.is_dir() and (p / "action.json").exists())
    olds = {n: load(OLD / n / "action.json") for n, _ in DEPLOYED}

    for use_pos, label in ((True, "含位置"), (False, "只看顺序 (kind,id)")):
        print("### 打分: %s" % label)
        hdr = "  " + " " * 30 + "".join("%-12s" % n.replace("_latest", "")[:11]
                                        for n, _ in DEPLOYED)
        print(hdr)
        best = {}
        for rec in news:
            nd = load(rec / "action.json")
            a = arm_sig(nd.get("steps", []), use_pos)
            row = []
            for name, _kind in DEPLOYED:
                b = arm_sig(olds[name].get("steps", []), use_pos)
                r = ratio(a, b)
                row.append(r)
                if r >= max(best.get(rec.name, (0, ""))[0], 0):
                    pass
            s = "".join("%-12s" % ("%.3f" % r) for r in row)
            print("  %-30s%s" % (rec.name[:29], s))
            top = sorted(zip(row, [n for n, _ in DEPLOYED]), reverse=True)[:2]
            best[rec.name] = top
        print("\n  最佳匹配:")
        for rec in news:
            t = best[rec.name]
            print("    %-30s -> %-26s %.3f   (次选 %s %.3f)"
                  % (rec.name[:29], t[0][1], t[0][0], t[1][1], t[1][0]))
        print()


if __name__ == "__main__":
    main()
