"""Read-only Bluetooth state on the Pi: adapter, the chassis pairing, who is talking.

Run ON the Pi via _pi_run_file.py.

Deliberately does NOT use sudo or btmon -- those prompt for a password over the
exec channel and hang.  Everything here answers without it.  If the deeper trace
is needed (`_pi_bt_diag.sh`), it has to be run by the operator at the console.

What the columns mean for THIS chassis (a JDY-31 SPP module, not BLE):
  Paired/Trusted  remembered by the adapter.  Missing Trusted is the usual reason
                  a reconnect silently does nothing after a power cycle.
  Connected       an ACL link exists right now.
  rfcomm          the bound tty.  Under the r...socket transport there is none BY
                  DESIGN, so its absence is not a fault on its own.
"""
import subprocess

MAC = "6E:53:BD:74:00:A7"


def run(cmd, timeout=20):
    try:
        done = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    except Exception as exc:                                      # noqa: BLE001
        return f"<{type(exc).__name__}: {exc}>"
    return (done.stdout or "") + (done.stderr or "")


print("=== bluetooth service ===")
print(run(["systemctl", "is-active", "bluetooth"]).strip(),
      "| hci:",
      run(["systemctl", "is-active", "hciuart"]).strip())

print("\n=== adapter ===")
show = run(["bluetoothctl", "show"])
for line in show.splitlines():
    if any(k in line for k in ("Controller", "Powered", "Discoverable",
                               "Pairable", "Name", "Class")):
        print("  " + line.strip())

print(f"\n=== chassis {MAC} ===")
info = run(["bluetoothctl", "info", MAC])
if not info.strip() or "not available" in info:
    print("  NOT KNOWN to the adapter (never paired, or pairing was cleared)")
for line in info.splitlines():
    if any(k in line for k in ("Device", "Name", "Alias", "Paired", "Trusted",
                               "Connected", "Blocked", "RSSI")):
        print("  " + line.strip())

print("\n=== rfcomm / tty ===")
print("  /dev/robogame-chassis:",
      "present" if __import__("os").path.exists("/dev/robogame-chassis")
      else "ABSENT (expected under the socket transport)")
print(run(["rfcomm"]).strip() or "  (rfcomm: no bound devices)")

print("\n=== who holds the chassis port lock ===")
print(run(["fuser", "-v", "/run/lock/robogame-chassis.lock"]).strip()
      or "  (nobody)")

print("\n=== bluetoothd recent log ===")
print(run(["journalctl", "-u", "bluetooth", "-n", "15", "--no-pager"]).strip())
