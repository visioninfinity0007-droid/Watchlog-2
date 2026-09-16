<#
  WatchLog background launcher.

  Runs as SYSTEM from Task Scheduler. The recorder credential lives in the
  machine-scoped DPAPI-encrypted split store (%ProgramData%\WatchLog\Secrets\
  nvr_credential.dpapi) and is self-decrypted by the Site Agent itself, so this
  launcher performs NO decryption and holds no secret. It only rotates the log
  and keeps the agent running. (Pre-0.3.4 plaintext credentials are migrated into
  the encrypted store on upgrade and then removed — never read at runtime.)
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
  # Stream, do not redirect. PowerShell's `*>> $log` does not put a long-running
  # process's output on disk promptly, so agent.log sat stale for hours -- useless for
  # support, and it made 0.4.8's setup-time check report a healthy agent as failed.
  # The agent already flushes every line (print(..., flush=True)); this writes each one
  # through as it arrives.
  & $agent 2>&1 | ForEach-Object {
    $writer = [System.IO.StreamWriter]::new($log, $true)
    try { $writer.WriteLine([string]$_); $writer.Flush() } finally { $writer.Dispose() }
  }
  $code = $LASTEXITCODE
  Add-Content -Path $log -Value "==== agent exited ($code); restarting in 15s $(Get-Date -Format o) ===="
  Start-Sleep -Seconds 15
}
