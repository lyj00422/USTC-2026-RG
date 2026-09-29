"""Replay a finished route's telemetry as a timeline of what the car actually did.

Read-only.  Answers "which pickup action ran, in what order, and how did each
end" -- the question you need before changing any action package.

Usage:  python _pi_run_actions.py [telemetry.jsonl]
"""

import json
import sys
from pathlib import Path

ROOT = Path("/home/pi/robogame-runtime")


def newest():
    files = sorted((ROOT / "logs").glob("route_v2_full_*.jsonl"),
                   key=lambda p: p.stat().st_mtime)
    return files[-1] if files else None


def main() -> int:
    path = Path(sys.argv[1]) if len(sys.argv) > 1 else newest()
    if path is None or not path.exists():
        print("no telemetry log found")
        return 1
    print(f"log: {path}")

    events = []          # (t, kind, detail)
    last_state = None
    last_action = None
    last_phase = None
    state_since = None
    seen_states = {}

    for line in path.open(encoding="utf-8", errors="replace"):
        line = line.strip()
        if not line:
            continue
        try:
            row = json.loads(line)
        except ValueError:
            continue
        t = row.get("t")
        state = row.get("state")
        if state != last_state:
            if last_state is not None:
                seen_states[last_state] = seen_states.get(last_state, 0) + (t - state_since)
            events.append((t, "STATE", state))
            last_state, state_since = state, t

        action = row.get("pickup_action")
        if isinstance(action, dict):
            name = action.get("selected_action") or action.get("action_ref")
            if name != last_action:
                events.append((t, "ACTION", f"{name}  ({action.get('step_count')} steps)"))
                last_action = name
        phase = row.get("pickup_phase")
        if phase != last_phase:
            events.append((t, "PHASE", phase))
            last_phase = phase

    jump = next((r.get("t") for r in (json.loads(l) for l in []) ), 0)
    for t, kind, detail in events:
        print(f"  t={t:9.2f}  {kind:7s} {detail}")

    print("\n--- time spent per state ---")
    for state, seconds in sorted(seen_states.items(), key=lambda kv: -kv[1]):
        print(f"  {state:30s} {seconds:8.1f}s")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
