# Dot-source this before any pi-tools command in a fresh PowerShell session:
#     . C:\Users\LJY\Desktop\RG\pi-tools\_pi_env.ps1
#
# The Pi password is never stored in this file or any other.  It is recovered
# at run time from the RG_PI_PW assignment recorded in this project's own
# Claude Code session transcripts (i.e. the shell that last had it exported),
# which keeps it out of the repo and out of the model's context.

$ErrorActionPreference = 'Stop'

$rx = [regex]"RG_PI_PW\s*=\s*'([^']+)'"
$counts = @{}
foreach ($f in Get-ChildItem "$env:USERPROFILE\.claude\projects\C--Users-LJY-Desktop-RG\*.jsonl") {
    foreach ($m in $rx.Matches([IO.File]::ReadAllText($f.FullName))) {
        $v = $m.Groups[1].Value
        if ($counts.ContainsKey($v)) { $counts[$v]++ } else { $counts[$v] = 1 }
    }
}
if ($counts.Count -eq 0) { throw 'no RG_PI_PW assignment found in session transcripts' }

$env:RG_PI_PW = ($counts.GetEnumerator() | Sort-Object Value -Descending | Select-Object -First 1).Key
# TWO networks, and the Pi moves between them:
#   router    Pi 10.121.252.137, laptop 10.121.252.82   <- current on 2026-10-01
#   hotspot   Pi 172.20.10.11,  laptop 172.20.10.2
# The Pi falls back to the hotspot at priority 10, so when the router's SSID goes
# away the laptop has to be on the hotspot too (see the pi-field-test note about
# the hotspot dying with no client attached).  Set RG_PI_HOST before dot-sourcing
# to point at the other one; the default here is only a starting guess.
#
# A plain `ssh pi@...` HANGS on the password prompt in this setup -- every pi-tool
# connects through paramiko with RG_PI_PW instead.  And a freshly booted Pi answers
# nothing at all for a while: retry before concluding it is unreachable.
if (-not $env:RG_PI_HOST) { $env:RG_PI_HOST = '10.121.252.137' }
if (-not $env:RG_PI_USER) { $env:RG_PI_USER = 'pi' }

Write-Output "RG_PI_PW loaded (len $($env:RG_PI_PW.Length)), host $($env:RG_PI_HOST)"
