# Poll a live route run and print ONE line whenever the state, the action step,
# or the fault fields change.  Built for the Monitor tool, which takes each
# stdout line as an event -- so it must stay quiet unless something happened.
#
#     powershell.exe -NoProfile -ExecutionPolicy Bypass -File pi-tools\_pi_watch_run.ps1
#
# The Pi password is recovered by _pi_env.ps1 from the session transcripts; it is
# never passed on this command line and never written to disk.

param(
    [int]$IntervalSeconds = 10,
    [int]$MaxMinutes = 45
)

$ErrorActionPreference = 'Continue'
. "$PSScriptRoot\_pi_env.ps1" | Out-Null

$deadline = (Get-Date).AddMinutes($MaxMinutes)
$prev = ''
$prevAlive = $true

while ((Get-Date) -lt $deadline) {
    $alive = (& python "$PSScriptRoot\_pi_ssh.py" 'pgrep -f run_route_v[2]\.py >/dev/null && echo YES || echo NO' 2>$null) -join ' '
    $running = $alive -match 'YES'

    if (-not $running) {
        if ($prevAlive) { Write-Output 'RUN_ENDED  (run_route_v2.py is no longer running)'; break }
    } else {
        $raw = (& python "$PSScriptRoot\_pi_run_file.py" "$PSScriptRoot\_pi_tick.py" /home/pi/robogame-runtime 2>$null) -join "`n"
        $line = ($raw -split "`n" | Where-Object { $_ -match '\|t=' } | Select-Object -First 1)
        if ($line) {
            $line = $line.Trim()
            $parts = $line -split '\|'
            # Dedupe on STATE + which action PACKAGE is running -- deliberately
            # NOT on the step index.  Keying on the step fired once per step,
            # which is 32 lines for one build; the interesting granularity is
            # "the car moved to a new state" or "a new package started".
            $act = ($parts | Where-Object { $_ -like 'act=*' } | Select-Object -First 1)
            if ($act) { $act = ($act -split ':')[0..1] -join ':' }
            $key = "$($parts[0])|$act"

            # Anything that is a fault signal gets through regardless, and gets
            # through every time it changes rather than being deduped away.
            $faults = ($parts | Where-Object { $_ -like 'dto=*' -or $_ -like 'wall=*' })
            $dto = ($faults | Where-Object { $_ -like 'dto=*' })
            if ($dto -and $dto -ne 'dto=0') { $key += "|$dto" }

            if ($key -ne $prev) {
                $prev = $key
                Write-Output $line
            }
        }
    }
    $prevAlive = $running
    Start-Sleep -Seconds $IntervalSeconds
}
Write-Output 'WATCHER_EXPIRED'
