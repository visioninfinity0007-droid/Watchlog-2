<#
  WatchLog transactional upgrade helper.

  The installer must never try to overwrite files while the existing WatchLog
  runtime, launcher, or setup UI is still using them.

  Stages:
    preflight      - suspend the scheduled task, close/kill ONLY WatchLog-owned
                     processes from this InstallDir, verify EVERY replace-target
                     file is unlocked, and back up the current payload.
    verify-version - prove the newly staged agent file/runtime version (and the
                     staged Setup UI file version).
    commit         - prove the new registered agent is running as one logical
                     instance, then discard the rollback payload.
    rollback       - stop the new runtime, restore the complete previous payload,
                     re-enable/restart the task, and leave the old build working.

  No recorder credential, enrollment code, agent key or signing secret is read.
#>
[CmdletBinding()]
param(
  [Parameter(Mandatory = $true)]
  [ValidateSet('preflight','verify-version','commit','rollback')]
  [string]$Stage,
  [Parameter(Mandatory = $true)][string]$InstallDir,
  [string]$ExpectedVersion = "",
  [int]$StopTimeoutSec = 35,
  [int]$StartTimeoutSec = 30,
  [string]$TaskName = "WatchLog Agent",
  [ValidateSet('full','repair')]
  [string]$PayloadProfile = "full",
  [string]$DataRootOverride = ""
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
    # A Setup UI left at the old version beside a new Agent is a mixed-version install.
    # The repair payload always carries it; a full install verifies the one it wrote.
    if ($PayloadProfile -eq "repair" -or (Test-Path -LiteralPath $SetupExe)) {
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
    Write-Stage "commit OK: version verified ($ExpectedVersion), fresh runtime health already proven, background task Running"
    exit 0
  }

  'rollback' {
    Write-Stage "rolling back the complete WatchLog payload"
    # Disable/stop whatever task definition exists now so no process can race restore.
    $current = Suspend-Task
    Stop-WatchLogRuntime

    $meta = Restore-Payload
    if ($meta) {
      Write-Stage "restored previous WatchLog payload"
    } else {
      Write-Stage "no rollback manifest found; attempting to recover the current on-disk payload"
    }

    # A field site must never be left dark just because the task happened to be
    # Disabled before the upgrade or because a prior failed upgrade rewrote its
    # definition. Re-register/start the restored payload unconditionally.
    if (-not (Ensure-WatchLogBackgroundTask "rollback recovery")) {
      Clear-Backup
      Fail 14 "previous WatchLog payload was restored but its background task could not be repaired/restarted"
    }

    Clear-Backup
    Write-Stage "rollback complete: previous WatchLog payload restored AND background task Running"
    exit 0
  }
}
