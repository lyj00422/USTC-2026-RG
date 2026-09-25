"""READ-ONLY progress report for the newest full-route run on the Pi.

Finds the newest logs/route_v2_full_*.jsonl, and prints a compact summary: how
many ticks, which states it visited and for how long, whether the purple leg was
actually entered, and the terminal verdict from the matching .out file.

Sends nothing to the chassis or the arm -- it only reads files and pgrep.
"""
import json
import subprocess
import sys
from collections import Counter
from pathlib import Path

ROOT = Path("/home/pi/robogame-runtime")
LOGS = ROOT / "logs"


def newest(pattern):
    files = sorted(LOGS.glob(pattern), key=lambda p: p.stat().st_mtime)
    return files[-1] if files else None


def main():
    telemetry = newest("route_v2_full_*.jsonl")
    if telemetry is None:
        print("no logs/route_v2_full_*.jsonl found")
        return 1
    rows = []
    for line in telemetry.read_text(errors="replace").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            rows.append(json.loads(line))
        except json.JSONDecodeError:
            pass

    print(f"telemetry : {telemetry.name}")
    print(f"ticks     : {len(rows)}")

    runner = subprocess.run(["pgrep", "-af", r"run_route_v[2]"], capture_output=True, text=True)
    alive = runner.stdout.strip()
    print(f"process   : {'RUNNING  ' + alive.splitlines()[-1][:90] if alive else 'NOT RUNNING'}")

    if rows:
        seen = Counter(r.get("state") for r in rows)
        order = []
        for r in rows:
            if r.get("state") not in order:
                order.append(r.get("state"))
        print("states    : " + " -> ".join(str(s) for s in order))
        print("ticks/st  : " + ", ".join(f"{s}={seen[s]}" for s in order))

        last = rows[-1]
        keep = ("state", "phase", "command", "mask", "travel", "lateral", "actual_speed")
        print("last tick : " + json.dumps({k: last.get(k) for k in keep if k in last}, ensure_ascii=False))
        for key in ("pickup_kind", "pickup_reason", "pickup_result", "pickup_action"):
            if key in last:
                print(f"  {key}: {json.dumps(last[key], ensure_ascii=False)[:300]}")
        if "vision" in last:
            print("  vision  : " + json.dumps(last["vision"], ensure_ascii=False)[:400])
        if "purple" in last:
            print("  purple  : " + json.dumps(last["purple"], ensure_ascii=False)[:400])

    out = telemetry.with_suffix("").name.replace("route_v2_full_", "full_") + ".out"
    outf = LOGS / out
    if outf.is_file():
        text = outf.read_text(errors="replace")
        print(f"stdout    : {outf.name} ({len(text)} bytes)")
        tail = [ln for ln in text.splitlines() if ln.strip()][-12:]
        for ln in tail:
            print(f"    {ln}")
        for marker in ("FAULT_SAFE", "FINISHED"):
            if marker in text:
                print(f"  !! contains {marker}")
    else:
        print(f"stdout    : {out} missing")
    return 0


if __name__ == "__main__":
    sys.exit(main())
