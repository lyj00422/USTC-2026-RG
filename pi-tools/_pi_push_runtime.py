"""Push the runtime/ deploy set to the Pi over a single SSH connection.

SFTP writes are broken on this Pi (ENOENT), so the payload travels over the
exec channel as base64 -- the same transport _pi_put_file.py uses, except the
whole set goes as one tar.gz instead of one connection per file.

Text files are normalised to LF on the way out: a Windows checkout's CRLF
breaks shell-side parsing on the Pi (the RFCOMM maintainer once read
"6E:53:...:A7\\r" and could not connect).

The remote directory is copied aside before anything is written, so a bad push
is one `mv` away from being undone.

data/ and logs/ are NOT pushed by default -- they are live runtime data that
the Pi writes itself, and overwriting them would destroy field records.  Pass
--include-data to push them anyway.

Usage:
    python _pi_push_runtime.py <local_dir> [--remote DIR] [--include-data]
                               [--dry-run]

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

SKIP_DIRS = {".git", "__pycache__", ".pytest_cache", "logs", "data"}
CHUNK = 60000  # base64 chars per exec call


def build_tarball(local_dir: str, include_data: bool):
    """Return (tar.gz bytes, [relative paths]). Text files get LF endings."""
    skip = set(SKIP_DIRS)
    if include_data:
        skip.discard("data")

    buf = io.BytesIO()
    names = []
    with tarfile.open(fileobj=buf, mode="w:gz") as tar:
        for root, dirs, files in os.walk(local_dir):
            dirs[:] = sorted(d for d in dirs if d not in skip)
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
                if rel.startswith("deploy/") and not rel.endswith((".service", ".rules")):
                    info.mode = 0o755  # shell scripts must stay executable
                tar.addfile(info, io.BytesIO(blob))
                names.append(rel)
    return buf.getvalue(), names


def run(client, command, timeout=120):
    _stdin, stdout, stderr = client.exec_command(command, timeout=timeout)
    out = stdout.read().decode("utf-8", "replace")
    err = stderr.read().decode("utf-8", "replace")
    rc = stdout.channel.recv_exit_status()
    return rc, out, err


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("local_dir")
    parser.add_argument("--remote", default="/home/pi/robogame-runtime")
    parser.add_argument("--include-data", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    payload, names = build_tarball(args.local_dir, args.include_data)
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
        bak = f"{args.remote}.bak-{stamp}"
        # Back up exactly what the push overwrites -- i.e. everything except the
        # live data/ and logs/, which are neither pushed nor touched.  A whole-dir
        # copy fails here: data/ holds root-owned files the pi user cannot read.
        keep = " ".join(f"! -name {d}" for d in sorted(SKIP_DIRS) if d != "logs")
        rc, out, err = run(
            client,
            f"if [ -d {args.remote} ]; then mkdir -p {bak} && "
            f"find {args.remote} -mindepth 1 -maxdepth 1 {keep} "
            f"-exec cp -a {{}} {bak}/ \\; && echo BACKED_UP; else echo NO_EXISTING; fi")
        print(f"[backup] {bak} {out.strip()} {err.strip()}")
        if rc != 0 or "BACKED_UP" not in out + err:
            print("[backup] FAILED -- refusing to push")
            return rc if rc else 1

        tmp = f"/tmp/rgpush-{stamp}.b64"
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
            f"mkdir -p {args.remote} && "
            f"base64 -d {tmp} | tar xzf - -C {args.remote} && "
            f"rm -f {tmp} && echo EXTRACTED")
        print(f"[extract] {out.strip()} {err.strip()}")
        if rc != 0 or "EXTRACTED" not in out:
            print("[extract] FAILED -- the backup is next to the target dir")
            return rc if rc else 1

        rc, out, err = run(
            client,
            f"find {args.remote} -path '*/.git' -prune -o -type f -print | wc -l")
        print(f"[verify] remote file count: {out.strip()}")
        rc, out, err = run(
            client,
            f"grep -E 'seek_line_max_cm|junction_3_distance_cm|pickup_seek_line_vy' "
            f"{args.remote}/config/route_v2.yaml")
        print("[verify] key route params on the Pi:")
        print(out.rstrip())
    finally:
        client.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
