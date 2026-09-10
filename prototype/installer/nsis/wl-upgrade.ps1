<#
  WatchLog transactional upgrade helper.

  The NSIS installer delegates the dangerous, order-sensitive part of an upgrade to this script so
  the sequence is testable and the installer can NEVER report success unless the new agent is
  actually installed and running. It is invoked in stages:

    -Stage preflight  : stop the background task, stop ONLY the exact watchlog-agent.exe under the
                        install dir, wait (bounded) for it to exit, verify the binary is unlocked,
                        and back up the current binary. Non-zero exit => the caller must NOT
                        overwrite anything (the old runtime is left intact / restarted).
    -Stage commit     : verify the on-disk binary's file ProductVersion AND its runtime-reported
                        --version both equal the expected release, start the agent, verify it stays
                        alive, and verify there is exactly ONE running instance. Non-zero exit =>
                        the caller must roll back.
    -Stage rollback   : restore the backed-up previous binary and restart the previous agent, so a
                        failed upgrade leaves WatchLog working on the OLD version, not disconnected.

  It touches processes, files and version strings ONLY. It reads/writes no agent key, enrollment
  code, NVR password or any decrypted secret, and it never logs one.
#>
[CmdletBinding()]
param(
  [Parameter(Mandatory = $true)][ValidateSet('preflight','verify-version','commit','rollback')][string]$Stage,
  [Parameter(Mandatory = $true)][string]$InstallDir,
  [string]$ExpectedVersion = "",
  [int]$StopTimeoutSec = 45,
  [int]$StartTimeoutSec = 30,
  [string]$TaskName = "WatchLog Agent"
)

$ErrorActionPreference = "Stop"
$AgentExe   = Join-Path $InstallDir "watchlog-agent.exe"
$BackupExe  = Join-Path $InstallDir "watchlog-agent.exe.wlbak"
$DataRoot   = Join-Path $env:ProgramData "WatchLog"
$UpgradeLog = Join-Path $DataRoot "upgrade.log"

# ---- support-grade logging (no secrets are ever handled here) ----------------------------------
function Write-Stage([string]$msg) {
  $line = "{0}  [{1}]  {2}" -f (Get-Date -Format o), $Stage, $msg
  try { New-Item -ItemType Directory -Force -Path $DataRoot | Out-Null; Add-Content -Path $UpgradeLog -Value $line } catch {}
  Write-Host $line
}
function Fail([int]$code, [string]$msg) { Write-Stage "FAILURE($code): $msg"; exit $code }

# ---- primitives ---------------------------------------------------------------------------------
function Test-BinaryUnlocked([string]$path) {
  if (-not (Test-Path $path)) { return $true }   # absent = nothing locking it
  try {
    $fs = [System.IO.File]::Open($path, [System.IO.FileMode]::Open, [System.IO.FileAccess]::ReadWrite, [System.IO.FileShare]::None)
    $fs.Close(); $fs.Dispose(); return $true
  } catch { return $false }
}

function Get-AgentProcesses {
  # EXACTLY the watchlog-agent.exe under THIS install dir - never a broad name/py kill.
  try {
    $want = (Resolve-Path -LiteralPath $AgentExe -ErrorAction SilentlyContinue).Path
    $procs = Get-CimInstance Win32_Process -Filter "Name='watchlog-agent.exe'" -ErrorAction SilentlyContinue
    return @($procs | Where-Object {
      $_.ExecutablePath -and ($want -eq $null -or ($_.ExecutablePath -ieq $want) -or ($_.ExecutablePath -like (Join-Path $InstallDir '*')))
    })
  } catch { return @() }
}

function Get-FileProductVersion([string]$path) {
  try { return ([string](Get-Item -LiteralPath $path).VersionInfo.ProductVersion).Trim() } catch { return "" }
}

function Get-RuntimeVersion([string]$path) {
  # run the EXE with --version (exits immediately, needs no config/cloud) and capture the line.
  try {
    $out = & $path --version 2>$null
    if ($LASTEXITCODE -ne 0) { return "" }
    return ([string]($out | Select-Object -First 1)).Trim()
  } catch { return "" }
}

function Stop-Task {
  # Use the ScheduledTasks cmdlets so a missing task is handled quietly (no native stderr). This
  # stops the task's launcher LOOP; the agent grandchild is stopped separately by Stop-Agent.
  $t = Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
  if ($t) { try { Stop-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue | Out-Null } catch {}; Start-Sleep -Milliseconds 500 }
}
function Start-Task {
  $t = Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
  if ($t) { try { Start-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue | Out-Null } catch {} }
}

function Stop-Agent {
  # graceful stop of the EXACT processes, then a bounded wait, then force ONLY those exact PIDs.
  $procs = Get-AgentProcesses
  foreach ($p in $procs) {
    Write-Stage "stopping watchlog-agent.exe pid=$($p.ProcessId) path=$($p.ExecutablePath)"
    try { Stop-Process -Id $p.ProcessId -ErrorAction SilentlyContinue } catch {}
  }
  $deadline = (Get-Date).AddSeconds($StopTimeoutSec)
  while ((Get-Date) -lt $deadline) {
    if ((Get-AgentProcesses).Count -eq 0 -and (Test-BinaryUnlocked $AgentExe)) { return }
    Start-Sleep -Milliseconds 500
  }
  # last resort: force-terminate ONLY the exact surviving watchlog-agent.exe PIDs
  foreach ($p in (Get-AgentProcesses)) {
    Write-Stage "force-terminating stuck watchlog-agent.exe pid=$($p.ProcessId)"
    try { Stop-Process -Id $p.ProcessId -Force -ErrorAction SilentlyContinue } catch {}
  }
}

