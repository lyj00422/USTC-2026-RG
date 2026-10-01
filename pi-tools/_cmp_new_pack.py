"""Compare the 2026-09-30 23:27 console export against the packages deployed on the Pi.

Read-only.  Prints, per recording, the deployed package it looks like it targets
(matched by name), the step-by-step arm trace of both, and the arm-only diff.
"""
import json
import sys
from pathlib import Path

NEW = Path(r"D:\xwechat_files\wxid_9us2thiolf0622_58d0\msg\file\2026-10"
           r"\动作包_20260930_232712\动作包_20260930_232712")
OLD = Path(r"C:\Users\LJY\Desktop\RG\local-materials\pi-packs-20261001")

ARM_KINDS = ("SERVO", "SUCTION")


def load(p):
    return json.loads(Path(p).read_text(encoding="utf-8"))


def fmt(step):
    k = step.get("kind")
    if k == "SERVO":
        return "SERVO %d @%d(%d)" % (step["id"], step["position"], step.get("time_ms", 0))
    if k == "SUCTION":
        return "SUCTION %s" % ("on" if step.get("enabled") else "off")
    if k == "CHASSIS":
        c = step.get("command")
        if c == "velocity":
            return "CHASSIS vx=%s vy=%s wz=%s" % (step.get("vx"), step.get("vy"), step.get("wz"))
        return "CHASSIS %s" % c
    return k


def trace(steps):
    return [fmt(s) for s in steps]


def arm_only(steps):
    return [fmt(s) for s in steps if s.get("kind") in ARM_KINDS]


def main():
    names = {}
    for p in sorted(OLD.glob("*/action.json")):
        d = load(p)
        names.setdefault(d.get("name", ""), []).append(p.parent.name)

    print("### 部署在树莓派上的包名")
    for n in sorted(names):
        print("  %-40s -> %s" % (n, ",".join(names[n])))
    print()

    for rec in sorted(p for p in NEW.iterdir() if p.is_dir()):
        aj = rec / "action.json"
        if not aj.exists():
            continue
        d = load(aj)
        steps = d.get("steps", [])
        nm = d.get("name", "")
        print("=" * 78)
        print("新录制 %s   name=%s   steps=%d" % (rec.name, nm, len(steps)))
        cand = names.get(nm, [])
        if cand:
            print("  同名旧包: %s" % ",".join(cand))
        else:
            print("  ⚠ 没有同名旧包（名字对不上，需要人工指定映射）")
        print("  --- 新包机械臂动作 (%d) ---" % len(arm_only(steps)))
        for i, t in enumerate(arm_only(steps)):
            print("    %2d %s" % (i, t))
        print()


if __name__ == "__main__":
    sys.exit(main())
