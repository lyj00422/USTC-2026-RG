"""What packages did a given historic run use?

Read-only.  Looks at a run's telemetry for anything that identifies the action
packages it loaded, and cross-checks the console event log that was live at the
same time, which does record the recorder's package names.

    python _pi_run_file.py pi-tools\\_pi_probe_run_pkgs.py /home/pi/robogame-runtime \
        -- 20260930_115138
"""
import json
import os
import sys

ROOT = "/home/pi/robogame-runtime"
stamp = sys.argv[1] if len(sys.argv) > 1 else "20260930_115138"

tel = f"{ROOT}/logs/route_v2_full_{stamp}.jsonl"
print(f"=== {tel}")
print("   size:", os.path.getsize(tel) if os.path.exists(tel) else "MISSING")
if os.path.exists(tel):
    with open(tel, encoding="utf-8", errors="replace") as fh:
        first = json.loads(fh.readline())
    keys = sorted(first)
    print(f"   总键数 {len(keys)}")
    hits = [k for k in keys if any(x in k.lower() for x in
                                   ("action", "package", "catalog", "role", "pickup",
                                    "suspend", "objective"))]
    print("   包相关键:", hits)
    for k in hits[:12]:
        print(f"      {k} = {str(first[k])[:120]}")
    # The `suspended` / action metadata column is where the executor names itself.
    with open(tel, encoding="utf-8", errors="replace") as fh:
        seen = set()
        for i, line in enumerate(fh):
            if i > 400:
                break
            try:
                row = json.loads(line)
            except Exception:
                continue
            for k in ("action", "action_metadata", "metadata", "action_result"):
                v = row.get(k)
                if isinstance(v, dict):
                    seen.add(json.dumps(v, ensure_ascii=False)[:200])
            if row.get("action_metadata"):
                seen.add(str(row["action_metadata"])[:200])
        for s in sorted(seen)[:10]:
            print("      action metadata:", s)
print()
print("=== 同期的控制台事件日志 ===")
for name in sorted(os.listdir(f"{ROOT}/logs")):
    if name.startswith("control_hub") and "20260930" in name:
        print("   ", name)
