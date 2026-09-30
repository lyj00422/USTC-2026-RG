"""Every servo command the route sent to the arm, with what it answered.

Run ON the Pi via _pi_run_file.py.  Reads `logs/arm_route.jsonl`, which
`ArmSession._record` writes (one JSONL line per TX and per RX) -- and which
nothing passed a path to until 2026-09-30, so no earlier run has one.

Written to answer 「大臂偶尔不动」, which has exactly three explanations and this
separates them:

  * the command was never sent        -> no TX line at all for that step
  * the command is a no-op            -> `delta 0`: told to go where it already is
  * sent, acked, and the arm still
    did not move                      -> clean TX + fast ACK, so the fault is on
                                         the arm side (supply, stall, mechanics)

Two derived numbers are worth as much as the above:

  * **ack latency** -- how long the board took to answer.  A step that is much
    slower than its neighbours is the arm being busy or wedged.
  * **gap since the previous command** -- compared against the previous
    command's own `time_ms`.  A gap SHORTER than that means the next joint was
    commanded before the previous one finished travelling.
"""
import glob
import json
import os

LOG = "logs/arm_route.jsonl"


def main() -> None:
    hits = glob.glob(LOG)
    if not hits:
        print("no arm log")
        return
    path = max(hits, key=os.path.getmtime)
    print(f"log: {path}")

    entries = []
    with open(path, encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if line:
                try:
                    entries.append(json.loads(line))
                except ValueError:
                    pass
    print(f"{len(entries)} records\n")
    if not entries:
        return

    t0 = entries[0]["timestamp_ns"]
    last_pos = {}
    last_time_ms = None
    pending = None            # (id, pos, time_ms, t_sent_s)
    sent_at = None
    rows = []
    acks = []

    for record in entries:
        when_s = (record["timestamp_ns"] - t0) / 1e9
        raw = record.get("raw", "")
        if record["direction"] == "tx" and raw.startswith("ARM,SERVO,"):
            parts = raw.split(",")
            sid, pos, time_ms = int(parts[2]), int(parts[3]), int(parts[4])
            previous = last_pos.get(sid)
            delta = None if previous is None else pos - previous
            gap = None if sent_at is None else when_s - sent_at
            rows.append({
                "t": when_s, "id": sid, "pos": pos, "time_ms": time_ms,
                "delta": delta, "gap": gap, "prev_time_ms": last_time_ms,
                "ack": None,
            })
            last_pos[sid] = pos
            last_time_ms = time_ms
            sent_at = when_s
            pending = rows[-1]
        elif record["direction"] == "rx" and raw == "ACK,SERVO" and pending is not None:
            latency = when_s - pending["t"]
            pending["ack"] = latency
            acks.append(latency)
            pending = None

    print("=== every servo command sent")
    for row in rows:
        mark = ""
        if row["delta"] == 0:
            mark = "   <== NO-OP (already there)"
        elif row["id"] == 1:
            mark = "   <== 大臂"
        if row["ack"] is None:
            mark += "   <== NO ACK RECORDED"
        elif row["ack"] > 0.25:
            mark += f"   <== SLOW ACK {row['ack'] * 1000:.0f} ms"
        if row["gap"] is not None and row["prev_time_ms"] is not None \
                and row["gap"] < row["prev_time_ms"] / 1000.0:
            mark += (f"   <== sent {row['gap'] * 1000:.0f} ms after the previous,"
                     f" which asked for {row['prev_time_ms']} ms of travel")
        print(f"  t={row['t']:8.3f}  id{row['id']} -> {row['pos']:<5} "
              f"time_ms={row['time_ms']:<4} delta={str(row['delta']):>5} "
              f"ack={('%.3f' % row['ack']) if row['ack'] is not None else '  -- '}"
              f"{mark}")

    if acks:
        acks_sorted = sorted(acks)
        print(f"\n=== ack latency: n={len(acks)}  min={min(acks) * 1000:.0f} ms  "
              f"median={acks_sorted[len(acks_sorted) // 2] * 1000:.0f} ms  "
              f"max={max(acks) * 1000:.0f} ms")
        missing = sum(1 for row in rows if row["ack"] is None)
        print(f"    commands with no ACK in the log: {missing} of {len(rows)}")


main()