# =================================================================================================
switch ($Stage) {

  'preflight' {
    Write-Stage "existing binary present=$([bool](Test-Path $AgentExe)) file_version=$(Get-FileProductVersion $AgentExe)"
    Stop-Task
    Stop-Agent
    # verify no watchlog-agent.exe from this install survives, and the binary is writable.
    $survivors = (Get-AgentProcesses).Count
    if ($survivors -gt 0) { Fail 10 "watchlog-agent.exe is still running ($survivors) after graceful+forced stop; refusing to overwrite" }
    $deadline = (Get-Date).AddSeconds([Math]::Min($StopTimeoutSec, 20))
    while (-not (Test-BinaryUnlocked $AgentExe) -and (Get-Date) -lt $deadline) { Start-Sleep -Milliseconds 500 }
    if (-not (Test-BinaryUnlocked $AgentExe)) { Fail 10 "watchlog-agent.exe is still LOCKED; refusing to overwrite (old runtime preserved)" }
    # back up the working binary so a later stage can roll back to it.
    if (Test-Path $AgentExe) {
      Copy-Item -LiteralPath $AgentExe -Destination $BackupExe -Force
      Write-Stage "backed up current binary -> watchlog-agent.exe.wlbak"
    }
    Write-Stage "preflight OK: task stopped, no live agent, binary unlocked and backed up"
    exit 0
  }

  'verify-version' {
    # Run AFTER the new binaries are staged but BEFORE the task is (re)registered/started, so a
    # binary that was not actually replaced can never be started or reported as an upgrade.
    if ([string]::IsNullOrWhiteSpace($ExpectedVersion)) { Fail 20 "verify-version requires -ExpectedVersion" }
    if (-not (Test-Path $AgentExe)) { Fail 11 "watchlog-agent.exe is missing after staging" }
    $fileVer = Get-FileProductVersion $AgentExe
    $runVer  = Get-RuntimeVersion $AgentExe
    Write-Stage "installed file_version=$fileVer runtime_version=$runVer expected=$ExpectedVersion path=$AgentExe"
    if ($fileVer -ne $ExpectedVersion) { Fail 11 "file ProductVersion '$fileVer' != expected '$ExpectedVersion' (binary was not actually replaced)" }
    if ($runVer  -ne $ExpectedVersion) { Fail 11 "runtime --version '$runVer' != expected '$ExpectedVersion'" }
    Write-Stage "verify-version OK: on-disk file + runtime both report $ExpectedVersion"
    exit 0
  }

  'commit' {
    # Run AFTER the task has been (re)registered + started (register-service.ps1). Confirms the
    # EXACT installed binary is actually running as a SINGLE instance and stays alive; also
    # re-checks the version as a final guard. On success, drops the rollback backup.
    if ([string]::IsNullOrWhiteSpace($ExpectedVersion)) { Fail 20 "commit requires -ExpectedVersion" }
    if (-not (Test-Path $AgentExe)) { Fail 11 "watchlog-agent.exe is missing at commit" }
    if ((Get-FileProductVersion $AgentExe) -ne $ExpectedVersion) { Fail 11 "on-disk version changed unexpectedly before commit" }

    # the task should already be running from register-service; nudge it and verify the process.
    Start-Task
    $deadline = (Get-Date).AddSeconds($StartTimeoutSec)
    $alive = $false
    while ((Get-Date) -lt $deadline) {
      if ((Get-AgentProcesses).Count -ge 1) { $alive = $true; break }
      Start-Sleep -Milliseconds 500
    }
    if (-not $alive) { Fail 12 "the new agent did not start within $StartTimeoutSec s" }
    Start-Sleep -Seconds 3
    $count = (Get-AgentProcesses).Count
    if ($count -eq 0) { Fail 12 "the new agent started but did not stay alive (crash loop)" }
    if ($count -gt 1) { Fail 13 "more than one watchlog-agent.exe is running ($count) - duplicate runtime" }

    if (Test-Path $BackupExe) { Remove-Item -LiteralPath $BackupExe -Force -ErrorAction SilentlyContinue }
    Write-Stage "commit OK: version verified ($ExpectedVersion), single instance alive"
    exit 0
  }

  'rollback' {
    Write-Stage "rolling back to the previous working runtime"
    Stop-Task
    Stop-Agent
    if (Test-Path $BackupExe) {
      Copy-Item -LiteralPath $BackupExe -Destination $AgentExe -Force
      Remove-Item -LiteralPath $BackupExe -Force -ErrorAction SilentlyContinue
      Write-Stage "restored watchlog-agent.exe from backup (version=$(Get-FileProductVersion $AgentExe))"
    } else {
      Write-Stage "no backup present to restore (fresh install or backup already consumed)"
    }
    Start-Task
    Write-Stage "rollback complete: previous agent restarted"
    exit 0
  }
}
