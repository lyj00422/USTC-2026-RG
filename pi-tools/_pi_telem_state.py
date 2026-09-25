"""Summarise a route-v2 telemetry JSONL: where the run got to, and how the
line following actually behaved.  Usage: _pi_telem_state.py <file> [file2...]
"""
import json
import sys
from collections import Counter


def load(path):
    rows = []
    with open(path, encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if line:
                try:
                    rows.append(json.loads(line))
                except json.JSONDecodeError:
                    pass
    return rows


def summarise(path):
    rows = load(path)
    if not rows:
        print(f"{path}: EMPTY")
        return
    print(f"=== {path} ===")
    print(f"ticks={len(rows)}  t={rows[0]['t']:.2f}..{rows[-1]['t']:.2f}s")

    seen = []
    for r in rows:
        if not seen or seen[-1] != r["state"]:
            seen.append(r["state"])
    print("state path: " + " -> ".join(seen))

    print("mask histogram (top 8):")
    for mask, n in Counter(r["mask"] for r in rows).most_common(8):
        print(f"   0x{mask:02X} ({mask:08b})  x{n}")

    errs = [r["line_error"] for r in rows if r.get("line_error") is not None]
    if errs:
        print(f"line_error: n={len(errs)} min={min(errs):.3f} max={max(errs):.3f} "
              f"mean={sum(errs)/len(errs):+.3f}")
    else:
        print("line_error: never produced (None on every tick)")

    vels = [(r["vy"], r["wz"]) for r in rows if r.get("vy") is not None]
    if vels:
        vys = [v[0] for v in vels]
        wzs = [v[1] for v in vels]
        sat = sum(1 for v in vys if abs(v) >= 19)
        print(f"vy: min={min(vys)} max={max(vys)} mean={sum(vys)/len(vys):+.1f} "
              f"| saturated(>=19) {sat}/{len(vys)}")
        print(f"wz: min={min(wzs)} max={max(wzs)}")

    issued = [r.get("issued") for r in rows if r.get("issued")]
    print(f"issued commands: {len(issued)}")
    for cmd, n in Counter(issued).most_common(6):
        print(f"   {cmd}  x{n}")
    print(f"last 3 issued: {issued[-3:]}")
    print()


for arg in sys.argv[1:]:
    summarise(arg)
