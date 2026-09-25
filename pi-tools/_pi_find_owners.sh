#!/bin/bash
# Find every possible owner of the chassis serial link.
MAC=6E:53:BD:74:00:A7

echo "===== 1. all units mentioning rfcomm/chassis/robogame/route ====="
systemctl list-units --all --no-pager --plain 2>/dev/null \
    | grep -iE 'rfcomm|chassis|robogame|route|jdy|4843' || echo "(none)"

echo
echo "===== 2. enabled units ====="
systemctl list-unit-files --no-pager --plain 2>/dev/null \
    | grep -iE 'rfcomm|chassis|robogame|route|jdy' || echo "(none)"

echo
echo "===== 3. unit files on disk ====="
ls -l /etc/systemd/system/ /etc/systemd/system/multi-user.target.wants/ 2>/dev/null \
    | grep -iE 'rfcomm|chassis|robogame|route|jdy' || echo "(none in /etc/systemd/system)"
ls -l /lib/systemd/system/ 2>/dev/null \
    | grep -iE 'rfcomm|chassis|robogame|route|jdy' || echo "(none in /lib/systemd/system)"

echo
echo "===== 4. anything under /etc referencing rfcomm or the MAC ====="
grep -rIl -e 'rfcomm' -e "$MAC" -e 'robogame-chassis' /etc 2>/dev/null || echo "(none)"

echo
echo "===== 5. udev rules ====="
ls -l /etc/udev/rules.d/ 2>/dev/null
grep -rIn -e 'rfcomm' -e 'robogame' /etc/udev/rules.d/ 2>/dev/null || echo "(no udev matches)"

echo
echo "===== 6. rc.local / profile.d / cron ====="
cat /etc/rc.local 2>/dev/null || echo "(no rc.local)"
ls -l /etc/profile.d/ 2>/dev/null | head
sudo crontab -l 2>&1 | head
crontab -l 2>&1 | head

echo
echo "===== 7. running processes touching rfcomm/chassis/bluetooth ====="
ps aux | grep -iE 'rfcomm|chassis|robogame|route_v2|run_control' | grep -v grep || echo "(none)"

echo
echo "===== 8. everything with /dev/rfcomm0 open ====="
sudo fuser -v /dev/rfcomm0 2>&1 || echo "(cannot check / no rfcomm0)"

echo
echo "===== 9. bluetoothd serial profile plugins ====="
grep -rIn -e 'Serial' -e 'rfcomm' /etc/bluetooth/ 2>/dev/null || echo "(no /etc/bluetooth matches)"
ls -l /etc/bluetooth/ 2>/dev/null

echo
echo "===== 10. this boot: every rfcomm/bluez related log line ====="
journalctl -b --no-pager 2>/dev/null \
    | grep -iE 'rfcomm|chassis|robogame' | tail -25 || echo "(none)"

echo
echo "===== 11. stale RFCOMM sockets ====="
cat /proc/net/rfcomm 2>/dev/null || echo "(no /proc/net/rfcomm)"

echo
echo "===== 12. rfcomm state right now ====="
rfcomm -a 2>&1
ls -l /dev/rfcomm* /dev/robogame-* 2>&1
