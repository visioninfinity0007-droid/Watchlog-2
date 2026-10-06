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

# T0-SEC1: %ProgramData% lets a standard user create files and folders in a new subfolder.
# WatchLog's SYSTEM launcher acts on files under this folder (staged remote updates, the
# upgrade backup it restores into Program Files), so a local account must not be able to plant
# anything here. Owner Administrators; inheritance from ProgramData cut; SYSTEM and
# Administrators full control; Users read only. A subfolder that already has its own
# protected DACL (Secrets, the repair candidate, remote-update) keeps it. Fails closed.
function Protect-WatchLogData([string]$Path) {
  $acl = Get-Acl -LiteralPath $Path
  $acl.SetOwner((New-Object System.Security.Principal.SecurityIdentifier('S-1-5-32-544')))
  $acl.SetAccessRuleProtection($true, $false)
  foreach ($rule in @($acl.Access)) { [void]$acl.RemoveAccessRule($rule) }
  $inherit = [System.Security.AccessControl.InheritanceFlags]'ContainerInherit,ObjectInherit'
  $none = [System.Security.AccessControl.PropagationFlags]::None
  foreach ($grant in @(@('S-1-5-18', 'FullControl'), @('S-1-5-32-544', 'FullControl'),
                       @('S-1-5-32-545', 'ReadAndExecute'))) {
    $sid = New-Object System.Security.Principal.SecurityIdentifier($grant[0])
    $acl.AddAccessRule((New-Object System.Security.AccessControl.FileSystemAccessRule(
      $sid, $grant[1], $inherit, $none, 'Allow')))
  }
  Set-Acl -LiteralPath $Path -AclObject $acl
  $check = Get-Acl -LiteralPath $Path
  if (-not $check.AreAccessRulesProtected) { throw "WatchLog data folder still inherits permissions" }
  foreach ($rule in $check.Access) {
    if ($rule.AccessControlType -ne 'Allow') { continue }
    $sid = $rule.IdentityReference.Translate([System.Security.Principal.SecurityIdentifier]).Value
    $rights = [int]$rule.FileSystemRights
    $writeMask = [int]([System.Security.AccessControl.FileSystemRights]'WriteData,AppendData,WriteExtendedAttributes,WriteAttributes,Delete,DeleteSubdirectoriesAndFiles,ChangePermissions,TakeOwnership')
    if (@('S-1-5-18', 'S-1-5-32-544') -notcontains $sid -and ($rights -band $writeMask)) {
      throw "WatchLog data folder still lets $sid write"
    }
  }
}
Protect-WatchLogData $data

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
# And kill any orphaned agent left behind by a stopped task, but ONLY when its
# executable path belongs to THIS WatchLog install. Never broad-kill a same-named
# process from another location.
try {
  $agent = Join-Path $InstallDir "watchlog-agent.exe"
  $want = [System.IO.Path]::GetFullPath($agent)
  Get-CimInstance Win32_Process -Filter "Name='watchlog-agent.exe'" -ErrorAction SilentlyContinue |
    Where-Object {
      $_.ExecutablePath -and
      ([System.IO.Path]::GetFullPath([string]$_.ExecutablePath) -ieq $want)
    } |
    ForEach-Object {
      Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue
    }
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
