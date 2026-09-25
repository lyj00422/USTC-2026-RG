#!/bin/bash
# RoboGame JDY-31 chassis RFCOMM link maintainer.
#
# NOTE: deliberately does NOT call `bluetoothctl connect`. The JDY-31 advertises
# Headset / Audio Sink / AVRCP / PBAP alongside SPP; bluetoothctl tries every
# profile, all of the non-SPP ones fail, and it reports br-connection-refused
# even though SPP itself is healthy. `rfcomm connect` brings the ACL up on its
# own and is the only thing we need. Channel is confirmed via SDP ("JL_SPP").
set -u

RUNTIME_CONFIG=/home/pi/robogame-runtime/config/runtime.yaml
MAC=$(awk '/^chassis:/{x=1;next} x&&/^[^ ]/{x=0} x&&/bluetooth_mac:/{print $2;exit}' "$RUNTIME_CONFIG")
CHANNEL=$(awk '/^chassis:/{x=1;next} x&&/^[^ ]/{x=0} x&&/spp_channel:/{print $2;exit}' "$RUNTIME_CONFIG")
MAC=${MAC:-6E:53:BD:74:00:A7}
CHANNEL=${CHANNEL:-1}
DEV=/dev/rfcomm0
LINK=/dev/robogame-chassis

cleanup() {
    rm -f "$LINK" 2>/dev/null || true
    /usr/bin/rfcomm release 0 >/dev/null 2>&1 || true
    # An older helper could create a regular /dev/rfcomm0 via shell redirection
    # when the kernel device did not exist. Remove only that invalid node so the
    # RFCOMM TTY driver can create the real character device.
    if [ -e "$DEV" ] && [ ! -c "$DEV" ]; then
        rm -f "$DEV"
    fi
}

# TERM/INT must *exit*, otherwise systemctl stop blocks until TimeoutStopSec
# because the loop simply resumes after the handler returns.
trap 'cleanup' EXIT
trap 'exit 0' INT TERM

while :; do
    cleanup
    /usr/bin/rfcomm connect 0 "$MAC" "$CHANNEL" >/tmp/robogame-rfcomm-connect.log 2>&1 &
    RFCOMM_PID=$!
    for _ in {1..50}; do
        [ -e "$DEV" ] && break
        sleep 0.2
    done
    if [ -e "$DEV" ]; then
        chgrp dialout "$DEV" 2>/dev/null || true
        chmod 0660 "$DEV" 2>/dev/null || true
        ln -sfn "$DEV" "$LINK"
    fi
    wait "$RFCOMM_PID" || true
    sleep 2
done
