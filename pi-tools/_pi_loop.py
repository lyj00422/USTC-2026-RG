"""What BUILD_AREA was thinking, from the telemetry's `loop` payload.

Run ON the Pi via _pi_run_file.py.  Reads the newest run.

BUILD_AREA's job is to slide right to the right place and then act.  Where it
ends up is decided by two counters in `LoopContext` that nothing recorded before
2026-09-30 -- so "it built on the empty floor instead of onto the second
building" had no evidence behind it at all.

Prints one line per CHANGE of the interesting values, per visit, so the whole
decision reads as a short timeline rather than 20 rows a second.
"""
import glob
import json
import os

TAG = "logs/route_v2_*.jsonl"
KEYS = ("cap_count", "building_count", "blobs_passed", "slide_exhausted",
        "plan", "purple", "orange", "left_slot", "right_slot", "suction_slot")


def main() -> None:
    hits = [p for p in glob.glob(TAG) if not p.endswith(".out")]
    if not hits:
        print("NO_LOG")
        return
    path = max(hits, key=os.path.getmtime)
    print(f"log: {os.path.basename(path)}\n")

    visits = []
    previous_state = None
    with open(path, encoding="utf-8") as handle:
        for line in handle:
            if not line.strip():
                continue
            d = json.loads(line)
            state = d.get("state")
            if state not in ("BUILD_AREA", "BUILD_ACTION"):
                previous_state = state
                continue
            loop = d.get("loop")
            if loop is None:
                continue
            if not visits or visits[-1]["state"] != state or previous_state not in (
                    "BUILD_AREA", "BUILD_ACTION"):
                visits.append({"state": state, "t0": d["t"], "t1": d["t"],
                               "rows": [], "last": None})
            visit = visits[-1]
            visit["t1"] = d["t"]
            key = tuple(str(loop.get(k)) for k in KEYS)
            if visit["last"] != key:
                visit["last"] = key
                visit["rows"].append((d["t"], loop))
            previous_state = state

    for index, visit in enumerate(visits, 1):
        print(f"=== visit {index}: {visit['state']}  "
              f"t={visit['t0']:.1f}..{visit['t1']:.1f} "
              f"({visit['t1'] - visit['t0']:.1f} s)")
        for when, loop in visit["rows"]:
            print(f"   t={when:8.1f}  cap={loop.get('cap_count')} "
                  f"bldg={loop.get('building_count')} "
                  f"passed={loop.get('blobs_passed')} "
                  f"exhausted={loop.get('slide_exhausted')} "
                  f"plan={loop.get('plan')}")
            print(f"                 purple={loop.get('purple')} "
                  f"orange={loop.get('orange')} "
                  f"slots L/R/S={loop.get('left_slot')}/{loop.get('right_slot')}"
                  f"/{loop.get('suction_slot')}")
        print()


main()
