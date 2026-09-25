"""Write one local file to the Pi over the exec channel (SFTP writes are broken there).

Usage:
    python _pi_put_file.py <local_path> <remote_path>
"""

import base64
import os
import sys

import paramiko

HOST = os.environ.get("RG_PI_HOST", "172.20.10.11")
USER = os.environ.get("RG_PI_USER", "pi")


def main():
    local, remote = sys.argv[1], sys.argv[2]
    data = open(local, "rb").read()
    if not os.environ.get("RG_PI_PUT_BINARY"):
        # The Pi is Linux: a Windows checkout's CRLF breaks shell-side parsing
        # (the RFCOMM maintainer read "6E:53:...:A7\r" and could not connect).
        data = data.replace(b"\r\n", b"\n")
    payload = base64.b64encode(data).decode("ascii")
    command = (
        f"mkdir -p $(dirname {remote}) && "
        f"printf '%s' '{payload}' | base64 -d > {remote} && "
        f"wc -c {remote}"
    )
    client = paramiko.SSHClient()
    client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    client.connect(HOST, username=USER, password=os.environ["RG_PI_PW"],
                   timeout=15, allow_agent=False, look_for_keys=False)
    try:
        _stdin, stdout, stderr = client.exec_command(command, timeout=60)
        print(stdout.read().decode("utf-8", "replace"), end="")
        print(stderr.read().decode("utf-8", "replace"), end="")
        rc = stdout.channel.recv_exit_status()
    finally:
        client.close()
    print(f"[put {local} -> {remote} exit={rc}]")
    return rc


if __name__ == "__main__":
    raise SystemExit(main())
