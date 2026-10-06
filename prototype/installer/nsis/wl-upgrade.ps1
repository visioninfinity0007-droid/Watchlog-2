<#
  WatchLog transactional upgrade helper.

  The installer must never try to overwrite files while the existing WatchLog
  runtime, launcher, or setup UI is still using them.

  Stages:
    preflight      - suspend the scheduled task, close/kill ONLY WatchLog-owned
                     processes from this InstallDir, verify EVERY replace-target
                     file is unlocked, and back up the current payload.
    verify-version - prove the newly staged agent file/runtime version (and the
                     Setup UI file version when the caller replaced it: always for
                     -PayloadProfile repair, for full Setup via -VerifySetupUi).
    commit         - prove the new registered agent is running as one logical
                     instance, then discard the rollback payload.
    rollback       - rollback-restore then rollback-start, in one call.
    rollback-restore - stop the new runtime and restore the complete previous payload,
                     leaving the task suspended (Repair removes a staged recorder registry
                     here, before the previous Agent can start and bind to it).
    rollback-start - re-register/enable/start the task, then PROVE the previous Agent runs:
                     a fresh runtime-health heartbeat from the restored version. Exit 0 only
                     with that proof (15 = task running, no heartbeat; 17 = no heartbeat
                     proof was possible because the site was not heartbeating before; 14 =
                     task not running; 16 = a payload file could not be restored, the task
                     is re-enabled anyway and the backup is kept for another attempt).
    recover        - run by the "<task> Upgrade Recovery" task that preflight -ArmRecovery
                     registers: if the upgrade owner process is gone (killed, power loss)
                     while WatchLog is paused, restore the previous WatchLog.
    uninstall      - stop WatchLog without a backup, remove its scheduled tasks (Agent,
                     recovery, orphaned candidate preflight), restore the power settings
                     recorded at install, and remove ProgramData state by the documented
                     uninstall policy (logs kept, everything else removed).

  No recorder credential, enrollment code, agent key or signing secret is read.
#>
[CmdletBinding()]
param(
  [Parameter(Mandatory = $true)]
  [ValidateSet('preflight','verify-version','commit','rollback','rollback-restore','rollback-start','recover','uninstall')]
  [string]$Stage,
  [Parameter(Mandatory = $true)][string]$InstallDir,
  [string]$ExpectedVersion = "",
  [int]$StopTimeoutSec = 35,
  [int]$StartTimeoutSec = 30,
  [string]$TaskName = "WatchLog Agent",
  [ValidateSet('full','repair')]
  [string]$PayloadProfile = "full",
  [string]$DataRootOverride = "",
  [switch]$VerifySetupUi,
  # rollback / rollback-start: require a fresh heartbeat from the restored Agent even when the
  # backup did not record one before (Repair always passes it).
  [switch]$ProveHealth,
  [int]$ProofTimeoutSec = 240,
  [int]$FutureSkewSec = 120,
  # preflight: arm the recovery task for the time WatchLog is paused; the upgrade owner is
  # -OwnerPid (default: the process that started this helper).
  [switch]$ArmRecovery,
  [int]$OwnerPid = 0,
  [int]$RecoveryMaxAttempts = 3,
  # Test seam: a stand-in for powercfg.exe (uninstall power restore).
  [string]$PowerCfgPath = ""
)

$ErrorActionPreference = "Stop"

