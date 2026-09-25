"""Diagnostic: time each stage of the paramiko connect to the Pi."""
import os
import socket
import time

import paramiko

HOST = os.environ.get("RG_PI_HOST", "172.20.10.11")
USER = os.environ.get("RG_PI_USER", "pi")
PW = os.environ["RG_PI_PW"]

print("host", HOST, "user", USER, "pwlen", len(PW))

for attempt in range(5):
    t0 = time.time()
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.settimeout(8)
    try:
        s.connect((HOST, 22))
        banner = s.recv(64)
        print(f"  raw   {attempt}: OK {time.time() - t0:.1f}s {banner.strip()[:30]}")
    except Exception as exc:
        print(f"  raw   {attempt}: FAIL {type(exc).__name__} {time.time() - t0:.1f}s")
    finally:
        s.close()

    t0 = time.time()
    c = paramiko.SSHClient()
    c.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    try:
        c.connect(HOST, username=USER, password=PW, timeout=15,
                  allow_agent=False, look_for_keys=False)
        print(f"  ssh   {attempt}: OK {time.time() - t0:.1f}s")
        c.close()
    except Exception as exc:
        print(f"  ssh   {attempt}: FAIL {type(exc).__name__} {exc} {time.time() - t0:.1f}s")
        try:
            c.close()
        except Exception:
            pass
