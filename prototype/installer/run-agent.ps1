<#
  WatchLog background launcher.

  Runs as SYSTEM from Task Scheduler. The recorder password is machine-scoped
  DPAPI data in ProgramData, so it is decrypted only into this process tree's
  environment immediately before the Site Agent starts. The plaintext value is
  never written back to disk or printed to the log.
#>
param([string]$InstallDir = "$env:ProgramFiles\WatchLog")

$ErrorActionPreference = "Stop"
$data = Join-Path $env:ProgramData "WatchLog"
$secretPath = Join-Path $data "nvr_password.dpapi"
$log = Join-Path $data "agent.log"
$oldLog = "$log.old"
$agent = Join-Path $InstallDir "watchlog-agent.exe"

New-Item -ItemType Directory -Force -Path $data | Out-Null
if (-not (Test-Path $agent)) { throw "WatchLog Site Agent is missing" }
if (-not (Test-Path $secretPath)) {
  throw "Protected recorder credential is missing. Run WatchLog Setup again."
}

try { Add-Type -AssemblyName System.Security -ErrorAction Stop } catch { }

if ((Test-Path $log) -and (Get-Item $log).Length -gt 5000000) {
  Move-Item -Force $log $oldLog
}

try {
  $protected = [IO.File]::ReadAllBytes($secretPath)
  $plainBytes = [Security.Cryptography.ProtectedData]::Unprotect(
    $protected,
    $null,
    [Security.Cryptography.DataProtectionScope]::LocalMachine
  )
  $env:WATCHLOG_NVR_PASSWORD = [Text.Encoding]::UTF8.GetString($plainBytes)
  [Array]::Clear($plainBytes, 0, $plainBytes.Length)

  while ($true) {
    Add-Content -Path $log -Value "`r`n==== agent starting $(Get-Date -Format o) ===="
    & $agent *>> $log
    $code = $LASTEXITCODE
    Add-Content -Path $log -Value "==== agent exited ($code); restarting in 15s $(Get-Date -Format o) ===="
    Start-Sleep -Seconds 15
  }
}
finally {
  Remove-Item Env:WATCHLOG_NVR_PASSWORD -ErrorAction SilentlyContinue
}
