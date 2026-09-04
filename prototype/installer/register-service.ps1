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

$existing = Get-ScheduledTask -TaskName $task -ErrorAction SilentlyContinue
if ($existing -and $existing.State -eq "Running") {
  Stop-ScheduledTask -TaskName $task -ErrorAction Stop
  Start-Sleep -Milliseconds 500
}

$powershell = "$env:SystemRoot\System32\WindowsPowerShell\v1.0\powershell.exe"
$arguments = "-NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File `"$runner`" -InstallDir `"$InstallDir`""
$action    = New-ScheduledTaskAction -Execute $powershell -Argument $arguments
$trigger   = New-ScheduledTaskTrigger -AtStartup
$principal = New-ScheduledTaskPrincipal -UserId "SYSTEM" -LogonType ServiceAccount -RunLevel Highest
$settings  = New-ScheduledTaskSettingsSet `
                -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries `
                -StartWhenAvailable -RestartCount 999 -RestartInterval (New-TimeSpan -Minutes 1) `
                -ExecutionTimeLimit (New-TimeSpan -Seconds 0) -MultipleInstances IgnoreNew

Register-ScheduledTask -TaskName $task -Action $action -Trigger $trigger `
    -Principal $principal -Settings $settings -Force | Out-Null
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

Write-Host "  WatchLog background Site Agent registered and started (state: $state)."
