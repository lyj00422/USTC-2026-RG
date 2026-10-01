"""Retarget ONE servo across every deployed action package by a uniform offset.

Run ON the Pi via _pi_run_file.py.  Dry run unless `--apply` is given.

Why: when a servo is replaced, or its horn is re-mounted a spline or two round,
the SAME physical pose gets a different protocol number.  Every package's poses
for that servo were dialled in against the old number, so they are all wrong by
the same delta -- and the operator's rule is that poses are stored relative to
the reset, not absolutely (see the handoff's base-servo note).  So the fix is a
uniform shift, not a re-tune of each package.

The anchor is `reset_final`: whatever value it holds for this servo IS the
reset, and the delta is measured against it.  That is the same method the
(now missing) `_retarget_base_servo.py` used for servo id 0; this is the id-1
version.  `reset_final` is shifted too, so it lands exactly on the new value --
that is the sanity check, and it is verified by reading the file back.

THE WRAP IS NOT OPTIONAL.  500..2500 is enforced at COMPILE time
(`route_v2/pickup_action.py:120`, plus `rg_runtime/devices.py:115` and
`control_hub/services/arm_service.py:117`), so a pose that shifts below 500 does
not merely behave oddly -- the whole package fails to load and the route cannot
run at all.  Out-of-range values are therefore wrapped, and every wrap is
printed, because a wrapped pose commands the servo to the numerically opposite
end of its travel and that is worth a human looking at.

WRAP_PERIOD is 2000 and is INFERRED, not read off a datasheet: the operator
confirmed the relative-offset method and that 1500 is the midpoint, not the
period.  If a turn turns out not to be 2000 units, change the constant here and
re-run -- do not hand-edit packages.

Editing is IN PLACE, digit by digit, rather than by re-serialising the JSON.
The deployed packages are not uniform: most are `indent=2` expanded, but
`purple_pickup_v1` / `purple_place_v1` (the superseded 2026-09-17 recordings)
keep one step per line, and they do not all end in a newline either.  A
round-trip through `json.dumps` rewrites every one of those files end to end and
buries the two numbers that actually moved.  Instead each target step's
`"position"` token is replaced where it stands, and the result is then PARSED
BACK and compared against the intended structure -- so a regex that misfired
cannot reach the disk.
"""
import json
import re
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path("/home/pi/robogame-runtime")
PACKS = ROOT / "data" / "route_v2_actions"
ANCHOR = "reset_final"
BACKUPS = ROOT / "backups"

FLOOR = 500
CEIL = 2500
WRAP_PERIOD = 2000


def wrap(value):
    """Fold a position into 500..2500 on the operator's circle."""
    return ((value - FLOOR) % WRAP_PERIOD) + FLOOR


def positions(steps, servo_id):
    """Indices of the SERVO steps driving `servo_id`."""
    return [i for i, s in enumerate(steps)
            if s.get("kind") == "SERVO" and s.get("id") == servo_id]


def rewrite_positions(text, servo_id, new_values):
    """Replace this servo's `"position"` numbers in place, keeping every byte
    of formatting.  Returns (new_text, count_replaced).

    Within a step object `"position"` always follows `"id"`, and the step is the
    only place both keys appear in an action.json, so `"id": N` up to the next
    `}` pins exactly one position.  `(?![0-9])` keeps id 1 off id 12.
    """
    pattern = re.compile(
        r'("id"\s*:\s*%d(?![0-9])[^}]*?"position"\s*:\s*)(-?\d+)' % servo_id)
    pieces, last, seen = [], 0, 0
    for match in pattern.finditer(text):
        if seen >= len(new_values):
            break
        pieces.append(text[last:match.start(2)])
        pieces.append(str(new_values[seen]))
        last = match.end(2)
        seen += 1
    pieces.append(text[last:])
    return "".join(pieces), seen


def load(path):
    return json.loads(path.read_text(encoding="utf-8"))


def referenced_paths():
    """Package directory names the route actually loads, per catalog.json."""
    catalog = PACKS / "catalog.json"
    if not catalog.exists():
        return None
    data = load(catalog)
    roles = data.get("catalog", data)
    if not isinstance(roles, dict):
        return None
    return {v.get("path") for v in roles.values() if isinstance(v, dict)}


