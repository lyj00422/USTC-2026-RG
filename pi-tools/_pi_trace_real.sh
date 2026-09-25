#!/bin/bash
# Run the REAL maintainer script under strace, in the same no-tty systemd
# context the service uses, and report exactly which syscall returns EBUSY.
sudo rfcomm release 0 >/dev/null 2>&1
sleep 1

timeout 15 strace -f -e trace=socket,connect,ioctl -o /tmp/rfcomm-strace.log \
    /bin/bash /usr/local/sbin/robogame-chassis-rfcomm >/tmp/script-out.log 2>&1

echo "=== device after run ==="
ls -l /dev/rfcomm0 2>&1
echo "=== connect log ==="
cat /tmp/robogame-rfcomm-connect.log 2>&1
echo "=== script stdout/stderr ==="
cat /tmp/script-out.log 2>&1

echo "=== strace: every EBUSY ==="
grep -n 'EBUSY' /tmp/rfcomm-strace.log | head -20

echo "=== strace: socket/connect calls (first 30) ==="
grep -nE 'socket\(|connect\(' /tmp/rfcomm-strace.log | head -30
