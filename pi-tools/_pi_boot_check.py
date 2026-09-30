"""Did the Pi reboot, did the chassis lose its counters, around the wedge.

Run ON the Pi via _pi_run_file.py.

Three independent witnesses, because "the car stopped mid-turn" has a power
story and a code story and they look alike in the telemetry:

  * the Pi's own uptime / boot list -- a Pi reboot is a whole-robot power event
    on this build, since the chassis and the arm hang off the same pack;
  * the RFCOMM maintainer's restart count -- it rebuilds /dev/robogame-chassis
    every time the link idles out, and each rebuild is a fresh tty;
  * the chassis's own ENC counters in the first row of each run -- ENC is a
    free-running counter on the chassis MCU, so a power cycle resets it to
    near zero.  A run whose first ENC reading is small started on a chassis
    that had just been powered up.
"""
import glob
import json
import os
import subprocess


def sh(cmd):
    try:
        return subprocess.run(cmd, shell=True, capture_output=True, text=True,
                              timeout=20).stdout.strip()
    except Exception as exc:  # noqa: BLE001
        return f"<{exc}>"


print("=== uptime ===")
print(sh("uptime"))
print(sh("cat /proc/uptime"))
print("=== reboots ===")
print(sh("last -x reboot shutdown 2>&1 | head -8"))
print("=== boot list ===")
print(sh("journalctl --list-boots 2>&1 | tail -6"))
print("=== rfcomm/chassis services ===")
print(sh("systemctl status robogame-chassis-rfcomm.service --no-pager 2>&1 | head -12"))
print("=== chassis node ===")
print(sh("ls -l /dev/robogame-chassis /dev/rfcomm0 2>&1"))

print("=== first ENC reading per run (small == chassis just powered up) ===")
files = sorted(
    (p for p in glob.glob("logs/route_v2_*.jsonl") if not p.endswith(".out")),
    key=os.path.getmtime,
)[-14:]
for path in files:
    first_enc = None
    first_t = None
    with open(path, errors="replace") as handle:
        for line in handle:
            try:
                row = json.loads(line)
            except Exception:
                continue
            if first_t is None:
                first_t = row.get("t")
            if row.get("encoder"):
                first_enc = row["encoder"]
                break
    print("  %-40s t0=%-7s first ENC=%s"
          % (os.path.basename(path), first_t, first_enc))
