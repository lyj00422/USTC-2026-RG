"""Fingerprint the runtime files a code deploy would touch, so the push is a diff.

Run ON the Pi via _pi_run_file.py.  Read-only.

Why per-file md5 rather than "push the manifest and see": `config/route_v2.yaml`
on the Pi is HAND-TUNED on the field and the handoff doc says not to overwrite it
wholesale.  Comparing first is what turns a blind overwrite into a deliberate
one -- if that yaml differs from the repo's by more than the keys this deploy
adds, the difference is somebody's field tuning and has to be preserved.
"""
import hashlib
import os

ROOT = "/home/pi/robogame-runtime"
FILES = (
    "run_route_v2.py",
    "route_v2/state_machine.py",
    "route_v2/config.py",
    "config/route_v2.yaml",
    "config/runtime.yaml",
    "rg_runtime/transports.py",
    "rg_runtime/chassis_link.py",
    "rg_runtime/app_support.py",
    "control_hub/server.py",
    "tests/test_route_inventory_loop.py",
    "tests/test_run_route_v2.py",
    "tests/test_socket_transport.py",
)

for rel in FILES:
    path = os.path.join(ROOT, rel)
    try:
        with open(path, "rb") as handle:
            raw = handle.read()
    except OSError as exc:
        print("%-38s MISSING %s" % (rel, exc))
        continue
    # Normalise exactly as _pi_push_files.py does before it hashes the local file,
    # so the two numbers are comparable.
    print("%-38s %s  %d bytes" % (rel, hashlib.md5(raw.replace(b"\r\n", b"\n")).hexdigest(),
                                  len(raw)))
