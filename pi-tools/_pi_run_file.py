"""Run a local Python file on the Pi without SFTP.

The Pi's SFTP server rejects writes (ENOENT on open-for-write) even though the
filesystem is writable, so the file is shipped as base64 over the exec channel.

Usage:
    python _pi_run_file.py <local_path> [remote_workdir] [timeout_s]
"""

import base64
import os
import sys

import paramiko

HOST = os.environ.get("RG_PI_HOST", "172.20.10.11")
USER = os.environ.get("RG_PI_USER", "pi")


def main():
    local = sys.argv[1]
    workdir = sys.argv[2] if len(sys.argv) > 2 else "/home/pi/robogame-runtime"
    timeout = float(sys.argv[3]) if len(sys.argv) > 3 else 120.0
    name = os.path.basename(local)
    remote = f"/tmp/{name}"
    payload = base64.b64encode(open(local, "rb").read()).decode("ascii")
    command = (
        f"printf '%s' '{payload}' | base64 -d > {remote} && "
        f"cd {workdir} && .venv/bin/python {remote}"
    )

    client = paramiko.SSHClient()
    client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    client.connect(HOST, username=USER, password=os.environ["RG_PI_PW"],
                   timeout=15, allow_agent=False, look_for_keys=False)
    try:
        _stdin, stdout, stderr = client.exec_command(command, timeout=timeout, get_pty=True)
        for stream in (stdout, stderr):
            for line in iter(stream.readline, ""):
                sys.stdout.write(line)
                sys.stdout.flush()
        rc = stdout.channel.recv_exit_status()
    finally:
        client.close()
    print(f"\n[exit={rc}]")
    return rc


if __name__ == "__main__":
    raise SystemExit(main())
