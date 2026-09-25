#!/bin/bash
# Reproduce the service loop body under a no-tty context to isolate the EBUSY.
MAC=6E:53:BD:74:00:A7
CH=1
DEV=/dev/rfcomm0

try() {
    local label="$1"; shift
    echo "### $label"
    "$@" &
    local p=$!
    for _ in $(seq 1 25); do [ -e "$DEV" ] && break; sleep 0.2; done
    if [ -e "$DEV" ]; then echo "  RESULT: created $(ls -l $DEV)"; else echo "  RESULT: FAILED"; fi
    kill "$p" 2>/dev/null
    wait "$p" 2>/dev/null
    rfcomm release 0 >/dev/null 2>&1
    sleep 2
}

echo "tty: $(tty 2>&1)"
echo "session leader: $(ps -o sid= -p $$ 2>/dev/null) pid=$$"

try "A: release, then connect immediately (service pattern)" \
    bash -c 'rfcomm release 0 >/dev/null 2>&1; rfcomm connect 0 '"$MAC"' '"$CH"' >/tmp/pA.log 2>&1'

try "B: release, sleep 1, then connect" \
    bash -c 'rfcomm release 0 >/dev/null 2>&1; sleep 1; rfcomm connect 0 '"$MAC"' '"$CH"' >/tmp/pB.log 2>&1'

try "C: connect with no prior release" \
    bash -c 'rfcomm connect 0 '"$MAC"' '"$CH"' >/tmp/pC.log 2>&1'

echo "### logs"
for f in /tmp/pA.log /tmp/pB.log /tmp/pC.log; do
    echo "$f: $(cat $f 2>&1)"
done