def main() -> int:
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    apply_ = "--apply" in sys.argv
    no_wrap = "--no-wrap" in sys.argv
    if len(args) != 2:
        print(__doc__)
        return 2
    servo_id, new_reset = int(args[0]), int(args[1])

    anchor_path = PACKS / ANCHOR / "action.json"
    if not anchor_path.exists():
        print(f"FAILED: {anchor_path} not found -- packages are field data")
        return 1
    anchor = load(anchor_path)
    idx = positions(anchor.get("steps", []), servo_id)
    if len(idx) != 1:
        print(f"FAILED: {ANCHOR} has {len(idx)} steps for servo {servo_id}; "
              f"expected exactly 1 to anchor the delta against")
        return 1
    old_reset = int(anchor["steps"][idx[0]]["position"])
    delta = new_reset - old_reset

    print(f"servo id {servo_id}")
    print(f"  之前的复位动作 (={ANCHOR}) : {old_reset}")
    print(f"  新的回正参数               : {new_reset}")
    print(f"  整体偏移 delta             : {delta:+d}")
    print(f"  回绕                         : "
          f"{'关闭 (--no-wrap)' if no_wrap else f'开启, 周期 {WRAP_PERIOD}, 范围 {FLOOR}..{CEIL}'}")
    print()

    if not PACKS.is_dir():
        print(f"FAILED: {PACKS} is not a directory")
        return 1

    referenced = referenced_paths()
    mapping, wrapped, out_of_range, changes = {}, [], [], []

    for path in sorted(PACKS.glob("*/action.json")):
        text = path.read_text(encoding="utf-8")
        data = load(path)
        steps = data.get("steps", [])
        seq_new, touched = [], []
        for i in positions(steps, servo_id):
            old = int(steps[i]["position"])
            shifted = old + delta
            if no_wrap:
                new = shifted
                if not FLOOR <= new <= CEIL:
                    out_of_range.append((path.parent.name, i, old, new))
            else:
                new = wrap(shifted)
                if new != shifted:
                    wrapped.append((path.parent.name, old, shifted, new))
            mapping.setdefault(old, set()).add(new)
            seq_new.append(new)
            if new != old:
                steps[i]["position"] = new
                touched.append((i, old, new))
        if touched:
            changes.append({"path": path, "data": data, "text": text,
                            "seq_new": seq_new, "touched": touched,
                            "pack": path.parent.name})

    if not changes:
        print(f"没有任何包里的 servo {servo_id} 需要改动 "
              f"(delta={delta:+d})  —— 没什么可做的。")
        return 0

    print(f"=== 每个包的 id{servo_id} 位姿（改后）===")
    touched_names = {c["pack"] for c in changes}
    for c in changes:
        flag = "" if (referenced is None or c["pack"] in referenced) else "   [不在 catalog，路线不加载]"
        print("  %-26s %s%s" % (c["pack"], c["seq_new"], flag))
    idle = [p.parent.name for p in sorted(PACKS.glob("*/action.json"))
            if p.parent.name not in touched_names]
    if idle:
        print("  (无此舵机步骤: %s)" % ", ".join(idle))

    print("\n=== 数值映射 ===")
    for old in sorted(mapping):
        for new in sorted(mapping[old]):
            if old == old_reset and new == new_reset:
                mark = "   <- 复位点，必须等于新回正值 ✓"
            elif new != old + delta:
                mark = "   <- 回绕"
            else:
                mark = ""
            print("  %4d -> %4d%s" % (old, new, mark))

    if wrapped:
        print("\n⚠ 回绕的位姿：")
        for pack, old, shifted, new in wrapped:
            print("  %-26s %4d %+d = %4d -> 回绕成 %4d" % (pack, old, delta, shifted, new))
        print("  这只在「数值大=往后倒、数值小=往前倒，位置是周期 %d 的圆」成立时才对。" % WRAP_PERIOD)
        print("  若某个回绕位姿上台架后停在了错误的一端，说明周期不是 2000 ——")
        print("  改脚本里的 WRAP_PERIOD 重跑，别手改包。")

    if out_of_range:
        print("\n✗ 越界（未回绕，包会编译失败）：")
        for pack, i, old, new in out_of_range:
            print("  %-26s steps[%d] %d -> %d" % (pack, i, old, new))
        print(f"  这些值在 {FLOOR}..{CEIL} 之外，pickup_action.py 编译期就会拒掉整个包。")
        return 1

    total = sum(len(c["touched"]) for c in changes)
    print(f"\n受影响: {len(changes)} 个包 / {total} 条 SERVO 步骤")

    # Build every new text and prove it before anything is written: the result
    # must parse, must equal the structure we intended, and must differ from the
    # original in exactly the position tokens.
    for c in changes:
        new_text, seen = rewrite_positions(c["text"], servo_id, c["seq_new"])
        if seen != len(c["seq_new"]):
            print(f"FAILED: {c['pack']}: matched {seen} position tokens, "
                  f"expected {len(c['seq_new'])}; nothing written")
            return 1
        try:
            reparsed = json.loads(new_text)
        except ValueError as exc:
            print(f"FAILED: {c['pack']}: rewrite does not parse ({exc}); nothing written")
            return 1
        if reparsed != c["data"]:
            print(f"FAILED: {c['pack']}: rewrite does not reproduce the intended "
                  f"structure; nothing written")
            return 1
        c["new_text"] = new_text

    if not apply_:
        print("\n[DRY RUN] 未写入。确认后加 --apply。")
        return 0

    BACKUPS.mkdir(exist_ok=True)
    stamp = time.strftime("%Y%m%d-%H%M%S")
    backup = BACKUPS / f"retarget-servo{servo_id}-{stamp}.tar"
    rels = [str(c["path"].relative_to(ROOT)) for c in changes]
    rc = subprocess.run(["tar", "cf", str(backup), *rels], cwd=ROOT).returncode
    print(f"[backup] rc={rc} {backup}")
    if rc != 0:
        print("FAILED: backup did not succeed; nothing written")
        return 1

    for c in changes:
        c["path"].write_text(c["new_text"], encoding="utf-8")
    print(f"[write] {len(changes)} 个包已更新")

    after = load(anchor_path)
    got = int(after["steps"][positions(after.get("steps", []), servo_id)[0]]["position"])
    ok = got == new_reset
    print(f"[verify] {ANCHOR} 的 id{servo_id} 现在是 {got} "
          f"({'✓ 等于新回正值' if ok else '✗ 不等于 ' + str(new_reset)})")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
