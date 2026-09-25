"""Compressed timeline of a route-v2 telemetry run: one line per issued command,
plus the masks seen in the run-up.  Usage: _pi_telem_trace.py <file>
"""
import json
import sys

path = sys.argv[1]
rows = []
with open(path, encoding="utf-8") as fh:
    for line in fh:
        line = line.strip()
        if line:
            try:
                rows.append(json.loads(line))
            except json.JSONDecodeError:
                pass

t0 = rows[0]["t"]
print(f"{'t':>7} {'state':<26} {'mask':>9} {'err':>7} {'vx':>4} {'vy':>4} {'wz':>4}  issued")
print("-" * 100)
prev_state = None
last_issued = None
for r in rows:
    issued = r.get("issued")
    state = r["state"]
    mask = r["mask"]
    err = r.get("line_error")
    # print on state change, on a new issued command, or on an unusual mask
    notable = (
        state != prev_state
        or (issued and issued != last_issued)
        or mask not in (0x80, 0x81)
    )
    if notable:
        vx = r.get("vy")
        print(f"{r['t']-t0:7.2f} {state:<26} 0x{mask:02X} "
              f"{('' if err is None else f'{err:+.3f}'):>7} "
              f"{str(r.get('vx','')):>4} "
              f"{('' if vx is None else str(vx)):>4} "
              f"{('' if r.get('wz') is None else str(r['wz'])):>4}  {issued or ''}")
    if issued:
        last_issued = issued
    prev_state = state
