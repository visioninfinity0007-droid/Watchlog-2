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
$launcherPidFile = Join-Path $data "run-agent.pid"
$remoteApply = Join-Path $InstallDir "apply-remote-update.ps1"
$remoteRoot = Join-Path $data "remote-update"
$remotePending = Join-Path $remoteRoot "pending.json"
$remoteResult = Join-Path $remoteRoot "result.json"
$remoteBackup = Join-Path $InstallDir "watchlog-agent.exe.remote.bak"

New-Item -ItemType Directory -Force -Path $data | Out-Null
if (-not (Test-Path $agent)) { throw "WatchLog Site Agent is missing" }

# Best-effort deterministic launcher identity for future upgrades. The upgrade
# helper still verifies the process command line before trusting this PID, so a
# stale/reused PID can never cause an unrelated process to be terminated.
try { Set-Content -LiteralPath $launcherPidFile -Value ([string]$PID) -Encoding ASCII } catch {}

function Write-AgentLog([string]$Text) {
  # Best-effort. A log write may never propagate an error into the supervision loop.
  try {
    $writer = [System.IO.StreamWriter]::new($log, $true)
    try { $writer.WriteLine($Text); $writer.Flush() } finally { $writer.Dispose() }
  } catch { }
}

function Read-RemoteResult {
  try {
    if (Test-Path -LiteralPath $remoteResult) { return (Get-Content -LiteralPath $remoteResult -Raw -ErrorAction Stop | ConvertFrom-Json -ErrorAction Stop) }
  } catch { }
  return $null
}

function Write-RemoteResult($Result) {
  $tmp = "$remoteResult.tmp"
  $Result | ConvertTo-Json -Compress | Set-Content -LiteralPath $tmp -Encoding UTF8 -ErrorAction Stop
  Move-Item -LiteralPath $tmp -Destination $remoteResult -Force -ErrorAction Stop
}

function Get-RemoteRollbackReason($Result, [int]$RuntimeSeconds, [datetime]$NowUtc, [bool]$BackupExists) {
  # Why the just-applied remote update must be undone now, or $null. Only while the rollback
  # is armed: a rollback image exists and the update is neither committed (proven healthy by
  # the new Agent) nor already rolled back.
  if ($null -eq $Result -or -not $BackupExists) { return $null }
  if ([bool]$Result.committed -or [bool]$Result.rollback_applied -or [bool]$Result.superseded) { return $null }
  if ([bool]$Result.rollback_requested) {
    return "the new Agent did not prove its health in the commit window: $([string]$Result.detail)"
  }
  if ($Result.ok -ne $true) { return $null }
  if ($RuntimeSeconds -lt 60) { return "the new Agent exited after $RuntimeSeconds s" }
  $deadline = $null
  foreach ($field in @("commit_deadline", "completed_at")) {
    try {
      if ($Result.$field) {
        $deadline = [DateTimeOffset]::Parse([string]$Result.$field).UtcDateTime
        if ($field -eq "completed_at") { $deadline = $deadline.AddSeconds(900) }
        break
      }
    } catch { }
  }
  if ($deadline -and $NowUtc -gt $deadline) { return "the commit window ended without health proof from the new Agent" }
  return $null
}

function Invoke-RemoteRollback($Result, [string]$Why) {
  # Put the previous Agent back from the verified rollback image. The restored Agent then
  # proves itself (fresh heartbeat + update polling) before it reports the rollback.
  $now = [DateTimeOffset]::UtcNow.ToString("o")
  $Result | Add-Member -NotePropertyName ok -NotePropertyValue $false -Force
  $Result | Add-Member -NotePropertyName completed_at -NotePropertyValue $now -Force
  try {
    $expected = [string]$Result.previous_sha256
    if ($expected -and (Get-FileHash -LiteralPath $remoteBackup -Algorithm SHA256 -ErrorAction Stop).Hash -ne $expected.ToUpperInvariant()) {
      throw "rollback image does not match the recorded previous Agent"
    }
    Copy-Item -LiteralPath $remoteBackup -Destination $agent -Force -ErrorAction Stop
    if ((Get-FileHash -LiteralPath $agent -Algorithm SHA256 -ErrorAction Stop).Hash -ne (Get-FileHash -LiteralPath $remoteBackup -Algorithm SHA256 -ErrorAction Stop).Hash) {
      throw "restored Agent does not match the rollback image"
    }
    Remove-Item -LiteralPath $remoteBackup -Force -ErrorAction SilentlyContinue
    $Result | Add-Member -NotePropertyName rollback_applied -NotePropertyValue $true -Force
    $Result | Add-Member -NotePropertyName rollback_at -NotePropertyValue $now -Force
    $Result | Add-Member -NotePropertyName applied_version -NotePropertyValue "" -Force
    $Result | Add-Member -NotePropertyName detail -NotePropertyValue ("remote update rolled back: " + $Why + "; previous version restored") -Force
    Write-AgentLog "==== remote update rolled back: $Why ===="
  } catch {
    # The new Agent stays in place (it is the only complete binary); report the hard failure.
    $Result | Add-Member -NotePropertyName rollback_failed -NotePropertyValue $true -Force
    $Result | Add-Member -NotePropertyName detail -NotePropertyValue ("remote update NOT rolled back: " + $Why + "; " + $_.Exception.Message) -Force
    Write-AgentLog "==== remote update rollback FAILED: $($_.Exception.Message) ===="
  }
  $Result | Add-Member -NotePropertyName rollback_requested -NotePropertyValue $false -Force
  Write-RemoteResult $Result
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

  # A signed remote update is staged by the running agent, then applied here BETWEEN
  # runs while watchlog-agent.exe is not alive. This avoids self-termination races.
  try {
    if ((Test-Path $remotePending) -and (Test-Path $remoteApply)) {
      Write-AgentLog "==== applying staged signed remote update $(Get-Date -Format o) ===="
      & powershell.exe -NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File $remoteApply -InstallDir $InstallDir 2>&1 |
        ForEach-Object { Write-AgentLog ([string]$_) }
      Write-AgentLog "==== remote update handoff exit=$LASTEXITCODE ===="
    }
  } catch {
    Write-AgentLog "==== remote update handoff error: $($_.Exception.Message) ===="
  }

  Write-AgentLog "`r`n==== agent starting $(Get-Date -Format o) ===="

  $code = $null
  $startedAt = Get-Date
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

  $runtimeSeconds = [int]((Get-Date) - $startedAt).TotalSeconds
  Write-AgentLog "==== agent exited ($code) after ${runtimeSeconds}s; restarting in 15s $(Get-Date -Format o) ===="

  # A just-applied remote release that exits within 60 s, asks to be rolled back (it could not
  # prove version, heartbeat, update polling and every previously-live recorder in its commit
  # window), or is still unproven after the window, is undone from the verified image. Once
  # the new Agent commits, the image is gone and nothing here can revert it later.
  try {
    $rr = Read-RemoteResult
    $why = Get-RemoteRollbackReason $rr $runtimeSeconds ([DateTime]::UtcNow) ([bool](Test-Path -LiteralPath $remoteBackup))
    if ($why) { Invoke-RemoteRollback $rr $why }
  } catch {
    Write-AgentLog "==== remote update rollback check error: $($_.Exception.Message) ===="
  }

  try { Start-Sleep -Seconds 15 } catch { }
}
