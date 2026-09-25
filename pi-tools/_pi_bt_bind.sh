#!/bin/bash
# Bind the JDY-31 chassis SPP link: /dev/rfcomm0 -> /dev/robogame-chassis.
# Channel 1 confirmed via SDP ("JL_SPP"). No bluetoothctl connect: that gates on
# profiles the module does not implement and always reports failure.
MAC=6E:53:BD:74:00:A7
CH=1

echo "=== release stale ==="
sudo rfcomm release 0 2>/dev/null || true
sleep 1

echo "=== start rfcomm connect (detached) ==="
sudo setsid nohup /usr/bin/rfcomm connect 0 "$MAC" "$CH" \
    > /tmp/rfcomm-connect.log 2>&1 < /dev/null &
disown 2>/dev/null || true

for _ in $(seq 1 60); do
    [ -c /dev/rfcomm0 ] && break
    sleep 0.2
done

echo "=== /dev/rfcomm0 ==="
ls -l /dev/rfcomm0 2>&1

if [ -c /dev/rfcomm0 ]; then
    sudo chgrp dialout /dev/rfcomm0 2>/dev/null || true
    sudo chmod 0660 /dev/rfcomm0 2>/dev/null || true
    sudo ln -sfn /dev/rfcomm0 /dev/robogame-chassis
    echo "=== /dev/robogame-chassis ==="
    ls -l /dev/robogame-chassis
else
    echo "FAILED to create /dev/rfcomm0"
    cat /tmp/rfcomm-connect.log
fi

echo "=== rfcomm -a ==="
rfcomm -a 2>&1
echo "=== connect log ==="
cat /tmp/rfcomm-connect.log 2>&1
