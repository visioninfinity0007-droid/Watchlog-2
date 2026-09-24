<#
  Register the WatchLog background Site Agent and harden power settings.
  Called by the authoritative NSIS installer AFTER recorder + enrollment setup
  has completed successfully. Runs elevated (the installer requires admin).

  The scheduled task runs as SYSTEM at boot. Its action is PowerShell with a
  hidden window, pointing at run-agent.ps1. That launcher unwraps the
  machine-scoped DPAPI recorder credential into a process-only environment
  variable, captures agent output to ProgramData, and restarts the agent if it
  exits unexpectedly.
#>
param([string]$InstallDir = "$env:ProgramFiles\WatchLog")

$ErrorActionPreference = "Stop"
$task = "WatchLog Agent"
$data = Join-Path $env:ProgramData "WatchLog"
$runner = Join-Path $InstallDir "run-agent.ps1"
if (-not (Test-Path $runner)) { throw "WatchLog runner not found: $runner" }
New-Item -ItemType Directory -Force -Path $data | Out-Null

# The Site Agent is only useful while the site PC is awake.
try {
  powercfg /change standby-timeout-ac 0
  powercfg /change hibernate-timeout-ac 0
  powercfg /change disk-timeout-ac 0
  powercfg /hibernate off
} catch { Write-Host "  (power settings: $($_.Exception.Message))" }

# Clear any prior instance, running or merely RECORDED as running. An unclean shutdown can
# leave Task Scheduler believing an instance is still alive; combined with
# -MultipleInstances IgnoreNew that would make the next -AtStartup trigger a silent no-op.
$existing = Get-ScheduledTask -TaskName $task -ErrorAction SilentlyContinue
if ($existing) {
  try { Stop-ScheduledTask -TaskName $task -ErrorAction SilentlyContinue } catch { }
  Start-Sleep -Milliseconds 500
}
# And kill any orphaned agent left behind by a stopped task, so the new instance is not
# refused and the exe is not locked.
try {
  Get-Process -Name "watchlog-agent" -ErrorAction SilentlyContinue |
    Stop-Process -Force -ErrorAction SilentlyContinue
} catch { }

$powershell = "$env:SystemRoot\System32\WindowsPowerShell\v1.0\powershell.exe"
$arguments = "-NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File `"$runner`" -InstallDir `"$InstallDir`""
$action    = New-ScheduledTaskAction -Execute $powershell -Argument $arguments
# TWO triggers, deliberately. -AtStartup alone means ANY death of the agent -- a crash, a
# launcher abort, an instance Windows still believes is running after an unclean shutdown --
# leaves the site dark until somebody reboots the PC. A CCTV site PC is exactly the machine
# nobody visits. The repeating watchdog is a harmless no-op while the agent is healthy,
# because -MultipleInstances IgnoreNew refuses a second instance.
$boot      = New-ScheduledTaskTrigger -AtStartup
# Give the network stack a moment; the agent tolerates a dead WAN now, but not racing it
# every single boot is still cheaper than retrying.
$boot.Delay = "PT30S"
$watchdog  = New-ScheduledTaskTrigger -Once -At (Get-Date).Date.AddMinutes(1) `
                -RepetitionInterval (New-TimeSpan -Minutes 5)
$trigger   = @($boot, $watchdog)
$principal = New-ScheduledTaskPrincipal -UserId "SYSTEM" -LogonType ServiceAccount -RunLevel Highest
$settings  = New-ScheduledTaskSettingsSet `
                -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries `
                -StartWhenAvailable -RestartCount 999 -RestartInterval (New-TimeSpan -Minutes 1) `
                -ExecutionTimeLimit (New-TimeSpan -Seconds 0) -MultipleInstances IgnoreNew

Register-ScheduledTask -TaskName $task -Action $action -Trigger $trigger `
    -Principal $principal -Settings $settings -Force | Out-Null

# Fresh proof only. setup_gui's foreground heartbeat may have written an older
# marker, so delete it before starting the SYSTEM task.
$ready = Join-Path $data "background-ready.json"
Remove-Item -Force $ready -ErrorAction SilentlyContinue
Start-ScheduledTask -TaskName $task -ErrorAction Stop

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

# Task Scheduler can report Running while run-agent.ps1 is merely supervising a
# child that is crashing/restarting. Require one fresh heartbeat marker from the
# actual packaged agent before calling setup complete.
$proofDeadline = (Get-Date).AddSeconds(25)
while ((Get-Date) -lt $proofDeadline -and -not (Test-Path $ready)) {
  Start-Sleep -Milliseconds 500
}
if (-not (Test-Path $ready)) {
  throw "WatchLog background agent started but did not prove a cloud heartbeat within 25 seconds"
}

Write-Host "  WatchLog background Site Agent registered, started and heartbeat-proven (state: $state)."
