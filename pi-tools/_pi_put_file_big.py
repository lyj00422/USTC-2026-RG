"""Write one LARGE local file to the Pi over the exec channel, in chunks.

_pi_put_file.py sends the whole payload as a single command argument, which
works until the file is big: Windows caps a command line near 32 KB, so the
base64 of anything over ~24 KB never leaves the laptop (the shell reports
"Argument list too long" on the Pi, or PowerShell fails to even spawn python).

This variant keeps the payload inside Python and appends it to a staging file
on the Pi a chunk at a time, then decodes it in place.  Binary is never
line-ending translated -- the bytes go up as base64 and are decoded remotely.

Usage:
    python _pi_put_file_big.py <local_path> <remote_path>
"""

import base64
import os
import sys

import paramiko

HOST = os.environ.get("RG_PI_HOST", "172.20.10.11")
USER = os.environ.get("RG_PI_USER", "pi")
CHUNK = 40000  # base64 chars per exec call


def main():
    local, remote = sys.argv[1], sys.argv[2]
    payload = base64.b64encode(open(local, "rb").read()).decode("ascii")
    staging = f"/tmp/rgput-{os.getpid()}.b64"

    client = paramiko.SSHClient()
    client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    client.connect(HOST, username=USER, password=os.environ["RG_PI_PW"],
                   timeout=15, allow_agent=False, look_for_keys=False)

    def run(command, timeout=120):
        _stdin, stdout, stderr = client.exec_command(command, timeout=timeout)
        out = stdout.read().decode("utf-8", "replace")
        err = stderr.read().decode("utf-8", "replace")
        return stdout.channel.recv_exit_status(), out, err

    try:
        run(f": > {staging}")
        for offset in range(0, len(payload), CHUNK):
            chunk = payload[offset:offset + CHUNK]
            rc, _out, err = run(f"printf '%s' '{chunk}' >> {staging}")
            if rc != 0:
                print(f"[put-big] chunk at {offset} failed: {err.strip()}")
                return rc
        rc, out, err = run(
            f"mkdir -p $(dirname {remote}) && "
            f"base64 -d {staging} > {remote} && wc -c {remote} && "
            f"md5sum {remote}")
        print(out.strip(), err.strip())
        if rc != 0:
            return rc
    finally:
        run(f": > {staging}")
        client.close()

    import hashlib
    print(f"[put-big] local md5 {hashlib.md5(open(local, 'rb').read()).hexdigest()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
