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
$env:RG_PI_HOST = '172.20.10.11'
$env:RG_PI_USER = 'pi'

Write-Output "RG_PI_PW loaded (len $($env:RG_PI_PW.Length)), host $($env:RG_PI_HOST)"
