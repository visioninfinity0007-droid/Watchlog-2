<#
  WatchLog background launcher.

  Runs as SYSTEM from Task Scheduler. The recorder credential is stored as an
  ACL-restricted plaintext env file in ProgramData (watchlog.env) and is read
  by the Site Agent itself, so this launcher performs NO decryption and holds
  no secret. It only rotates the log and keeps the agent running.
#>
param([string]$InstallDir = "$env:ProgramFiles\WatchLog")

$ErrorActionPreference = "Stop"
$data = Join-Path $env:ProgramData "WatchLog"
$log = Join-Path $data "agent.log"
$oldLog = "$log.old"
$agent = Join-Path $InstallDir "watchlog-agent.exe"

New-Item -ItemType Directory -Force -Path $data | Out-Null
if (-not (Test-Path $agent)) { throw "WatchLog Site Agent is missing" }

if ((Test-Path $log) -and (Get-Item $log).Length -gt 5000000) {
  Move-Item -Force $log $oldLog
}

while ($true) {
  Add-Content -Path $log -Value "`r`n==== agent starting $(Get-Date -Format o) ===="
  & $agent *>> $log
  $code = $LASTEXITCODE
  Add-Content -Path $log -Value "==== agent exited ($code); restarting in 15s $(Get-Date -Format o) ===="
  Start-Sleep -Seconds 15
}
