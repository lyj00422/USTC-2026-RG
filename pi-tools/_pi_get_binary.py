"""Fetch files from the Pi over SFTP -- the read counterpart to _pi_put_file.py.

Why this exists next to _pi_get_file.py rather than replacing it: _pi_get_file.py
reads over the exec channel as base64, which is fine for a telemetry log and
wrong for video.  The whole payload is materialised as one Python string and
base64 inflates it by a third, so a 33 MB recording becomes a ~45 MB string
before anything is written.  SFTP *reads* are not affected by the write-side
bug this Pi has (ENOENT on open-for-write -- see _pi_put_file.py), and they
stream, so large files are not a problem.

Usage:
    python _pi_get_binary.py <remote_path> [<remote_path> ...] [--dest <local_dir>]

    --dest defaults to the current directory; each file keeps its basename.

Example:
    python _pi_get_binary.py /home/pi/robogame-runtime/data/control_hub/recordings --dest ..\recordings
"""

import os
import sys
import time

import paramiko

HOST = os.environ.get("RG_PI_HOST", "172.20.10.11")
USER = os.environ.get("RG_PI_USER", "pi")


def main():
    args = sys.argv[1:]
    dest = "."
    if "--dest" in args:
        i = args.index("--dest")
        dest = args[i + 1]
        args = args[:i] + args[i + 2:]
    if not args:
        print(__doc__)
        return 2
    os.makedirs(dest, exist_ok=True)

    client = paramiko.SSHClient()
    client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    client.connect(HOST, username=USER, password=os.environ["RG_PI_PW"],
                   timeout=15, allow_agent=False, look_for_keys=False)
    rc = 0
    try:
        sftp = client.open_sftp()
        for remote in args:
            if remote.endswith("/"):
                remote = remote[:-1]
            try:
                st = sftp.stat(remote)
            except IOError:
                # A directory: expand it to the files inside, so a whole
                # recordings folder comes back in one call.
                try:
                    names = sftp.listdir(remote)
                except IOError as exc:
                    print(f"  SKIP {remote}: {exc}")
                    rc = 1
                    continue
                for name in names:
                    rc |= _fetch(sftp, f"{remote}/{name}", dest)
                continue
            if st.st_size is not None and not _is_dir(st):
                rc |= _fetch(sftp, remote, dest)
            else:
                for name in sftp.listdir(remote):
                    rc |= _fetch(sftp, f"{remote}/{name}", dest)
    finally:
        client.close()
    return rc


def _is_dir(st):
    import stat as _stat
    return _stat.S_ISDIR(st.st_mode)


def _fetch(sftp, remote, dest):
    name = remote.rsplit("/", 1)[-1]
    local = os.path.join(dest, name)
    size = sftp.stat(remote).st_size
    t0 = time.time()
    written = 0
    # Streamed in chunks: never hold the whole file, and print progress so a
    # long pull is distinguishable from a hang.
    with sftp.open(remote, "rb") as src, open(local, "wb") as dst:
        while True:
            chunk = src.read(1 << 20)
            if not chunk:
                break
            dst.write(chunk)
            written += len(chunk)
    dt = max(1e-6, time.time() - t0)
    ok = "OK " if written == size else "SHORT "
    print(f"  {ok}{name}  {written}/{size} bytes  {dt:.1f}s  ({written/dt/1e6:.1f} MB/s)")
    return 0 if written == size else 1


if __name__ == "__main__":
    raise SystemExit(main())
