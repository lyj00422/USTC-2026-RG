#!/usr/bin/env bash
# Watch one running route on the Pi; emit ONE line per state change.
#
#   bash pi-tools/_watch_run.sh <telemetry-basename> <run-out-basename>
#
# STREAM <state> ...  the route entered a new state (with speed / travel / mask)
# DROP                the route logged another chassis-link reconnect
# EXITED              the route process is gone; its own last output follows
#
# The route's failures are SILENT HOLDS -- it stops in place and keeps sending
# STOP without ever exiting -- so state changes, not liveness, are the signal.
set -u

TEL="${1:?telemetry basename required}"
OUT="${2:?run-out basename required}"

export RG_PI_HOST="${RG_PI_HOST:-172.20.10.11}"
export RG_PI_USER="${RG_PI_USER:-pi}"
if [ -z "${RG_PI_PW:-}" ]; then
  RG_PI_PW=$(powershell.exe -NoProfile -Command \
    ". 'C:\\Users\\LJY\\Desktop\\RG\\pi-tools\\_pi_env.ps1' > \$null; Write-Output \$env:RG_PI_PW" \
    | tr -d '\r\n')
  export RG_PI_PW
fi

REMOTE="cd /home/pi/robogame-runtime; prev=; drops=0; while pgrep -f route_v[2].py >/dev/null; do cur=\$(tail -n1 logs/${TEL} | sed -n 's/.*\"state\": \"\([^\"]*\)\".*/\1/p'); if [ \"\$cur\" != \"\$prev\" ]; then echo \"STREAM \$cur \$(tail -n1 logs/${TEL} | grep -oE '\"(actual_speed|travel_cm|lateral_cm|mask)\": [^,}]*' | tr '\n' ' ')\"; prev=\"\$cur\"; fi; d=\$(grep -c CHASSIS_RECONNECT logs/${OUT}); if [ \"\$d\" != \"\$drops\" ]; then echo \"DROP chassis reconnect count now \$d\"; drops=\"\$d\"; fi; sleep 4; done; echo EXITED; tail -n 25 logs/${OUT}"

exec python "C:/Users/LJY/Desktop/RG/pi-tools/_pi_ssh.py" --timeout 2400 "$REMOTE"
