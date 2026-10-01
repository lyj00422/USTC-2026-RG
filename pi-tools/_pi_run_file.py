"""Run a local Python file on the Pi without SFTP.

The Pi's SFTP server rejects writes (ENOENT on open-for-write) even though the
filesystem is writable, so the file is shipped as base64 over the exec channel.

Usage:
    python _pi_run_file.py <local_path> [remote_workdir] [timeout_s] [-- args...]

Anything after `--` is appended to the remote command line, so a script that
takes parameters can be driven without editing it for every run:

    python _pi_run_file.py _pi_retarget_servo.py /home/pi/robogame-runtime 120 -- 1 1000

Shell quoting round the trip is the fragile part here, so the extra arguments
are quoted with shlex on the way in.
"""

import base64
import os
import shlex
import sys

import paramiko

HOST = os.environ.get("RG_PI_HOST", "172.20.10.11")
USER = os.environ.get("RG_PI_USER", "pi")


def main():
    rest = sys.argv[1:]
    extra = []
    if "--" in rest:
        split = rest.index("--")
        rest, extra = rest[:split], rest[split + 1:]
    if not rest:
        print(__doc__)
        return 2
    local = rest[0]
    workdir = rest[1] if len(rest) > 1 else "/home/pi/robogame-runtime"
    timeout = float(rest[2]) if len(rest) > 2 else 120.0
    name = os.path.basename(local)
    remote = f"/tmp/{name}"
    payload = base64.b64encode(open(local, "rb").read()).decode("ascii")
    tail = (" " + " ".join(shlex.quote(a) for a in extra)) if extra else ""
    command = (
        f"printf '%s' '{payload}' | base64 -d > {remote} && "
        f"cd {workdir} && .venv/bin/python {remote}{tail}"
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
