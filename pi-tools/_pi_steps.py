"""How long each action-package STEP took inside one state, on the real run.

Run ON the Pi via _pi_run_file.py.  Reads the newest run and reports, for every
state that ran an action package, the dwell at each step_index in order.

This is how the 1 s 大臂 hold is checked on hardware: `ActionPackageExecutor`
advances the index and THEN holds, so a step whose servo was the 大臂 shows up
as a dwell about `arm_move_settle_s` longer than the same package's other servo
steps.  A hold that is configured but not firing looks like a uniformly flat
list, which is exactly what the build packages looked like before this change.
"""
import glob
import json
import os

TAG = "logs/route_v2_j3_*.jsonl"

# The `time_ms` every servo step in these packages asks for, in seconds.  The
# dwell of a step that issues a servo command is at least this.
SERVO_TRAVEL_S = 0.5


def main() -> None:
    hits = [p for p in glob.glob(TAG) if not p.endswith(".out")]
    if not hits:
        print("NO_LOG")
        return
    path = max(hits, key=os.path.getmtime)

    # state -> list of (step_index, first_t, last_t)
    timeline = {}
    order = []
    with open(path, encoding="utf-8") as handle:
        for line in handle:
            if not line.strip():
                continue
            d = json.loads(line)
            action = d.get("pickup_action")
            if not action or not action.get("executed"):
                continue
            ref = action.get("action_ref")
            index = action.get("step_index")
            state = d.get("state")
            key = (state, ref)
            if key not in timeline:
                timeline[key] = []
                order.append(key)
            rows = timeline[key]
            if not rows or rows[-1][0] != index:
                rows.append([index, d["t"], d["t"]])
            else:
                rows[-1][2] = d["t"]

    for state, ref in order:
        rows = timeline[state, ref]
        total = rows[-1][2] - rows[0][1] if rows else 0.0
        print(f"\n=== {state}  {ref}   {len(rows)} steps, {total:.1f} s")
        for index, first, last in rows:
            dwell = last - first
            flag = ""
            if dwell >= SERVO_TRAVEL_S + 0.9:
                flag = "   <== 大臂 hold (%.2f s over the servo's own %.1f s)" % (
                    dwell - SERVO_TRAVEL_S, SERVO_TRAVEL_S)
            print(f"   step {index:>3}  {dwell:6.2f} s{flag}")


main()
