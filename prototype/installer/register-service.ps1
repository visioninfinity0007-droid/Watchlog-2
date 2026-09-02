<#
  Register the WatchLog background Site Agent and harden power settings.
  Called by the authoritative NSIS installer AFTER recorder + enrollment setup
  has completed successfully. Runs elevated (the installer requires admin).

  "Service" here is a SYSTEM scheduled task that starts at boot, runs with
  no window (session 0), and restarts on failure. This meets the operational
  requirement: background, no terminal, startup after reboot, and restart after
  a process failure. Register-ScheduledTask -Force updates an existing task in
  place so an upgrade does not destructively delete its rollback path first.
#>
param([string]$InstallDir = "$env:ProgramFiles\WatchLog")

$ErrorActionPreference = "Stop"
$task = "WatchLog Agent"
$data = Join-Path $env:ProgramData "WatchLog"
$runner = Join-Path $InstallDir "run-agent.cmd"
if (-not (Test-Path $runner)) { throw "WatchLog runner not found: $runner" }
New-Item -ItemType Directory -Force -Path $data | Out-Null

# --- keep this PC awake: the agent is only useful while it runs ----------
# Never sleep or turn the disks off on AC. (Power-on-after-a-power-cut is a
# BIOS setting we cannot set from Windows - the installer README asks the
# installer to enable it there.)
try {
  powercfg /change standby-timeout-ac 0
  powercfg /change hibernate-timeout-ac 0
  powercfg /change disk-timeout-ac 0
  powercfg /hibernate off
} catch { Write-Host "  (power settings: $($_.Exception.Message))" }

# --- create/update the startup task --------------------------------------
$existing = Get-ScheduledTask -TaskName $task -ErrorAction SilentlyContinue
if ($existing -and $existing.State -eq "Running") {
  Stop-ScheduledTask -TaskName $task -ErrorAction Stop
  Start-Sleep -Milliseconds 500
}

$action    = New-ScheduledTaskAction -Execute $runner
$trigger   = New-ScheduledTaskTrigger -AtStartup
$principal = New-ScheduledTaskPrincipal -UserId "SYSTEM" -LogonType ServiceAccount -RunLevel Highest
$settings  = New-ScheduledTaskSettingsSet `
                -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries `
                -StartWhenAvailable -RestartCount 999 -RestartInterval (New-TimeSpan -Minutes 1) `
                -ExecutionTimeLimit (New-TimeSpan -Seconds 0) -MultipleInstances IgnoreNew

Register-ScheduledTask -TaskName $task -Action $action -Trigger $trigger `
    -Principal $principal -Settings $settings -Force | Out-Null
Start-ScheduledTask -TaskName $task -ErrorAction Stop

# Registration is not complete until Windows confirms the long-running wrapper
# is actually running. run-agent.cmd itself owns restart-on-agent-exit, so a
# healthy task remains Running instead of completing immediately.
$deadline = (Get-Date).AddSeconds(10)
$state = $null
while ((Get-Date) -lt $deadline) {
  Start-Sleep -Milliseconds 500
  $registered = Get-ScheduledTask -TaskName $task -ErrorAction SilentlyContinue
  if ($registered) {
    $state = [string]$registered.State
    if ($state -eq "Running") { break }
  }
}
if ($state -ne "Running") {
  throw "WatchLog scheduled task did not reach Running state (state: $state)"
}

Write-Host "  WatchLog background Site Agent registered and started (state: $state)."
