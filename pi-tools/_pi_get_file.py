"""Read one file back from the Pi over the exec channel.

The mirror of _pi_put_file.py.  Binary-safe: the file is base64'd on the Pi and
decoded here, so no line-ending translation is applied.

Usage:
    python _pi_get_file.py <remote_path> <local_path>
"""

import base64
import os
import sys

import paramiko

HOST = os.environ.get("RG_PI_HOST", "172.20.10.11")
USER = os.environ.get("RG_PI_USER", "pi")


def main():
    remote, local = sys.argv[1], sys.argv[2]
    client = paramiko.SSHClient()
    client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    client.connect(HOST, username=USER, password=os.environ["RG_PI_PW"],
                   timeout=15, allow_agent=False, look_for_keys=False)
    try:
        _stdin, stdout, stderr = client.exec_command(f"base64 -w0 {remote}", timeout=120)
        payload = stdout.read().decode("ascii")
        error = stderr.read().decode("utf-8", "replace")
        rc = stdout.channel.recv_exit_status()
    finally:
        client.close()
    if rc != 0:
        print(f"read failed ({rc}): {error}")
        return rc
    data = base64.b64decode(payload)
    with open(local, "wb") as handle:
        handle.write(data)
    print(f"[get {remote} -> {local}, {len(data)} bytes]")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
