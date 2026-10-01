"""Every SERVO position in every deployed package, per servo id.

Read-only.  Run on the Pi via _pi_run_file.py.  The 大臂 (id 1) is the axis whose
values this round changed, so it gets a summary line per package, plus a flag for
any id-1 value above 2000 -- on the new servo the working range is ~500..1000, so
anything up there is either a leftover wrapped pose or a genuinely different axis.
"""
import json
from pathlib import Path

PACKS = Path("/home/pi/robogame-runtime/data/route_v2_actions")

catalog = json.loads((PACKS / "catalog.json").read_text(encoding="utf-8"))
referenced = {v.get("path") for v in catalog.get("catalog", {}).values()}

print("%-26s %-8s %s" % ("package", "loaded", "servo id -> positions"))
print("-" * 100)
for path in sorted(PACKS.glob("*/action.json")):
    doc = json.loads(path.read_text(encoding="utf-8"))
    per = {}
    for step in doc.get("steps", []):
        if step.get("kind") == "SERVO":
            per.setdefault(step["id"], []).append(step["position"])
    tag = "yes" if path.parent.name in referenced else "NO"
    print("%-26s %-8s %s" % (path.parent.name, tag,
                             "  ".join("%d:%s" % (k, v) for k, v in sorted(per.items()))))

print()
print("id1 (大臂) 概览 —— 新舵机的工作区间约 500..1000，>2000 的都不是这一轮录的:")
for path in sorted(PACKS.glob("*/action.json")):
    doc = json.loads(path.read_text(encoding="utf-8"))
    vals = [s["position"] for s in doc.get("steps", [])
            if s.get("kind") == "SERVO" and s["id"] == 1]
    if not vals:
        continue
    hi = [v for v in vals if v > 2000]
    print("  %-26s %-34s %s" % (path.parent.name, vals,
                                ("<== 异常 %s" % hi) if hi else ""))
