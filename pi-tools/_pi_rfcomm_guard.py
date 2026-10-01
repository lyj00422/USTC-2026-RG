"""What does the RFCOMM maintainer think the transport is?

Run ON the Pi via _pi_run_file.py.

`/usr/local/sbin/robogame-chassis-rfcomm` stands down only when its awk finds
`transport: <value>` inside the top-level `chassis:` block:

    awk '/^chassis:/{x=1;next} x&&/^[^ ]/{x=0} x&&/transport:/{print $2;exit}'

That is exact about indentation and ordering.  If it comes out EMPTY -- a
reordered block, a different indent, a CRLF, or the key moved -- the guard does
not fire, the service carries on rebuilding /dev/robogame-chassis on its ~14 s
cycle, and it competes with the runtime's socket for the ONE SPP session the
JDY-31 will hand out.  The symptom is exactly "Bluetooth will not connect",
intermittently, with the module paired and advertising the whole time.

Reimplements the same awk here (and a tolerant YAML read) so the two can be
compared, then reports what the service has been logging.
"""
import re
import subprocess
from pathlib import Path

CONFIG = Path("/home/pi/robogame-runtime/config/runtime.yaml")
SCRIPT = Path("/usr/local/sbin/robogame-chassis-rfcomm")

print("=== the guard's own awk, run against the live config ===")
awk = ("/^chassis:/{x=1;next} x&&/^[^ ]/{x=0} x&&/transport:/{print $2;exit}")
done = subprocess.run(["awk", awk, str(CONFIG)], capture_output=True, text=True)
parsed = done.stdout.strip()
print(f"  TRANSPORT = {parsed!r}")
print("  -> guard " + ("FIRES (stands down)" if parsed == "rfcomm_socket"
                       else "DOES NOT FIRE -- maintainer keeps the session!"))

print("\n=== the config's chassis block, verbatim ===")
lines = CONFIG.read_text(encoding="utf-8", errors="replace").splitlines()
for index, line in enumerate(lines):
    if line.startswith("chassis:"):
        for offset in range(index, min(index + 14, len(lines))):
            print(f"  {offset + 1:>4} | {lines[offset]!r}")
        break
else:
    print("  no top-level 'chassis:' line found")

print("\n=== what the maintainer logs ===")
print(subprocess.run(["journalctl", "-u", "robogame-chassis-rfcomm",
                      "-n", "25", "--no-pager"],
                     capture_output=True, text=True).stdout.strip() or "  (empty)")

print("\n=== does the maintainer mention the guard? ===")
text = SCRIPT.read_text(encoding="utf-8", errors="replace")
for number, line in enumerate(text.splitlines(), 1):
    if re.search(r"TRANSPORT|standing down|rfcomm_socket", line):
        print(f"  {number:>4} | {line.rstrip()}")
