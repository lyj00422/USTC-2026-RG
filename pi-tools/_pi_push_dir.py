"""Push one local directory to a directory on the Pi, with a backup first.

`_pi_push_runtime.py` deliberately refuses to touch `data/` (it is live runtime
state the Pi writes itself), so deploying the action packages needs its own
path.  This is that path, generalised: same transport (one tar.gz over the exec
channel, base64 chunked, because SFTP writes are rejected on this Pi), same
backup-first rule, but the local and remote directories are both arguments.

Text files are normalised to LF on the way out -- a Windows checkout's CRLF
breaks shell-side parsing on the Pi.  Binary files (the camera JPEGs) pass
through untouched.

Usage:
    python _pi_push_dir.py <local_dir> <remote_dir> [--dry-run]

Invoke from PowerShell, not Git Bash (MSYS rewrites /home/pi/...).
"""

import argparse
import base64
import io
import os
import sys
import tarfile
import time

import paramiko

HOST = os.environ.get("RG_PI_HOST", "172.20.10.11")
USER = os.environ.get("RG_PI_USER", "pi")
CHUNK = 60000  # base64 chars per exec call


def build_tarball(local_dir: str):
    """Return (tar.gz bytes, [relative paths]). Text files get LF endings."""
    buf = io.BytesIO()
    names = []
    with tarfile.open(fileobj=buf, mode="w:gz") as tar:
        for root, dirs, files in os.walk(local_dir):
            dirs[:] = sorted(dirs)
            for name in sorted(files):
                full = os.path.join(root, name)
                rel = os.path.relpath(full, local_dir).replace(os.sep, "/")
                with open(full, "rb") as handle:
                    blob = handle.read()
                if b"\x00" not in blob:
                    blob = blob.replace(b"\r\n", b"\n")
                info = tarfile.TarInfo(rel)
                info.size = len(blob)
                info.mtime = int(os.path.getmtime(full))
                info.mode = 0o644
                tar.addfile(info, io.BytesIO(blob))
                names.append(rel)
    return buf.getvalue(), names


def run(client, command, timeout=300):
    _stdin, stdout, stderr = client.exec_command(command, timeout=timeout)
    out = stdout.read().decode("utf-8", "replace")
    err = stderr.read().decode("utf-8", "replace")
    rc = stdout.channel.recv_exit_status()
    return rc, out, err


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("local_dir")
    parser.add_argument("remote_dir")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    payload, names = build_tarball(args.local_dir)
    packed = base64.b64encode(payload).decode("ascii")
    print(f"[build] {len(names)} files, {len(payload)} bytes "
          f"({len(packed)} base64 chars)")

    if args.dry_run:
        for rel in names:
            print("  would push", rel)
        return 0

    client = paramiko.SSHClient()
    client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    client.connect(HOST, username=USER, password=os.environ["RG_PI_PW"],
                   timeout=15, allow_agent=False, look_for_keys=False)
    rc = 0
    try:
        stamp = time.strftime("%Y%m%d-%H%M%S")
        bak = f"{args.remote_dir}.bak-{stamp}"
        # `cp -a` of the directory itself, so the backup is one `mv` from being
        # restored.  Strip any trailing slash first: `cp -a dir/ dest` would
        # copy the CONTENTS into dest instead of the directory.
        source = args.remote_dir.rstrip("/")
        rc, out, err = run(
            client,
            f"if [ -d {source} ]; then cp -a {source} {bak} && echo BACKED_UP; "
            f"else echo NO_EXISTING; fi")
        print(f"[backup] {bak} {out.strip()} {err.strip()}")
        if rc != 0 or "BACKED_UP" not in out + err:
            print("[backup] FAILED -- refusing to push")
            return rc if rc else 1

        tmp = f"/tmp/rgpushdir-{stamp}.b64"
        run(client, f"rm -f {tmp} && : > {tmp}")
        for offset in range(0, len(packed), CHUNK):
            chunk = packed[offset:offset + CHUNK]
            rc, _out, err = run(client, f"printf '%s' '{chunk}' >> {tmp}")
            if rc != 0:
                print(f"[push] chunk at {offset} failed: {err.strip()}")
                return rc
            done = min(offset + CHUNK, len(packed))
            print(f"[push] {done}/{len(packed)}", end="\r")
        print()

        rc, out, err = run(
            client,
            f"mkdir -p {args.remote_dir} && "
            f"base64 -d {tmp} | tar xzf - -C {args.remote_dir} && "
            f"rm -f {tmp} && echo EXTRACTED")
        print(f"[extract] {out.strip()} {err.strip()}")
        if rc != 0 or "EXTRACTED" not in out:
            print("[extract] FAILED -- the backup is next to the target dir")
            return rc if rc else 1

        rc, out, err = run(
            client, f"find {args.remote_dir} -type f | wc -l")
        print(f"[verify] remote file count: {out.strip()}")
    finally:
        client.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
