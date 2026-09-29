"""Push a list of text files to the Pi, backing up first and verifying after.

Why this exists rather than a tarball: `_pi_put_file.py` and `_pi_put_big.py` both
normalise CRLF to LF (correct for .py and .json on the Pi, fatal for a tarball),
and `_pi_push_runtime.py` would `cp -a` the whole tree, which is the 262 MB
whole-venv backup that filled the card.  This touches exactly the named files.

Verification is not optional: `_pi_put_big.py`'s own docstring records a push that
reported failure, left the OLD file in place, and had a follow-up check silently
validate stale code.  Every file is re-hashed on the Pi and compared.

Usage:
    python _pi_push_files.py <manifest.json>

manifest.json: {"root": "<repo>", "remote": "<pi runtime dir>",
                "backup": "<pi backup tar path>",
                "files": [["<local rel>", "<remote rel>"], ...]}
"""
import base64
import hashlib
import json
import os
import sys

import paramiko

HOST = os.environ.get("RG_PI_HOST", "172.20.10.11")
USER = os.environ.get("RG_PI_USER", "pi")
CHUNK = 60000


def connect():
    client = paramiko.SSHClient()
    client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    client.connect(HOST, username=USER, password=os.environ["RG_PI_PW"],
                   timeout=15, allow_agent=False, look_for_keys=False)
    return client


def run(client, command, timeout=180.0):
    _in, out, err = client.exec_command(command, timeout=timeout)
    rc = out.channel.recv_exit_status()
    return rc, out.read().decode("utf-8", "replace"), err.read().decode("utf-8", "replace")


def push(client, data: bytes, remote: str) -> None:
    packed = base64.b64encode(data).decode("ascii")
    tmp = f"/tmp/_push_{os.getpid()}.b64"
    for i in range(0, len(packed), CHUNK):
        chunk = packed[i:i + CHUNK]
        op = ">" if i == 0 else ">>"
        rc, _o, e = run(client, f"printf %s '{chunk}' {op} {tmp}")
        if rc != 0:
            raise RuntimeError(f"chunk write failed at {i}: {e.strip()}")
    rc, _o, e = run(client, f"base64 -d {tmp} > {remote} && rm -f {tmp}")
    if rc != 0:
        raise RuntimeError(f"decode failed for {remote}: {e.strip()}")


def main():
    manifest = json.loads(open(sys.argv[1], encoding="utf-8").read())
    root, remote_root = manifest["root"], manifest["remote"]
    files = manifest["files"]

    client = connect()
    try:
        paths = " ".join(f'"{r}"' for _l, r in files)
        backup = manifest["backup"]
        rc, _o, e = run(
            client,
            f"cd {remote_root} && tar czf {backup} {paths} 2>/dev/null; "
            f"ls -la {backup}",
        )
        print(f"[backup] rc={rc} {backup}")

        sent = []
        for local_rel, remote_rel in files:
            local = os.path.join(root, local_rel)
            data = open(local, "rb").read().replace(b"\r\n", b"\n")
            push(client, data, f"{remote_root}/{remote_rel}")
            local_md5 = hashlib.md5(data).hexdigest()
            rc, out, _e = run(client, f"md5sum {remote_root}/{remote_rel}")
            remote_md5 = out.split()[0] if rc == 0 and out.split() else "?"
            ok = remote_md5 == local_md5
            sent.append(ok)
            if not ok:
                print(f"  MISMATCH {remote_rel}\n    local  {local_md5}\n    remote {remote_md5}")
        print(f"[push] {sum(sent)}/{len(sent)} verified")
        return 0 if all(sent) else 1
    finally:
        client.close()


if __name__ == "__main__":
    raise SystemExit(main())
