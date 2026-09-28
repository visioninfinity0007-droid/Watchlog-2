<#
  WatchLog transactional upgrade helper.

  The installer must never try to overwrite files while the existing WatchLog
  runtime, launcher, or setup UI is still using them.

  Stages:
    preflight      - suspend the scheduled task, close/kill ONLY WatchLog-owned
                     processes from this InstallDir, verify EVERY replace-target
                     file is unlocked, and back up the current payload.
    verify-version - prove the newly staged agent file/runtime version.
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
  [int]$StopTimeoutSec = 20,
  [int]$StartTimeoutSec = 30,
  [string]$TaskName = "WatchLog Agent",
  [string]$DataRootOverride = ""
)

$ErrorActionPreference = "Stop"

$InstallDir = [System.IO.Path]::GetFullPath($InstallDir).TrimEnd('\')
$AgentExe   = Join-Path $InstallDir "watchlog-agent.exe"
$SetupExe   = Join-Path $InstallDir "watchlog-setup-ui.exe"
$RunnerPs1  = Join-Path $InstallDir "run-agent.ps1"
$RunnerCmd  = Join-Path $InstallDir "run-agent.cmd"
$LauncherPidFile = Join-Path $DataRoot "run-agent.pid"
$BackupExe  = Join-Path $InstallDir "watchlog-agent.exe.wlbak"   # compatibility / support breadcrumb
$DataRoot   = if ([string]::IsNullOrWhiteSpace($DataRootOverride)) {
  Join-Path $env:ProgramData "WatchLog"
} else {
  [System.IO.Path]::GetFullPath($DataRootOverride)
}
$UpgradeLog = Join-Path $DataRoot "upgrade.log"
$BackupRoot = Join-Path $DataRoot "upgrade-backup"
$Manifest   = Join-Path $BackupRoot "manifest.json"

# Every file NSIS replaces during the core payload extraction. Preflight proves
# ALL of these are writable before NSIS is allowed to touch the installation.
$PayloadFiles = @(
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

function Write-Stage([string]$msg) {
  $line = "{0}  [{1}]  {2}" -f (Get-Date -Format o), $Stage, $msg
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
  # Stop ONLY PowerShell/cmd launchers whose command line points to THIS
  # WatchLog install directory. Prefer the launcher PID file when present, but
  # verify the command line so a stale/reused PID is harmless. Then scan as a
  # fallback for Build 69 and other older launchers that never wrote run-agent.pid.
  $wantPs1 = [System.IO.Path]::GetFullPath($RunnerPs1).ToLowerInvariant()
  $wantCmd = [System.IO.Path]::GetFullPath($RunnerCmd).ToLowerInvariant()
  $out = @()
  $seen = @{}

  function Add-LauncherRecord($p) {
    if ($null -eq $p) { return }
    $name = ([string]$p.Name).ToLowerInvariant()
    if ($name -notin @("powershell.exe","pwsh.exe","cmd.exe")) { return }
    $line = ([string]$p.CommandLine).ToLowerInvariant()
    if (-not $line) { return }
    if (-not ($line.Contains($wantPs1) -or $line.Contains($wantCmd))) { return }
    $rec = Convert-ProcessRecord $p
    if ($null -eq $rec) { return }
    $key = [string]$rec.ProcessId
    if (-not $seen.ContainsKey($key)) {
      $seen[$key] = $true
      $script:__wl_launcher_records += $rec
    }
  }

  try {
    $script:__wl_launcher_records = @()

    if (Test-Path -LiteralPath $LauncherPidFile) {
      $pidText = ""
      try { $pidText = (Get-Content -LiteralPath $LauncherPidFile -Raw).Trim() } catch {}
      $pidValue = 0
      if ([int]::TryParse($pidText, [ref]$pidValue) -and $pidValue -gt 0) {
        $p = Get-CimInstance Win32_Process -Filter "ProcessId=$pidValue" -ErrorAction SilentlyContinue
        Add-LauncherRecord $p
      }
    }

    foreach ($p in @(Get-CimInstance Win32_Process -ErrorAction SilentlyContinue)) {
      Add-LauncherRecord $p
    }

    $out = @($script:__wl_launcher_records)
    Remove-Variable -Name __wl_launcher_records -Scope Script -ErrorAction SilentlyContinue
    return @($out)
  } catch {
    Remove-Variable -Name __wl_launcher_records -Scope Script -ErrorAction SilentlyContinue
    return @()
  }
}

function Stop-Pids([array]$Processes, [string]$Label, [switch]$Force) {
  foreach ($p in @($Processes)) {
    $pidValue = 0
    try { $pidValue = [int]$p.ProcessId } catch { $pidValue = 0 }
    if ($pidValue -le 0) {
      Write-Stage "refusing malformed $Label process record with no valid PID"
      continue
    }
    Write-Stage "stopping $Label pid=$pidValue path=$([string]$p.ExecutablePath)"
    try {
      if ($Force) {
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
  if ($t) {
    try { Stop-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue | Out-Null } catch {}
    try { Disable-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue | Out-Null } catch {}
    Start-Sleep -Milliseconds 500
  }
  return $snap
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
  Stop-Pids (Get-LauncherProcesses) "WatchLog launcher" -Force

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

      $unlockDeadline = (Get-Date).AddSeconds([Math]::Min($StopTimeoutSec, 10))
      $locked = @(Get-LockedPayloadFiles)
      while ($locked.Count -gt 0 -and (Get-Date) -lt $unlockDeadline) {
        Start-Sleep -Milliseconds 250
        $locked = @(Get-LockedPayloadFiles)
      }
      if ($locked.Count -gt 0) {
        Resume-Task ([bool]$taskSnapshot.present) ([bool]$taskSnapshot.enabled)
        Fail 10 ("WatchLog update files are still locked: " + ($locked -join ", "))
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
    Write-Stage "verify-version OK: on-disk file + runtime both report $ExpectedVersion"
    exit 0
  }

  'commit' {
    if ([string]::IsNullOrWhiteSpace($ExpectedVersion)) { Fail 20 "commit requires -ExpectedVersion" }
    if (-not (Test-Path -LiteralPath $AgentExe)) { Fail 11 "watchlog-agent.exe is missing at commit" }
    if ((Get-FileProductVersion $AgentExe) -ne $ExpectedVersion) {
      Fail 11 "on-disk version changed unexpectedly before commit"
    }

    # register-service.ps1 should already have recreated/enabled/started the task.
    $task = Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
    if ($task -and [string]$task.State -eq "Disabled") {
      try { Enable-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue | Out-Null } catch {}
    }
    if ($task) {
      try { Start-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue | Out-Null } catch {}
    }

    $deadline = (Get-Date).AddSeconds($StartTimeoutSec)
    $alive = $false
    while ((Get-Date) -lt $deadline) {
      if ((Get-AgentRuntimeLeaves).Count -ge 1) { $alive = $true; break }
      Start-Sleep -Milliseconds 500
    }
    if (-not $alive) { Fail 12 "the new agent did not start within $StartTimeoutSec s" }

    Start-Sleep -Seconds 3
    $count = (Get-AgentRuntimeLeaves).Count
    if ($count -eq 0) { Fail 12 "the new agent started but did not stay alive (crash loop)" }
    if ($count -gt 1) { Fail 13 "more than one watchlog-agent.exe logical runtime is running ($count) - duplicate runtime" }

    Clear-Backup
    Write-Stage "commit OK: version verified ($ExpectedVersion), single logical instance alive"
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
      Resume-Task ([bool]$meta.task_present) ([bool]$meta.task_enabled)
    } else {
      Write-Stage "no rollback payload found; leaving current task state unchanged"
      Resume-Task ([bool]$current.present) ([bool]$current.enabled)
    }

    # Restoring files is not enough. If the prior installation did not have an
    # enabled WatchLog task, or if that task cannot bring the previous agent back,
    # rollback must fail closed instead of claiming the site is healthy.
    $expectedTaskPresent = if ($meta) { [bool]$meta.task_present } else { [bool]$current.present }
    $expectedTaskEnabled = if ($meta) { [bool]$meta.task_enabled } else { [bool]$current.enabled }
    if (-not $expectedTaskPresent -or -not $expectedTaskEnabled) {
      Clear-Backup
      Fail 14 "previous WatchLog payload was restored but there is no enabled background task to restart it"
    }

    $rollbackDeadline = (Get-Date).AddSeconds([Math]::Max(10, $StartTimeoutSec))
    while ((Get-Date) -lt $rollbackDeadline) {
      if ((Get-AgentRuntimeLeaves).Count -ge 1) {
        Start-Sleep -Seconds 2
        if ((Get-AgentRuntimeLeaves).Count -ge 1) {
          Clear-Backup
          Write-Stage "rollback complete: previous WatchLog payload restored AND agent running"
          exit 0
        }
      }
      Start-Sleep -Milliseconds 500
    }

    # One final exact task nudge. The task name is fixed/known; no process-wide kill.
    try { & "$env:SystemRoot\System32\schtasks.exe" /Run /TN "$TaskName" | Out-Null } catch {}
    Start-Sleep -Seconds 3
    if ((Get-AgentRuntimeLeaves).Count -ge 1) {
      Clear-Backup
      Write-Stage "rollback complete after task nudge: previous agent running"
      exit 0
    }

    Clear-Backup
    Fail 14 "previous WatchLog payload was restored but the previous agent could not be restarted"
  }
}
