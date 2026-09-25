"""Flip route_v2 `vision.calibrated` on the Pi's DEPLOYED config only.

Why this exists as a script: the obvious shell version (`sed -i 's/.../.../'`)
cannot survive the trip through PowerShell -- embedded double quotes get
mangled in native-command argument passing.  Doing it in Python on the Pi side
sidesteps shell quoting entirely and lets us show the exact line before and
after.

Scope: ONLY /home/pi/robogame-runtime/config/route_v2.yaml.  The repo copy in
runtime/config/route_v2.yaml is deliberately left at `false`, so any future
_pi_push_runtime.py reverts this automatically.

It is a FLIP, not a set: run it once to open the gate for a full-route test,
run it again to close it.  It copies the file aside first and prints the line
it changed, so the resulting state is never in doubt.
"""
import re
import shutil
import sys
import time
from pathlib import Path

CFG = Path("/home/pi/robogame-runtime/config/route_v2.yaml")

# Anchored so it matches the real key and NOT the comment on line 5 that reads
# "...while calibrated is false." -- that line has no colon after `calibrated`.
PATTERN = re.compile(r"^([ \t]*)calibrated:([ \t]*)(true|false)[ \t]*$", re.M)


def main():
    text = CFG.read_text(encoding="utf-8")
    match = PATTERN.search(text)
    if match is None:
        print("FAILED: no `calibrated: true|false` key found -- nothing changed")
        return 1

    old = match.group(3)
    new = "false" if old == "true" else "true"

    context = text[:match.start()].count("\n") + 1
    print(f"file    : {CFG}")
    print(f"line    : {context}")
    print(f"before  : calibrated: {old}")
    print(f"after   : calibrated: {new}")

    backup = CFG.with_name(f"{CFG.name}.bak-calib-{time.strftime('%Y%m%d-%H%M%S')}")
    shutil.copy2(CFG, backup)
    print(f"backup  : {backup}")

    patched = text[:match.start()] + f"{match.group(1)}calibrated:{match.group(2)}{new}" + text[match.end():]
    CFG.write_text(patched, encoding="utf-8")

    # Read it back from disk rather than trusting the write: this flag is what
    # stands between a segmented test and an uncalibrated full-route run.
    verify = PATTERN.search(CFG.read_text(encoding="utf-8"))
    got = verify.group(3) if verify else None
    print(f"readback: calibrated: {got}")
    if got != new:
        print("FAILED: readback does not match -- restore from the backup above")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
