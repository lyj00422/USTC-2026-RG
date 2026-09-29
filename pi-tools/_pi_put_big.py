"""Write one LARGE local file to the Pi over the exec channel, in chunks.

`_pi_put_file.py` sends the whole base64 payload as a single
`printf '%s' '<payload>'` argument.  The Pi's shell rejects that with
`Argument list too long` once the file is bigger than roughly 70 KB, which bit on
`run_route_v2.py` (2026-09-27, ~95 KB): the push reported `exit=1`, the Pi kept
the OLD file, and the verification that followed silently validated stale code.
**Always re-hash after a push.**

This appends the payload in CHUNK-sized pieces instead -- the same transport and
the same chunk size `_pi_push_runtime.py` uses for whole-runtime tarballs, minus
the tarball, so it touches exactly one file on the Pi rather than the whole tree.

SFTP is not an option: SFTP writes are broken on this Pi.

Usage:
    python _pi_put_big.py <local_path> <remote_path>
"""

import base64
import os
import sys

import paramiko

HOST = os.environ.get("RG_PI_HOST", "172.20.10.11")
USER = os.environ.get("RG_PI_USER", "pi")
# Base64 chars per exec call.  Matches _pi_push_runtime.py:CHUNK.
CHUNK = 60000


def main() -> int:
    local, remote = sys.argv[1], sys.argv[2]
    data = open(local, "rb").read()
    if not os.environ.get("RG_PI_PUT_BINARY"):
        # The Pi is Linux: a Windows checkout's CRLF breaks shell-side parsing
        # (the RFCOMM maintainer read "6E:53:...:A7\r" and could not connect).
        data = data.replace(b"\r\n", b"\n")
    packed = base64.b64encode(data).decode("ascii")
    tmp = f"/tmp/_pi_put_big_{os.getpid()}.b64"
    print(f"[put-big] {local} -> {remote}  ({len(data)} bytes, "
          f"{len(packed)} base64 chars)", flush=True)

    client = paramiko.SSHClient()
    client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    client.connect(HOST, username=USER, password=os.environ["RG_PI_PW"],
                   timeout=15, allow_agent=False, look_for_keys=False)

    def run(command: str, timeout: float = 120.0):
        _stdin, stdout, stderr = client.exec_command(command, timeout=timeout)
        rc = stdout.channel.recv_exit_status()
        return (rc, stdout.read().decode("utf-8", "replace"),
                stderr.read().decode("utf-8", "replace"))

    try:
        run(f"rm -f {tmp}")
        for offset in range(0, len(packed), CHUNK):
            chunk = packed[offset:offset + CHUNK]
            rc, _out, err = run(f"printf '%s' '{chunk}' >> {tmp}")
            if rc != 0:
                print(f"[put-big] chunk at {offset} FAILED: {err.strip()}")
                return 1
            print(f"[put-big] {min(offset + CHUNK, len(packed))}/{len(packed)}",
                  flush=True)
        rc, out, err = run(f"base64 -d {tmp} > {remote} && rm -f {tmp} && "
                           f"wc -c {remote}")
        print(out, end="")
        if rc != 0:
            print(f"[put-big] decode FAILED: {err.strip()}")
            return rc
        return 0
    finally:
        client.close()


if __name__ == "__main__":
    raise SystemExit(main())
