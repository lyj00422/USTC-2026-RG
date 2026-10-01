"""Tar the live action-package tree on the Pi so it can be fetched as one file.

Run ON the Pi via _pi_run_file.py, then fetch with _pi_get_binary.py:

    python pi-tools\\_pi_run_file.py pi-tools\\_pi_tar_actions.py
    python pi-tools\\_pi_get_binary.py /tmp/route_v2_actions.tgz --dest local-materials\\pi-live-20261001

Read-only against the packages themselves: `tar` never writes into them, and the
tarball goes to /tmp.  Pulling the whole tree (rather than the handful of files a
deploy touches) is what makes it possible to prove that every role still matches
the tree the edit was built from -- a package edited on the field after the merge
would otherwise go unnoticed.
"""
import os
import subprocess

ROOT = "/home/pi/robogame-runtime/data"
OUT = "/tmp/route_v2_actions.tgz"

result = subprocess.run(
    ["tar", "-czf", OUT, "-C", ROOT, "route_v2_actions"],
    capture_output=True, text=True)
print("tar rc=%d %s%s" % (result.returncode, result.stdout, result.stderr))
print("size=%d bytes" % os.path.getsize(OUT))
