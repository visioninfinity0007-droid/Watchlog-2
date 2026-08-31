<#
  Register the WatchLog background service and harden power settings.
  Called by the Inno installer AFTER setup has written watchlog.ini.
  Runs elevated (the installer requires admin).

  "Service" here is a SYSTEM scheduled task that starts at boot, runs with
  no window (session 0), and restarts on failure. This meets every
  requirement - background, no terminal, auto-restart on boot - and, unlike
  a native service wrapped around a frozen Python exe, it is reliable to
  package and easy to verify. A true services.msc entry can be added later
  with a wrapper if the console listing is specifically wanted.
#>
param([string]$InstallDir = "$env:ProgramFiles\WatchLog")

$ErrorActionPreference = "Stop"
$task = "WatchLog Agent"
$data = Join-Path $env:ProgramData "WatchLog"
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

# --- (re)create the startup task -----------------------------------------
schtasks /Query /TN $task >$null 2>&1
if ($LASTEXITCODE -eq 0) { schtasks /Delete /TN $task /F >$null 2>&1 }

$action    = New-ScheduledTaskAction -Execute (Join-Path $InstallDir "run-agent.cmd")
$trigger   = New-ScheduledTaskTrigger -AtStartup
$principal = New-ScheduledTaskPrincipal -UserId "SYSTEM" -LogonType ServiceAccount -RunLevel Highest
$settings  = New-ScheduledTaskSettingsSet `
                -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries `
                -StartWhenAvailable -RestartCount 999 -RestartInterval (New-TimeSpan -Minutes 1) `
                -ExecutionTimeLimit (New-TimeSpan -Seconds 0) -MultipleInstances IgnoreNew

Register-ScheduledTask -TaskName $task -Action $action -Trigger $trigger `
    -Principal $principal -Settings $settings -Force | Out-Null
Start-ScheduledTask -TaskName $task

Start-Sleep -Seconds 2
$state = (Get-ScheduledTask -TaskName $task -ErrorAction SilentlyContinue).State
Write-Host "  WatchLog background service registered and started (state: $state)."
