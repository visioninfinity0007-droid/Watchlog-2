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

# The Site Agent is only useful while the site PC is awake, so WatchLog keeps it from sleeping
# on mains power. These are machine-wide changes WatchLog owns for its lifetime: the values
# they replace are recorded ONCE, before the first change, in Secrets\power-baseline.json
# (never overwritten by Repair, rollback or a re-run), and the uninstaller restores them
# (wl-upgrade.ps1 -Stage uninstall). A site first installed before 5.1.1 had these changed
# without a record; its first baseline holds WatchLog's own values, so uninstall changes
# nothing there. See WINDOWS_INSTALLER_SOURCE_OF_TRUTH.md "Machine changes and uninstall".
$PowerCfg = Join-Path $env:SystemRoot "System32\powercfg.exe"
$PowerBaselinePath = Join-Path $data "Secrets\power-baseline.json"
$PowerSettings = @(
  @{ name = "standby-timeout-ac";   subgroup = "SUB_SLEEP"; setting = "STANDBYIDLE" },
  @{ name = "hibernate-timeout-ac"; subgroup = "SUB_SLEEP"; setting = "HIBERNATEIDLE" },
  @{ name = "disk-timeout-ac";      subgroup = "SUB_DISK";  setting = "DISKIDLE" }
)

function Read-PowerAcValue([string]$Subgroup, [string]$Setting) {
  # powercfg /query prints, for one setting: min, max, increment, then the current AC index
  # and the current DC index, each as 0x%08x. The labels are localised; the order is not.
  try {
    $out = (& $PowerCfg /query SCHEME_CURRENT $Subgroup $Setting 2>$null) -join "`n"
    if ($LASTEXITCODE -ne 0) { return $null }
    $hex = [regex]::Matches($out, '0x[0-9a-fA-F]{8}')
    if ($hex.Count -lt 2) { return $null }
    return [Convert]::ToInt64($hex[$hex.Count - 2].Value.Substring(2), 16)
  } catch { return $null }
}

function Get-ActivePowerScheme {
  try {
    $out = (& $PowerCfg /getactivescheme 2>$null) -join " "
    if ($out -match '([0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12})') { return $Matches[1] }
  } catch {}
  return ""
}

function Get-HibernateEnabled {
  try {
    return [int](Get-ItemProperty -LiteralPath 'HKLM:\SYSTEM\CurrentControlSet\Control\Power' -Name HibernateEnabled -ErrorAction Stop).HibernateEnabled
  } catch { return $null }
}

function Save-PowerBaseline {
  if (Test-Path -LiteralPath $PowerBaselinePath) {
    Write-Host "  power baseline already recorded; not overwritten"
    return
  }
  $rows = @(foreach ($s in $PowerSettings) {
    [ordered]@{ name = $s.name; subgroup = $s.subgroup; setting = $s.setting
                ac_value = (Read-PowerAcValue $s.subgroup $s.setting) }
  })
  $baseline = [ordered]@{
    schema = "watchlog.power_baseline.v1"
    captured_at = [DateTimeOffset]::UtcNow.ToString("o")
    scheme_guid = (Get-ActivePowerScheme)
    hibernate_enabled = (Get-HibernateEnabled)
    settings = $rows
  }
  New-Item -ItemType Directory -Force -Path (Split-Path -Parent $PowerBaselinePath) | Out-Null
  $tmp = $PowerBaselinePath + ".tmp"
  $baseline | ConvertTo-Json -Depth 4 | Set-Content -LiteralPath $tmp -Encoding UTF8
  Move-Item -LiteralPath $tmp -Destination $PowerBaselinePath -Force
  Write-Host "  power baseline recorded for uninstall"
}

function Set-SiteAwakePower {
  foreach ($s in $PowerSettings) {
    & $PowerCfg /change $s.name 0 2>$null | Out-Null
    if ($LASTEXITCODE -ne 0) { Write-Host "  (power setting $($s.name) not changed: exit $LASTEXITCODE)" }
  }
  & $PowerCfg /hibernate off 2>$null | Out-Null
  if ($LASTEXITCODE -ne 0) { Write-Host "  (hibernation not turned off: exit $LASTEXITCODE)" }
}

# Record first. If the record cannot be written the site is still kept awake (monitoring
# needs it), and the log says the old values cannot be restored on uninstall.
try { Save-PowerBaseline } catch { Write-Host "  (power baseline not recorded; uninstall cannot restore power settings: $($_.Exception.Message))" }
try { Set-SiteAwakePower } catch { Write-Host "  (power settings: $($_.Exception.Message))" }

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
