#!/bin/bash
# Read-only-ish Bluetooth SPP diagnosis for the JDY-31 chassis link.
MAC=6E:53:BD:74:00:A7

echo "=== 1. classic vs BLE: what did the adapter actually see? ==="
bluetoothctl info "$MAC" 2>&1 | grep -E 'Connected|Paired|Trusted|Modalias|Class|UUID' | head -14

echo
echo "=== 2. btmon trace during a fresh connect attempt ==="
(sudo timeout 25 btmon > /tmp/bt3.log 2>&1 &)
sleep 2
bluetoothctl --timeout 12 connect "$MAC" >/dev/null 2>&1
sleep 3

echo "--- ACL (baseband) result ---"
grep -A3 -iE 'ACL Connection Complete' /tmp/bt3.log | head -20
echo "--- L2CAP signalling ---"
grep -iE 'PSM|Connection Request|Connection Response|Result:' /tmp/bt3.log | head -20

echo
echo "=== 3. is the module advertising as BLE? ==="
sudo timeout 8 hcitool lescan --duplicates 2>/dev/null | grep -i "$MAC" | head -3
echo "(no line above = not advertising over LE)"

echo
echo "=== 4. SDP query, with explicit error ==="
sudo timeout 15 sdptool search --bdaddr "$MAC" SP 2>&1 | head -20
echo "sdptool rc=$?"

echo
echo "=== 5. channel probe: try RFCOMM channels 1-3 directly ==="
for ch in 1 2 3; do
    out=$(sudo timeout 8 rfcomm connect 0 "$MAC" "$ch" 2>&1 | head -2)
    echo "ch$ch: $out"
    sudo rfcomm release 0 >/dev/null 2>&1
    sleep 1
done

echo
echo "=== 6. final device state ==="
rfcomm -a 2>&1
ls -l /dev/rfcomm0 2>&1
