"""Is the JDY-31 advertising, and is anything else competing for the SPP session?

Run ON the Pi via _pi_run_file.py.

Two questions, because they need opposite fixes:

  * NOT SEEN in a scan      -> the chassis side: the module is unpowered, or the
                               chassis is off.  Nothing on the Pi can fix that.
  * SEEN but connect fails  -> the Pi side or the session state: a JDY-31 accepts
                               ONE SPP connection, so a stale session (or the old
                               rfcomm maintainer still rebuilding a tty every
                               ~14 s) makes every new attempt time out exactly
                               like this.

The maintainer check matters because the transport moved to a direct socket and
the tty node it used to build is gone -- if that service is still running it is
now racing the socket for the one session the module will give out.
"""
import subprocess
import time

MAC = "6E:53:BD:74:00:A7"


def run(cmd, timeout=45):
    try:
        done = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    except subprocess.TimeoutExpired:
        return "<TIMEOUT>"
    except Exception as exc:                                      # noqa: BLE001
        return f"<{type(exc).__name__}: {exc}>"
    return ((done.stdout or "") + (done.stderr or "")).strip()


print("=== classic scan (12 s) ===")
scan = run(["bluetoothctl", "--timeout", "12", "scan", "on"])
found = [ln.strip() for ln in scan.splitlines() if MAC.lower() in ln.lower()]
print("  " + (found[0] if found else "JDY-31 NOT SEEN"))
others = [ln.strip() for ln in scan.splitlines()
          if "Device" in ln and MAC.lower() not in ln.lower()]
print(f"  {len(others)} other device line(s) seen")
for line in others[:6]:
    print("    " + line)

print("\n=== anything else holding a Bluetooth session ===")
for pattern in ("rfcomm", "bluetooth"):
    out = run(["pgrep", "-af", pattern])
    rows = [ln for ln in out.splitlines()
            if "_pi_bt_scan" not in ln and "_pi_run_file" not in ln]
    print(f"  pgrep {pattern}:")
    for line in rows[:12]:
        print("    " + line[:120])
    if not rows:
        print("    (none)")

print("\n=== unit files that could rebuild the link ===")
units = run(["systemctl", "list-units", "--all", "--no-pager", "--no-legend",
             "*rfcomm*", "*chassis*", "*robogame*", "*bluetooth*"])
print(units or "  (none)")

print("\n=== previous session state ===")
print(run(["bluetoothctl", "--timeout", "10", "info", MAC]) or "  (no info)")