$InstallDir = [System.IO.Path]::GetFullPath($InstallDir).TrimEnd('\')
$AgentExe   = Join-Path $InstallDir "watchlog-agent.exe"
$SetupExe   = Join-Path $InstallDir "watchlog-setup-ui.exe"
$RunnerPs1  = Join-Path $InstallDir "run-agent.ps1"
$RunnerCmd  = Join-Path $InstallDir "run-agent.cmd"
$RegisterService = Join-Path $InstallDir "register-service.ps1"
$BackupExe  = Join-Path $InstallDir "watchlog-agent.exe.wlbak"   # compatibility / support breadcrumb
$DataRoot   = if ([string]::IsNullOrWhiteSpace($DataRootOverride)) {
  Join-Path $env:ProgramData "WatchLog"
} else {
  [System.IO.Path]::GetFullPath($DataRootOverride)
}
$LauncherPidFile = Join-Path $DataRoot "run-agent.pid"
$UpgradeLog = Join-Path $DataRoot "upgrade.log"
$BackupRoot = Join-Path $DataRoot "upgrade-backup"
$Manifest   = Join-Path $BackupRoot "manifest.json"
$HealthPath = Join-Path $DataRoot "Secrets\runtime-health.json"
$MarkerPath = Join-Path $DataRoot "upgrade-in-progress.json"
$PowerBaselinePath = Join-Path $DataRoot "Secrets\power-baseline.json"
# The recovery task runs a copy of THIS helper from the install folder (administrators only),
# never from ProgramData, which older installs left writable by standard users.
$RecoveryScript = Join-Path $InstallDir "wl-upgrade-recover.ps1"
$RecoveryTaskName = "$TaskName Upgrade Recovery"

# Scope lock/backup/rollback checks to the payload the caller actually replaces.
# Full Setup replaces the readme/icon as well. Existing-site Repair/Upgrade replaces the
# Agent, its scripts and the Qt setup UI (Manage Recorders, Site Status) but deliberately
# not the readme/icon, so an unrelated lock on one of those must never block a repair.
$FullPayloadFiles = @(
  "watchlog-agent.exe",
  "watchlog-setup-ui.exe",
  "run-agent.ps1",
  "register-service.ps1",
  "apply-remote-update.ps1",
  "wl-upgrade.ps1",
  "READ ME FIRST.txt",
  "setup.ico",
  "watchlog.defaults.ini"
)
$RepairPayloadFiles = @(
  "watchlog-agent.exe",
  "watchlog-setup-ui.exe",
  "run-agent.ps1",
  "register-service.ps1",
  "apply-remote-update.ps1",
  "wl-upgrade.ps1",
  "watchlog.defaults.ini"
)
$PayloadFiles = if ($PayloadProfile -eq "repair") { @($RepairPayloadFiles) } else { @($FullPayloadFiles) }

function Write-Stage([string]$msg) {
  $line = "{0}  [{1}/{2}]  {3}" -f (Get-Date -Format o), $Stage, $PayloadProfile, $msg
  try {
    New-Item -ItemType Directory -Force -Path $DataRoot | Out-Null
    Add-Content -Path $UpgradeLog -Value $line
  } catch {}
  Write-Host $line
}

function Fail([int]$code, [string]$msg) {
  Write-Stage "FAILURE($code): $msg"
  exit $code
}

function Test-FileUnlocked([string]$path) {
  if (-not (Test-Path -LiteralPath $path)) { return $true }
  try {
    $fs = [System.IO.File]::Open(
      $path,
      [System.IO.FileMode]::Open,
      [System.IO.FileAccess]::ReadWrite,
      [System.IO.FileShare]::None
    )
    $fs.Close()
    $fs.Dispose()
    return $true
  } catch {
    return $false
  }
}

function Get-LockedPayloadFiles {
  $locked = @()
  foreach ($name in $PayloadFiles) {
    $path = Join-Path $InstallDir $name
    if (-not (Test-FileUnlocked $path)) { $locked += $name }
  }
  return @($locked)
}

function Convert-ProcessRecord($p) {
  if ($null -eq $p) { return $null }
  try {
    $pidValue = [int]$p.ProcessId
    if ($pidValue -le 0) { return $null }
    return [pscustomobject]@{
      ProcessId       = $pidValue
      ParentProcessId = [int]$p.ParentProcessId
      Name            = [string]$p.Name
      ExecutablePath  = [string]$p.ExecutablePath
      CommandLine     = [string]$p.CommandLine
    }
  } catch {
    return $null
  }
}

function Get-ExactExecutableProcesses([string]$Name, [string]$ExpectedPath) {
  try {
    $want = [System.IO.Path]::GetFullPath($ExpectedPath)
    $out = @()
    foreach ($p in @(Get-CimInstance Win32_Process -Filter "Name='$Name'" -ErrorAction SilentlyContinue)) {
      if (-not $p.ExecutablePath) { continue }
      $actual = [System.IO.Path]::GetFullPath([string]$p.ExecutablePath)
      if ($actual -ine $want) { continue }
      $rec = Convert-ProcessRecord $p
      if ($null -ne $rec) { $out += $rec }
    }
    return @($out)
  } catch {
    return @()
  }
}

function Get-AgentProcesses {
  return @(Get-ExactExecutableProcesses "watchlog-agent.exe" $AgentExe)
}

function Get-SetupProcesses {
  return @(Get-ExactExecutableProcesses "watchlog-setup-ui.exe" $SetupExe)
}

function Get-AgentRuntimeLeaves {
  # PyInstaller one-file can expose a bootloader + child for the same exe.
  # Commit cares about logical runtimes, not bootloader process count.
  $mine = @(Get-AgentProcesses)
  $parents = @($mine | ForEach-Object { [int]$_.ParentProcessId })
  $leaves = @($mine | Where-Object { $parents -notcontains [int]$_.ProcessId })
  if ($leaves.Count -gt 0) { return @($leaves) }
  return @($mine)
}

function Get-LauncherProcesses {
  # Stop ONLY PowerShell/cmd launchers belonging to THIS WatchLog install.
  #
  # Evidence paths, strongest first:
  #   1. ParentProcessId of the exact installed watchlog-agent.exe. This works
  #      even when Windows/WMI withholds CommandLine for a SYSTEM-owned process.
  #   2. Verified run-agent.pid (still requires the command line to match).
  #   3. Command-line scan fallback for Build 69/older launchers between restarts.
  $wantPs1 = [System.IO.Path]::GetFullPath($RunnerPs1).ToLowerInvariant()
  $wantCmd = [System.IO.Path]::GetFullPath($RunnerCmd).ToLowerInvariant()
  $parentPids = @{}
  foreach ($agentProc in @(Get-AgentProcesses)) {
    $ppid = [int]$agentProc.ParentProcessId
    if ($ppid -gt 0) { $parentPids[[string]$ppid] = $true }
  }

  $pidFileValue = 0
  if (Test-Path -LiteralPath $LauncherPidFile) {
    $pidText = ""
    try { $pidText = (Get-Content -LiteralPath $LauncherPidFile -Raw).Trim() } catch {}
    [void][int]::TryParse($pidText, [ref]$pidFileValue)
  }

  try {
    $out = @()
    $seen = @{}
    foreach ($p in @(Get-CimInstance Win32_Process -ErrorAction SilentlyContinue)) {
      $name = ([string]$p.Name).ToLowerInvariant()
      if ($name -notin @("powershell.exe","pwsh.exe","cmd.exe")) { continue }

      $pidValue = 0
      try { $pidValue = [int]$p.ProcessId } catch { $pidValue = 0 }
      if ($pidValue -le 0) { continue }

      $line = ([string]$p.CommandLine).ToLowerInvariant()
      $lineMatch = $false
      if ($line) {
        $lineMatch = $line.Contains($wantPs1) -or $line.Contains($wantCmd)
      }

      # Parent fallback is ONLY for the SYSTEM/WMI case where CommandLine is
      # unavailable. If Windows gives us a command line and it is not run-agent,
      # do not kill that PowerShell even if it manually launched the agent.
      $isAgentParentFallback = (
        $parentPids.ContainsKey([string]$pidValue) -and -not $line
      )
      $isVerifiedPidFile = ($pidFileValue -eq $pidValue -and $lineMatch)
      if (-not ($isAgentParentFallback -or $lineMatch -or $isVerifiedPidFile)) { continue }

      $rec = Convert-ProcessRecord $p
      if ($null -eq $rec) { continue }
      $key = [string]$rec.ProcessId
      if ($seen.ContainsKey($key)) { continue }
      $seen[$key] = $true
      $out += $rec
    }
    return @($out)
  } catch {
    return @()
  }
}

function Stop-Pids([array]$Processes, [string]$Label, [switch]$Force, [switch]$Tree) {
  foreach ($p in @($Processes)) {
    $pidValue = 0
    try { $pidValue = [int]$p.ProcessId } catch { $pidValue = 0 }
    if ($pidValue -le 0) {
      Write-Stage "refusing malformed $Label process record with no valid PID"
      continue
    }
    Write-Stage "stopping $Label pid=$pidValue path=$([string]$p.ExecutablePath)"
    try {
      if ($Tree) {
        # PID is admitted only after path/command-line ownership checks above.
        # /T is therefore a targeted WatchLog process-tree kill, never a name-wide kill.
        & "$env:SystemRoot\System32\taskkill.exe" /PID $pidValue /T /F 2>$null | Out-Null
      } elseif ($Force) {
        Stop-Process -Id $pidValue -Force -ErrorAction SilentlyContinue
      } else {
        Stop-Process -Id $pidValue -ErrorAction SilentlyContinue
      }
    } catch {}
  }
}

function Get-TaskStateSnapshot {
  $t = Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
  if (-not $t) {
    return @{ present = $false; enabled = $false }
  }
  return @{
    present = $true
    enabled = ([string]$t.State -ne "Disabled")
  }
}

function Suspend-Task {
  $snap = Get-TaskStateSnapshot
  $t = Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
  if (-not $t) { return $snap }

  try { Stop-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue | Out-Null } catch {}
  try { Disable-ScheduledTask -TaskName $TaskName -ErrorAction Stop | Out-Null } catch {
    throw "could not disable scheduled task '$TaskName': $($_.Exception.Message)"
  }

  # Older field builds have a repeating watchdog trigger. Prove it is disabled
  # before killing the launcher, otherwise it can resurrect the Agent mid-upgrade.
  $deadline = (Get-Date).AddSeconds(5)
  while ((Get-Date) -lt $deadline) {
    $check = Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
    if ($check -and [string]$check.State -eq "Disabled") {
      Write-Stage "scheduled task '$TaskName' disabled and watchdog suppressed"
      return $snap
    }
    Start-Sleep -Milliseconds 250
  }
  throw "scheduled task '$TaskName' could not be proven Disabled"
}

function Resume-Task([bool]$WasPresent, [bool]$WasEnabled) {
  if (-not $WasPresent -or -not $WasEnabled) { return }
  $t = Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
  if (-not $t) { return }
  try { Enable-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue | Out-Null } catch {}
  try { Start-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue | Out-Null } catch {}
}

function Stop-WatchLogRuntime {
  # 1) launcher first, otherwise its restart loop can resurrect the agent.
  # It is a stateless restart loop, so terminate this exact launcher immediately.
  Stop-Pids (Get-LauncherProcesses) "WatchLog launcher" -Force -Tree

  # 2) ask an open Setup UI to close, then kill only the exact packaged UI if it stays.
  foreach ($p in @(Get-SetupProcesses)) {
    try {
      $gp = Get-Process -Id $p.ProcessId -ErrorAction SilentlyContinue
      if ($gp -and $gp.MainWindowHandle -ne 0) {
        Write-Stage "requesting WatchLog Setup UI close pid=$($p.ProcessId)"
        [void]$gp.CloseMainWindow()
      }
    } catch {}
  }
  Start-Sleep -Milliseconds 800
  Stop-Pids (Get-SetupProcesses) "WatchLog Setup UI" -Force

  # 3) stop the agent; give it a short bounded chance to release the one-file exe.
  Stop-Pids (Get-AgentProcesses) "watchlog-agent.exe"
  $grace = (Get-Date).AddSeconds([Math]::Min(5, $StopTimeoutSec))
  while ((Get-Date) -lt $grace) {
    if ((Get-AgentProcesses).Count -eq 0) { break }
    Start-Sleep -Milliseconds 250
  }
  Stop-Pids (Get-AgentProcesses) "stuck watchlog-agent.exe" -Force

  # 4) bounded final drain. This catches PyInstaller bootloader/child teardown.
  $deadline = (Get-Date).AddSeconds($StopTimeoutSec)
  while ((Get-Date) -lt $deadline) {
    if ((Get-AgentProcesses).Count -eq 0 -and
        (Get-SetupProcesses).Count -eq 0 -and
        (Get-LauncherProcesses).Count -eq 0) {
      return
    }
    Start-Sleep -Milliseconds 250
  }
}

function Backup-Payload([hashtable]$TaskSnapshot) {
  Remove-Item -LiteralPath $BackupRoot -Recurse -Force -ErrorAction SilentlyContinue
  New-Item -ItemType Directory -Force -Path $BackupRoot | Out-Null

  $existing = @()
  foreach ($name in $PayloadFiles) {
    $src = Join-Path $InstallDir $name
    if (Test-Path -LiteralPath $src) {
      Copy-Item -LiteralPath $src -Destination (Join-Path $BackupRoot $name) -Force
      $existing += $name
    }
  }

  # Keep the historical agent-only backup too, so older support/uninstall logic
  # still has a familiar rollback breadcrumb.
  if (Test-Path -LiteralPath $AgentExe) {
    Copy-Item -LiteralPath $AgentExe -Destination $BackupExe -Force
  }

  $meta = [ordered]@{
    schema = "watchlog.upgrade_backup.v2"
    created_at = [DateTimeOffset]::UtcNow.ToString("o")
    task_present = [bool]$TaskSnapshot.present
    task_enabled = [bool]$TaskSnapshot.enabled
    existing_files = @($existing)
    # What a rollback must prove: this version heartbeating again (when it was before).
    previous_version = (Get-FileProductVersion $AgentExe)
    health_before = (Test-RecentHeartbeat)
  }
  $meta | ConvertTo-Json -Depth 4 | Set-Content -LiteralPath $Manifest -Encoding UTF8
}

function Read-BackupManifest {
  if (-not (Test-Path -LiteralPath $Manifest)) { return $null }
  try { return Get-Content -LiteralPath $Manifest -Raw | ConvertFrom-Json } catch { return $null }
}

function Restore-Payload {
  $meta = Read-BackupManifest
  if (-not $meta) {
    # Compatibility fallback for an older preflight that only made .wlbak.
    if (Test-Path -LiteralPath $BackupExe) {
      Copy-Item -LiteralPath $BackupExe -Destination $AgentExe -Force
      Remove-Item -LiteralPath $BackupExe -Force -ErrorAction SilentlyContinue
      return @{ task_present = $true; task_enabled = $true }
    }
    return $null
  }

  $existed = @($meta.existing_files)
  foreach ($name in $PayloadFiles) {
    $target = Join-Path $InstallDir $name
    $backup = Join-Path $BackupRoot $name
    if ($existed -contains $name) {
      if (Test-Path -LiteralPath $backup) {
        Copy-Item -LiteralPath $backup -Destination $target -Force
        # A copy that "succeeded" but left different bytes is not a restore.
        if ((Get-FileHash -LiteralPath $backup -Algorithm SHA256).Hash -ne
            (Get-FileHash -LiteralPath $target -Algorithm SHA256).Hash) {
          throw "restored $name does not match its backup"
        }
      }
    } elseif (Test-Path -LiteralPath $target) {
      # File did not exist before this upgrade; remove the partially introduced copy.
      Remove-Item -LiteralPath $target -Force -ErrorAction SilentlyContinue
    }
  }

  Remove-Item -LiteralPath $BackupExe -Force -ErrorAction SilentlyContinue
  return $meta
}

function Clear-Backup {
  Remove-Item -LiteralPath $BackupExe -Force -ErrorAction SilentlyContinue
  Remove-Item -LiteralPath $BackupRoot -Recurse -Force -ErrorAction SilentlyContinue
}


function Ensure-WatchLogBackgroundTask([string]$Reason) {
  Write-Stage "ensuring WatchLog background task: $Reason"

  # Preferred path: use the payload's own task-registration script. This is
  # deliberately idempotent and recreates a damaged/disabled task definition.
  if (Test-Path -LiteralPath $RegisterService) {
    $out = & powershell.exe -NoProfile -ExecutionPolicy Bypass -File $RegisterService -InstallDir $InstallDir 2>&1
    $rc = $LASTEXITCODE
    foreach ($line in @($out)) {
      if ($null -ne $line -and -not [string]::IsNullOrWhiteSpace([string]$line)) {
        Write-Stage ("register-service: " + [string]$line)
      }
    }
    if ($rc -ne 0) {
      Write-Stage "register-service failed with exit=$rc"
    }
  }

  # Fallback for very old field builds whose rollback payload did not contain
  # register-service.ps1. Recreate the known WatchLog task directly from the
  # restored run-agent.ps1 rather than leaving the site dark.
  $task = Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
  if (-not $task -and (Test-Path -LiteralPath $RunnerPs1)) {
    try {
      $powershell = "$env:SystemRoot\System32\WindowsPowerShell\v1.0\powershell.exe"
      $arguments = "-NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File `"$RunnerPs1`" -InstallDir `"$InstallDir`""
      $action = New-ScheduledTaskAction -Execute $powershell -Argument $arguments
      $boot = New-ScheduledTaskTrigger -AtStartup
      $boot.Delay = "PT30S"
      $watchdog = New-ScheduledTaskTrigger -Once -At (Get-Date).Date.AddMinutes(1) -RepetitionInterval (New-TimeSpan -Minutes 5)
      $principal = New-ScheduledTaskPrincipal -UserId "SYSTEM" -LogonType ServiceAccount -RunLevel Highest
      $settings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -StartWhenAvailable -RestartCount 999 -RestartInterval (New-TimeSpan -Minutes 1) -ExecutionTimeLimit (New-TimeSpan -Seconds 0) -MultipleInstances IgnoreNew
      Register-ScheduledTask -TaskName $TaskName -Action $action -Trigger @($boot,$watchdog) -Principal $principal -Settings $settings -Force | Out-Null
      Write-Stage "recreated fallback WatchLog scheduled task"
    } catch {
      Write-Stage "fallback task registration failed: $($_.Exception.Message)"
    }
  }

  try {
    $task = Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
    if (-not $task) { return $false }
    if ([string]$task.State -eq "Disabled") {
      Enable-ScheduledTask -TaskName $TaskName -ErrorAction Stop | Out-Null
    }
    Start-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue | Out-Null
  } catch {
    Write-Stage "task enable/start failed: $($_.Exception.Message)"
    return $false
  }

  # The scheduled task owns the persistent run-agent.ps1 supervision loop.
  # Proving the task is Running is more reliable on field Windows builds than
  # WMI/CIM process-path enumeration, which can hide SYSTEM-owned process data.
  $deadline = (Get-Date).AddSeconds([Math]::Max(15, $StartTimeoutSec))
  while ((Get-Date) -lt $deadline) {
    $task = Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
    if ($task -and [string]$task.State -eq "Running") {
      Write-Stage "WatchLog background task proven Running"
      return $true
    }
    Start-Sleep -Milliseconds 500
  }
  return $false
}

function Get-FileProductVersion([string]$path) {
  try { return ([string](Get-Item -LiteralPath $path).VersionInfo.ProductVersion).Trim() } catch { return "" }
}

function Get-RuntimeVersion([string]$path) {
  try {
    $out = & $path --version 2>$null
    if ($LASTEXITCODE -ne 0) { return "" }
    return ([string]($out | Select-Object -First 1)).Trim()
  } catch {
    return ""
  }
}


function ConvertTo-UtcOrNull($Value) {
  if (-not $Value) { return $null }
  try { return [DateTimeOffset]::Parse([string]$Value).UtcDateTime } catch { return $null }
}

function Get-VersionKey([string]$Version) {
  $text = ([string]$Version).Trim().TrimStart('v','V').Split('+')[0].Split('-')[0]
  $parts = @($text.Split('.') | ForEach-Object { $n = 0; [void][int]::TryParse($_, [ref]$n); $n })
  while ($parts.Count -lt 3) { $parts += 0 }
  return ("{0}.{1}.{2}" -f $parts[0], $parts[1], $parts[2])
}

function Read-RuntimeHealth {
  try {
    if (Test-Path -LiteralPath $HealthPath) { return (Get-Content -LiteralPath $HealthPath -Raw | ConvertFrom-Json) }
  } catch {}
  return $null
}

function Test-RecentHeartbeat {
  # Was the Agent heartbeating just before this upgrade (last 15 minutes, not future-dated)?
  $h = Read-RuntimeHealth
  if (-not $h) { return $false }
  $beat = ConvertTo-UtcOrNull $h.heartbeat_at
  $now = [DateTime]::UtcNow
  return [bool]($beat -and $beat -ge $now.AddMinutes(-15) -and $beat -le $now.AddSeconds($FutureSkewSec))
}

function Wait-RestoredAgentHealth([datetime]$SinceUtc, [string]$ExpectVersion) {
  # Proof that the PREVIOUS Agent runs, not just its launcher task: the protected
  # runtime-health file carries a heartbeat written after the restart (not older, not
  # future-dated) by the restored version.
  $deadline = (Get-Date).AddSeconds([Math]::Max(5, $ProofTimeoutSec))
  $want = if ($ExpectVersion) { Get-VersionKey $ExpectVersion } else { "" }
  while ((Get-Date) -lt $deadline) {
    $h = Read-RuntimeHealth
    if ($h) {
      $beat = ConvertTo-UtcOrNull $h.heartbeat_at
      $versionOk = (-not $want) -or ((Get-VersionKey ([string]$h.agent_version)) -eq $want)
      if ($versionOk -and $beat -and $beat -ge $SinceUtc -and $beat -le [DateTime]::UtcNow.AddSeconds($FutureSkewSec)) {
        Write-Stage "previous Agent proven running: version $([string]$h.agent_version) heartbeat $([string]$h.heartbeat_at)"
        return $true
      }
    }
    Start-Sleep -Seconds 1
  }
  Write-Stage "previous Agent NOT proven: no fresh heartbeat from version '$ExpectVersion' within $ProofTimeoutSec s"
  return $false
}

function Set-ManifestField([string]$Name, $Value) {
  try {
    $meta = Read-BackupManifest
    if (-not $meta) { return }
    $meta | Add-Member -NotePropertyName $Name -NotePropertyValue $Value -Force
    $meta | ConvertTo-Json -Depth 4 | Set-Content -LiteralPath $Manifest -Encoding UTF8
  } catch {}
}

function Invoke-RollbackRestore {
  # Stop the new runtime and put the previous payload back; the task stays suspended. A copy
  # error is retried (late process teardown, antivirus) and then reported as code 16; the
  # caller still re-enables the task, so a failed restore never leaves the site dark.
  try { $null = Suspend-Task } catch { Write-Stage "task suspend failed before restore: $($_.Exception.Message)" }
  Stop-WatchLogRuntime
  $lastError = ""
  for ($attempt = 1; $attempt -le 3; $attempt++) {
    try {
      $meta = Restore-Payload
      if ($meta) { Write-Stage "restored previous WatchLog payload" }
      else { Write-Stage "no rollback manifest found; attempting to recover the current on-disk payload" }
      Set-ManifestField "restore_failed" $false
      return @{ code = 0; meta = $meta }
    } catch {
      $lastError = $_.Exception.Message
      Write-Stage "payload restore attempt $attempt/3 failed: $lastError"
      Stop-Pids (Get-AgentProcesses) "late watchlog-agent.exe" -Force
      Stop-Pids (Get-SetupProcesses) "late WatchLog Setup UI" -Force
      Start-Sleep -Seconds 2
    }
  }
  Write-Stage "RESTORE FAILED: previous WatchLog payload could not be restored: $lastError"
  Set-ManifestField "restore_failed" $true
  return @{ code = 16; meta = (Read-BackupManifest) }
}

function Invoke-RollbackStart($Meta, [bool]$Prove) {
  $since = [DateTime]::UtcNow
  if (-not (Ensure-WatchLogBackgroundTask "rollback recovery")) { return 14 }
  $need = $Prove -or ($Meta -and [bool]$Meta.health_before)
  if (-not $need) {
    Write-Stage "previous Agent cannot be proven: this site was not heartbeating before the upgrade"
    return 17
  }
  $expect = if ($Meta) { [string]$Meta.previous_version } else { "" }
  if (Wait-RestoredAgentHealth $since $expect) { return 0 }
  return 15
}

function Complete-Rollback([int]$RestoreCode, [int]$StartCode) {
  if ($RestoreCode -ne 0) {
    # Keep the backup (the only good copy) and the recovery task, which retries.
    Fail 16 "previous WatchLog payload could not be restored; background task re-enable result=$StartCode; backup kept for another attempt"
  }
  switch ($StartCode) {
    0 {
      Clear-Backup
      Disarm-Recovery
      Write-Stage "rollback complete: previous payload restored, task Running, previous Agent proven by a fresh heartbeat"
      exit 0
    }
    14 { Fail 14 "previous WatchLog payload was restored but its background task could not be repaired/restarted (backup and recovery task kept)" }
    15 {
      Clear-Backup
      Disarm-Recovery
      Fail 15 "previous WatchLog payload restored and its task is Running, but the previous Agent sent no fresh heartbeat within $ProofTimeoutSec s"
    }
    default {
      Clear-Backup
      Disarm-Recovery
      Fail 17 "previous WatchLog payload restored and its task is Running; no heartbeat proof is possible because the site was not heartbeating before"
    }
  }
}

function Read-Marker {
  try {
    if (Test-Path -LiteralPath $MarkerPath) { return (Get-Content -LiteralPath $MarkerPath -Raw | ConvertFrom-Json) }
  } catch {}
  return $null
}

function Write-Marker($Marker) {
  $tmp = $MarkerPath + ".tmp"
  $Marker | ConvertTo-Json -Depth 4 | Set-Content -LiteralPath $tmp -Encoding UTF8
  Move-Item -LiteralPath $tmp -Destination $MarkerPath -Force
}

function Get-ProcessStartUtc([int]$ProcessId) {
  try { return (Get-Process -Id $ProcessId -ErrorAction Stop).StartTime.ToUniversalTime() } catch { return $null }
}

function Test-OwnerAlive($Marker) {
  # The same PID AND the same start time: after a reboot or PID reuse it is not the owner.
  $ownerPid = 0
  [void][int]::TryParse([string]$Marker.owner_pid, [ref]$ownerPid)
  if ($ownerPid -le 0) { return $false }
  $started = Get-ProcessStartUtc $ownerPid
  $recorded = ConvertTo-UtcOrNull $Marker.owner_started_at
  if ($null -eq $started -or $null -eq $recorded) { return $false }
  return ([Math]::Abs(($started - $recorded).TotalSeconds) -lt 2)
}

function Arm-Recovery {
  # While WatchLog is paused (task disabled), a killed orchestrator or a power cut must not
  # leave the site dark until someone notices: record what is in progress, and register a
  # SYSTEM task that restores the previous WatchLog once the owner process is gone.
  $ownerId = $OwnerPid
  if ($ownerId -le 0) {
    try { $ownerId = [int](Get-CimInstance Win32_Process -Filter "ProcessId=$PID").ParentProcessId } catch { $ownerId = 0 }
  }
  $started = Get-ProcessStartUtc $ownerId
  $marker = [ordered]@{
    schema = "watchlog.upgrade_in_progress.v1"
    created_at = [DateTimeOffset]::UtcNow.ToString("o")
    owner_pid = $ownerId
    owner_started_at = $(if ($started) { ([DateTimeOffset]$started).ToString("o") } else { "" })
    install_dir = $InstallDir
    task_name = $TaskName
    payload_profile = $PayloadProfile
    prove_health = [bool]$ProveHealth
    attempts = 0
    registry_staged_id = ""
  }
  Write-Marker $marker
  try {
    if ($PSCommandPath -and ([IO.Path]::GetFullPath($PSCommandPath) -ine [IO.Path]::GetFullPath($RecoveryScript))) {
      Copy-Item -LiteralPath $PSCommandPath -Destination $RecoveryScript -Force
    }
    $powershell = "$env:SystemRoot\System32\WindowsPowerShell\v1.0\powershell.exe"
    $arguments = "-NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File `"$RecoveryScript`" -Stage recover -InstallDir `"$InstallDir`" -TaskName `"$TaskName`" -PayloadProfile $PayloadProfile"
    if ($DataRootOverride) { $arguments += " -DataRootOverride `"$DataRoot`"" }
    $action = New-ScheduledTaskAction -Execute $powershell -Argument $arguments
    $boot = New-ScheduledTaskTrigger -AtStartup
    $boot.Delay = "PT1M"
    $watch = New-ScheduledTaskTrigger -Once -At (Get-Date).AddMinutes(5) -RepetitionInterval (New-TimeSpan -Minutes 5)
    $principal = New-ScheduledTaskPrincipal -UserId "SYSTEM" -LogonType ServiceAccount -RunLevel Highest
    $settings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -StartWhenAvailable -ExecutionTimeLimit (New-TimeSpan -Minutes 20) -MultipleInstances IgnoreNew
    Register-ScheduledTask -TaskName $RecoveryTaskName -Action $action -Trigger @($boot,$watch) -Principal $principal -Settings $settings -Force | Out-Null
    Write-Stage "recovery armed: '$RecoveryTaskName' restores the previous WatchLog if owner pid=$ownerId stops before commit/rollback"
  } catch {
    Write-Stage "WARNING: recovery task could not be registered ($($_.Exception.Message)); marker written to $MarkerPath"
  }
}

function Disarm-Recovery {
  try { Unregister-ScheduledTask -TaskName $RecoveryTaskName -Confirm:$false -ErrorAction Stop | Out-Null } catch {}
  Remove-Item -LiteralPath $MarkerPath -Force -ErrorAction SilentlyContinue
  Remove-Item -LiteralPath ($MarkerPath + ".tmp") -Force -ErrorAction SilentlyContinue
  Remove-Item -LiteralPath $RecoveryScript -Force -ErrorAction SilentlyContinue
}

function Test-TrustedDirectory([string]$Path) {
  try {
    $acl = Get-Acl -LiteralPath $Path
    $owner = $acl.GetOwner([Security.Principal.SecurityIdentifier]).Value
    if (@('S-1-5-18','S-1-5-32-544') -notcontains $owner -or -not $acl.AreAccessRulesProtected) { return $false }
    foreach ($rule in $acl.Access) {
      if ($rule.AccessControlType -ne 'Allow') { continue }
      $sid = $rule.IdentityReference.Translate([Security.Principal.SecurityIdentifier]).Value
      if (@('S-1-5-18','S-1-5-32-544') -notcontains $sid) { return $false }
    }
    return $true
  } catch { return $false }
}

function Undo-InterruptedRegistryStaging($Marker) {
  # A recorder registry staged by an interrupted Repair must go before the previous Agent
  # starts. Only the candidate Setup UI knows how to remove it safely, and only from its
  # protected candidate folder.
  $id = [string]$Marker.registry_staged_id
  if (-not $id) { return }
  $candidate = Join-Path $DataRoot "repair-candidate"
  $ui = Join-Path $candidate "watchlog-setup-ui.exe"
  if (-not (Test-Path -LiteralPath $ui) -or -not (Test-TrustedDirectory $candidate)) {
    Write-Stage "WARNING: a recorder registry staged by the interrupted Repair ($id) was kept: no protected candidate Setup UI is left to remove it; support must check recorders.json"
    return
  }
  $result = Join-Path $candidate ("recover-registry-" + [guid]::NewGuid().ToString("N") + ".json")
  $config = Join-Path $InstallDir "watchlog.ini"
  try {
    $p = Start-Process -FilePath $ui -ArgumentList @("--registry-rollback", $id, "--config", ('"' + $config + '"'), "--result-json", ('"' + $result + '"')) -PassThru -WindowStyle Hidden
    $null = $p.Handle
    if (-not $p.WaitForExit(180000)) {
      try { & "$env:SystemRoot\System32\taskkill.exe" /PID $p.Id /T /F 2>$null | Out-Null } catch {}
      Write-Stage "WARNING: registry staging rollback timed out; staged registry may remain"
      return
    }
    $obj = if (Test-Path -LiteralPath $result) { Get-Content -LiteralPath $result -Raw | ConvertFrom-Json } else { $null }
    Write-Stage ("registry staged by the interrupted Repair: " + $(if ($obj -and [bool]$obj.ok) { "removed" } else { "kept ($([string]$obj.action))" }))
  } catch {
    Write-Stage "WARNING: registry staging rollback failed: $($_.Exception.Message)"
  } finally {
    Remove-Item -LiteralPath $result -Force -ErrorAction SilentlyContinue
  }
}

function Write-RecoveryResult([int]$Code, [string]$Message) {
  # The Repair result file is what Site Status/support read; an interrupted Repair gets an
  # honest final state there too.
  if ($PayloadProfile -ne "repair") { return }
  try {
    $status = if ($Code -eq 0) { "rolled_back" } else { "failed" }
    $lines = @("[repair]", "status=$status", "code=$Code", "stage=interrupted upgrade recovery",
               ("message=" + $Message.Replace("=","-")), ("log=" + (Join-Path $DataRoot "upgrade.log")))
    $lines | Set-Content -LiteralPath (Join-Path $DataRoot "repair-upgrade-result.ini") -Encoding ASCII
  } catch {}
}

function Get-PowerCfg {
  if ($PowerCfgPath) { return $PowerCfgPath }
  return (Join-Path $env:SystemRoot "System32\powercfg.exe")
}

function Restore-PowerBaseline {
  # register-service.ps1 records the AC sleep/hibernate/disk timeouts and the hibernation
  # state ONCE, before WatchLog first changes them (Secrets\power-baseline.json, never
  # overwritten). Uninstall puts exactly those values back.
  if (-not (Test-Path -LiteralPath $PowerBaselinePath)) {
    Write-Stage "no power baseline recorded (installed before 5.1.1): power settings are left as they are"
    return
  }
  try { $b = Get-Content -LiteralPath $PowerBaselinePath -Raw | ConvertFrom-Json } catch {
    Write-Stage "power baseline unreadable; power settings are left as they are"
    return
  }
  $pc = Get-PowerCfg
  $scheme = [string]$b.scheme_guid
  if ($scheme -notmatch '^[0-9a-fA-F-]{36}$') { $scheme = "SCHEME_CURRENT" }
  foreach ($s in @($b.settings | Where-Object { $null -ne $_ })) {
    if ($null -eq $s.ac_value) { continue }
    $value = [string][int64]$s.ac_value
    & $pc /setacvalueindex $scheme ([string]$s.subgroup) ([string]$s.setting) $value 2>$null | Out-Null
    Write-Stage "power: restored $([string]$s.name) (AC) to $value s (exit $LASTEXITCODE)"
  }
  $active = ""
  try {
    $out = (& $pc /getactivescheme 2>$null) -join " "
    if ($out -match '([0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12})') { $active = $Matches[1] }
  } catch {}
  if ($scheme -eq "SCHEME_CURRENT" -or $active -ieq $scheme) { & $pc /setactive $scheme 2>$null | Out-Null }
  if ($null -ne $b.hibernate_enabled -and [int]$b.hibernate_enabled -eq 1) {
    & $pc /hibernate on 2>$null | Out-Null
    Write-Stage "power: hibernation turned back on (exit $LASTEXITCODE)"
  }
  Remove-Item -LiteralPath $PowerBaselinePath -Force -ErrorAction SilentlyContinue
}

# Uninstall policy (docs/release/WINDOWS_INSTALLER_SOURCE_OF_TRUTH.md, "Machine changes and
# uninstall"): WatchLog has no "keep my site for a reinstall" choice, so uninstall removes
# every identity, credential, queue, cache and staging file it created. Only support logs stay.
$UninstallKeepNames = @("agent.log","agent.log.old","upgrade.log","repair-upgrade.log",
                        "repair-upgrade-result.ini","setup.log")
$UninstallInstallDirLeftovers = @("watchlog.defaults.ini","watchlog-agent.exe.remote.bak",
                                  "watchlog-agent.next.verify","watchlog-agent.exe.wlbak",
                                  "wl-upgrade-recover.ps1")

function Remove-UninstallLeftovers {
  if ((Split-Path -Leaf $DataRoot) -ne "WatchLog") {
    Write-Stage "refusing to clean '$DataRoot': not a WatchLog data folder"
    return
  }
  if (Test-Path -LiteralPath $DataRoot) {
    foreach ($item in @(Get-ChildItem -LiteralPath $DataRoot -Force -ErrorAction SilentlyContinue)) {
      if ($UninstallKeepNames -contains $item.Name) { continue }
      try {
        Remove-Item -LiteralPath $item.FullName -Recurse -Force -ErrorAction Stop
      } catch {
        Write-Stage "could not remove $($item.Name): $($_.Exception.Message)"
      }
    }
  }
  foreach ($name in $UninstallInstallDirLeftovers) {
    Remove-Item -LiteralPath (Join-Path $InstallDir $name) -Force -ErrorAction SilentlyContinue
  }
}

function Remove-WatchLogTasks {
  $names = @($TaskName, $RecoveryTaskName)
  try {
    $names += @(Get-ScheduledTask -TaskName "WatchLog Candidate Preflight *" -ErrorAction SilentlyContinue |
                ForEach-Object { $_.TaskName })
  } catch {}
  foreach ($name in $names) {
    try {
      Stop-ScheduledTask -TaskName $name -ErrorAction SilentlyContinue | Out-Null
      Unregister-ScheduledTask -TaskName $name -Confirm:$false -ErrorAction Stop | Out-Null
      Write-Stage "removed scheduled task '$name'"
    } catch {}
  }
}

switch ($Stage) {
  'preflight' {
    Write-Stage "payload profile=$PayloadProfile files=$($PayloadFiles -join ', ')"
    Write-Stage "existing binary present=$([bool](Test-Path $AgentExe)) file_version=$(Get-FileProductVersion $AgentExe)"
    $taskSnapshot = Suspend-Task

    try {
      Stop-WatchLogRuntime

      $agentCount = (Get-AgentProcesses).Count
      $setupCount = (Get-SetupProcesses).Count
      $launcherCount = (Get-LauncherProcesses).Count
      if ($agentCount -gt 0 -or $setupCount -gt 0 -or $launcherCount -gt 0) {
        Resume-Task ([bool]$taskSnapshot.present) ([bool]$taskSnapshot.enabled)
        Fail 10 "WatchLog processes are still running after bounded forced shutdown (agent=$agentCount setup=$setupCount launcher=$launcherCount)"
      }

      # Antivirus/indexing and PyInstaller teardown can hold the just-stopped EXE
      # for more than 10 seconds on older site PCs. Use the full bounded stop
      # budget and keep draining only WatchLog-owned processes while waiting.
      $unlockDeadline = (Get-Date).AddSeconds($StopTimeoutSec)
      $locked = @(Get-LockedPayloadFiles)
      $lastLocked = ""
      while ($locked.Count -gt 0 -and (Get-Date) -lt $unlockDeadline) {
        $joined = ($locked -join ", ")
        if ($joined -ne $lastLocked) {
          Write-Stage "waiting for payload lock release: $joined"
          $lastLocked = $joined
        }
        Stop-Pids (Get-LauncherProcesses) "late WatchLog launcher" -Force -Tree
        Stop-Pids (Get-SetupProcesses) "late WatchLog Setup UI" -Force
        Stop-Pids (Get-AgentProcesses) "late watchlog-agent.exe" -Force
        Start-Sleep -Milliseconds 500
        $locked = @(Get-LockedPayloadFiles)
      }
      if ($locked.Count -gt 0) {
        Resume-Task ([bool]$taskSnapshot.present) ([bool]$taskSnapshot.enabled)
        Fail 10 ("WatchLog update files stayed locked for $StopTimeoutSec s: " + ($locked -join ", "))
      }

      Backup-Payload $taskSnapshot
      if ($ArmRecovery) { Arm-Recovery }
      Write-Stage "preflight OK: task suspended, WatchLog launcher/UI/agent stopped, all payload files unlocked and backed up"
      exit 0
    } catch {
      Resume-Task ([bool]$taskSnapshot.present) ([bool]$taskSnapshot.enabled)
      Fail 10 ("upgrade preflight failed before replacement: " + $_.Exception.Message)
    }
  }

  'verify-version' {
    if ([string]::IsNullOrWhiteSpace($ExpectedVersion)) { Fail 20 "verify-version requires -ExpectedVersion" }
    if (-not (Test-Path -LiteralPath $AgentExe)) { Fail 11 "watchlog-agent.exe is missing after staging" }
    $fileVer = Get-FileProductVersion $AgentExe
    $runVer  = Get-RuntimeVersion $AgentExe
    Write-Stage "installed file_version=$fileVer runtime_version=$runVer expected=$ExpectedVersion path=$AgentExe"
    if ($fileVer -ne $ExpectedVersion) { Fail 11 "file ProductVersion '$fileVer' != expected '$ExpectedVersion'" }
    if ($runVer -ne $ExpectedVersion) { Fail 11 "runtime --version '$runVer' != expected '$ExpectedVersion'" }
    # A Setup UI left at the old version beside a new Agent is a mixed-version install, but
    # only when the caller replaced it: the repair payload always carries it and full Setup
    # passes -VerifySetupUi. The in-app updater replaces the Agent alone, with the default
    # profile, so the Setup UI it leaves untouched must not fail (and roll back) the update.
    if ($PayloadProfile -eq "repair" -or $VerifySetupUi) {
      $uiVer = Get-FileProductVersion $SetupExe
      Write-Stage "installed setup UI file_version=$uiVer expected=$ExpectedVersion path=$SetupExe"
      if ($uiVer -ne $ExpectedVersion) { Fail 11 "setup UI file ProductVersion '$uiVer' != expected '$ExpectedVersion'" }
    }
    Write-Stage "verify-version OK: on-disk file + runtime both report $ExpectedVersion"
    exit 0
  }

  'commit' {
    if ([string]::IsNullOrWhiteSpace($ExpectedVersion)) { Fail 20 "commit requires -ExpectedVersion" }
    if (-not (Test-Path -LiteralPath $AgentExe)) { Fail 11 "watchlog-agent.exe is missing at commit" }
    if ((Get-FileProductVersion $AgentExe) -ne $ExpectedVersion) {
      Fail 11 "on-disk version changed unexpectedly before commit"
    }

    # The orchestrator has already required fresh heartbeat + recorder +
    # remote-update-poll health from this exact version. Do not invalidate that
    # stronger proof with a later CIM/WMI process-path lookup: real field PCs can
    # hide SYSTEM-owned ExecutablePath/CommandLine and falsely report no Agent.
    $task = Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
    if (-not $task) { Fail 12 "WatchLog background task is missing at final commit" }
    if ([string]$task.State -eq "Disabled") {
      try { Enable-ScheduledTask -TaskName $TaskName -ErrorAction Stop | Out-Null } catch {
        Fail 12 "WatchLog background task could not be enabled at final commit"
      }
    }
    try { Start-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue | Out-Null } catch {}

    $deadline = (Get-Date).AddSeconds([Math]::Max(10, $StartTimeoutSec))
    $running = $false
    while ((Get-Date) -lt $deadline) {
      $task = Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
      if ($task -and [string]$task.State -eq "Running") { $running = $true; break }
      Start-Sleep -Milliseconds 500
    }
    if (-not $running) { Fail 12 "WatchLog background task did not remain Running at final commit" }

    Clear-Backup
    Disarm-Recovery
    Write-Stage "commit OK: version verified ($ExpectedVersion), fresh runtime health already proven, background task Running"
    exit 0
  }

  'rollback' {
    Write-Stage "rolling back the complete WatchLog payload"
    $restored = Invoke-RollbackRestore
    # A field site must never be left dark: re-register/start the restored payload even when
    # the restore itself failed or the task was Disabled before the upgrade.
    $startCode = Invoke-RollbackStart $restored.meta ([bool]$ProveHealth)
    Complete-Rollback $restored.code $startCode
  }

  'rollback-restore' {
    Write-Stage "rolling back: restoring the complete previous WatchLog payload (task stays suspended)"
    $restored = Invoke-RollbackRestore
    if ($restored.code -ne 0) { Fail 16 "previous WatchLog payload could not be restored; run rollback-start to re-enable the task" }
    Write-Stage "rollback-restore OK: previous payload restored; the task is started by rollback-start"
    exit 0
  }

  'rollback-start' {
    $meta = Read-BackupManifest
    $restoreCode = if ($meta -and [bool]$meta.restore_failed) { 16 } else { 0 }
    $startCode = Invoke-RollbackStart $meta ([bool]$ProveHealth)
    Complete-Rollback $restoreCode $startCode
  }

  'recover' {
    $marker = Read-Marker
    if (-not $marker) {
      Disarm-Recovery
      exit 0
    }
    if (Test-OwnerAlive $marker) {
      Write-Stage "upgrade owner pid=$([string]$marker.owner_pid) is still running; nothing to recover"
      exit 0
    }
    # Restore exactly the payload the interrupted upgrade backed up.
    if (@('full','repair') -contains [string]$marker.payload_profile) {
      $PayloadProfile = [string]$marker.payload_profile
      $PayloadFiles = if ($PayloadProfile -eq "repair") { @($RepairPayloadFiles) } else { @($FullPayloadFiles) }
    }
    $attempt = [int]$marker.attempts + 1
    $marker | Add-Member -NotePropertyName attempts -NotePropertyValue $attempt -Force
    Write-Marker $marker
    if ($attempt -gt $RecoveryMaxAttempts) {
      Write-RecoveryResult 18 "WatchLog could not restore itself after an interrupted update ($RecoveryMaxAttempts attempts). Do not uninstall WatchLog; contact support with upgrade.log."
      Disarm-Recovery
      Fail 18 "giving up after $RecoveryMaxAttempts recovery attempts"
    }
    Write-Stage "INTERRUPTED UPGRADE: owner pid=$([string]$marker.owner_pid) is gone while WatchLog was paused; restoring the previous WatchLog (attempt $attempt/$RecoveryMaxAttempts)"
    $restored = Invoke-RollbackRestore
    Undo-InterruptedRegistryStaging $marker
    $startCode = Invoke-RollbackStart $restored.meta ([bool]$marker.prove_health)
    $proven = ($restored.code -eq 0 -and $startCode -eq 0)
    Write-RecoveryResult $(if ($proven) { 0 } else { [Math]::Max($restored.code, $startCode) }) $(if ($proven) {
      "The update was interrupted (the PC restarted or the installer was stopped). WatchLog restored the previous version and proved it is running."
    } else {
      "The update was interrupted and WatchLog restored the previous files, but could not prove the previous version is running (restore=$($restored.code) start=$startCode). Do not uninstall WatchLog; contact support with upgrade.log."
    })
    Complete-Rollback $restored.code $startCode
  }

  'uninstall' {
    Write-Stage "uninstall: stopping WatchLog and removing what it added to this PC"
    try { $null = Suspend-Task } catch { Write-Stage "task suspend failed: $($_.Exception.Message)" }
    Stop-WatchLogRuntime
    $unlockDeadline = (Get-Date).AddSeconds($StopTimeoutSec)
    while ((Get-LockedPayloadFiles).Count -gt 0 -and (Get-Date) -lt $unlockDeadline) {
      Stop-Pids (Get-LauncherProcesses) "late WatchLog launcher" -Force -Tree
      Stop-Pids (Get-AgentProcesses) "late watchlog-agent.exe" -Force
      Start-Sleep -Milliseconds 500
    }
    Remove-WatchLogTasks
    Restore-PowerBaseline
    Remove-UninstallLeftovers
    $left = (Get-AgentProcesses).Count + (Get-SetupProcesses).Count + (Get-LauncherProcesses).Count
    if ($left -gt 0) { Fail 10 "uninstall: $left WatchLog process(es) still running" }
    Write-Stage "uninstall: WatchLog stopped; tasks, power settings and data removed (support logs kept)"
    exit 0
  }
}
