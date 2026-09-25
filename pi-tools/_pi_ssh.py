"""Temporary SSH helper for the RoboGame Pi. Password comes from env RG_PI_PW.

Usage:
    python _pi_ssh.py "command"
    python _pi_ssh.py --timeout 300 "long command"
"""
import os
import sys

import paramiko

HOST = os.environ.get("RG_PI_HOST", "172.20.10.11")
USER = os.environ.get("RG_PI_USER", "pi")


def main():
    args = sys.argv[1:]
    timeout = 60.0
    if args and args[0] == "--timeout":
        timeout = float(args[1])
        args = args[2:]
    command = args[0]

    client = paramiko.SSHClient()
    client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    client.connect(
        HOST,
        username=USER,
        password=os.environ["RG_PI_PW"],
        timeout=15,
        allow_agent=False,
        look_for_keys=False,
    )
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
