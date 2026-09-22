<#
  WatchLog Site Agent launcher (scheduled task action, runs as SYSTEM at boot).

  The agent reads its machine-scoped DPAPI-encrypted split store
  (%ProgramData%\WatchLog\Secrets\) itself; this launcher performs NO decryption and
  holds no secret. It only rotates the log, runs the agent, and restarts it if it
  exits.

  THIS LOOP IS THE ONLY THING KEEPING A SITE ONLINE. The scheduled task has a single
  -AtStartup trigger plus a watchdog, so if this script ever returns, the site is dark
  until the next boot. Everything below is therefore written to be unkillable:

    * $ErrorActionPreference is "Stop" ONLY for the preconditions above the loop.
      Inside the loop it is "Continue", because in Windows PowerShell 5.1 a native
      command's stderr merged with 2>&1 (or redirected with *>>) arrives as an
      ErrorRecord, and under EAP=Stop that ErrorRecord is a TERMINATING error
      (NativeCommandError). The agent has 17 `raise SystemExit("FATAL: ...")` sites and
      any uncaught traceback also writes stderr, so a single such line used to abort
      this script mid-loop. A site ran 915s and then died on reboot exactly this way -
      and because the abort happened while piping, the stderr line never reached the
      log either, so the failure erased its own evidence.

    * Every statement in the loop body is inside try/catch. A logging failure (full
      disk, Defender holding the file, a support-bundle read) must never be able to
      stop the agent from running.
#>
param([string]$InstallDir = "$env:ProgramFiles\WatchLog")

$ErrorActionPreference = "Stop"
$data = Join-Path $env:ProgramData "WatchLog"
$log = Join-Path $data "agent.log"
$oldLog = "$log.old"
$agent = Join-Path $InstallDir "watchlog-agent.exe"

New-Item -ItemType Directory -Force -Path $data | Out-Null
if (-not (Test-Path $agent)) { throw "WatchLog Site Agent is missing" }

function Write-AgentLog([string]$Text) {
  # Best-effort. A log write may never propagate an error into the supervision loop.
  try {
    $writer = [System.IO.StreamWriter]::new($log, $true)
    try { $writer.WriteLine($Text); $writer.Flush() } finally { $writer.Dispose() }
  } catch { }
}

# From here on nothing is allowed to terminate the script.
$ErrorActionPreference = "Continue"

while ($true) {
  try {
    # Rotate INSIDE the loop: a months-old site PC nobody visits would otherwise grow
    # agent.log without bound, and a full volume would kill the agent.
    if ((Test-Path $log) -and (Get-Item $log).Length -gt 5000000) {
      Move-Item -Force $log $oldLog
    }
  } catch { }

  Write-AgentLog "`r`n==== agent starting $(Get-Date -Format o) ===="

  $code = $null
  try {
    # Stream, do not redirect to a file handle: PowerShell's `*>> $log` does not put a
    # long-running process's output on disk promptly, which left agent.log stale on
    # every site and made it useless for support. Each line is written as it arrives.
    & $agent 2>&1 | ForEach-Object { Write-AgentLog ([string]$_) }
    $code = $LASTEXITCODE
  } catch {
    # Includes NativeCommandError from the agent's stderr. Record it and keep looping.
    Write-AgentLog "==== launcher caught: $($_.Exception.Message) ===="
  }

  Write-AgentLog "==== agent exited ($code); restarting in 15s $(Get-Date -Format o) ===="
  try { Start-Sleep -Seconds 15 } catch { }
}
