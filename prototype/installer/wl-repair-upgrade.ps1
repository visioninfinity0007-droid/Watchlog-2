<#
  WatchLog Existing-Site Repair/Upgrade orchestrator.

  Existing-site only. No discovery, no re-enrollment, no recorder credential entry.

  Safety order:
    1. Verify candidate payload/version.
    2. Run the staged candidate as SYSTEM against existing config/DPAPI identity
       while the current WatchLog remains running. No installed file is touched.
    3. Candidate failure => stop immediately; current WatchLog remains untouched.
    4. Candidate pass => suspend WatchLog task, stop old runtime and back up old payload.
    5. Copy staged payload, verify version, register/start task.
    6. Require fresh cloud heartbeat + recorder + remote-update poll proof.
    7. Commit only after health proof; otherwise rollback and prove old Agent restarted.
#>
[CmdletBinding()]
param(
  [Parameter(Mandatory=$true)][string]$CandidateDir,
  [Parameter(Mandatory=$true)][string]$InstallDir,
  [Parameter(Mandatory=$true)][string]$ExpectedVersion,
  [string]$TaskName = "WatchLog Agent",
  [int]$PreflightTimeoutSec = 75,
  [int]$HealthTimeoutSec = 120
)

$ErrorActionPreference = "Stop"
$CandidateDir = [IO.Path]::GetFullPath($CandidateDir).TrimEnd('\')
$InstallDir = [IO.Path]::GetFullPath($InstallDir).TrimEnd('\')
$DataRoot = Join-Path $env:ProgramData "WatchLog"
$LogPath = Join-Path $DataRoot "repair-upgrade.log"
$HealthPath = Join-Path $DataRoot "Secrets\runtime-health.json"
$ConfigPath = Join-Path $InstallDir "watchlog.ini"
$StatePath = Join-Path $DataRoot "agent_state.json"
$AgentKeyPath = Join-Path $DataRoot "Secrets\agent_key.dpapi"
$RecorderCredentialPath = Join-Path $DataRoot "Secrets\nvr_credential.dpapi"
$CandidateAgent = Join-Path $CandidateDir "watchlog-agent.exe"
$UpgradeHelper = Join-Path $CandidateDir "wl-upgrade.ps1"
$RegisterService = Join-Path $CandidateDir "register-service.ps1"
$PreflightResult = Join-Path $CandidateDir ("repair-preflight-" + [guid]::NewGuid().ToString("N") + ".json")
$PreflightTask = "WatchLog Candidate Preflight " + [guid]::NewGuid().ToString("N")

$PayloadFiles = @(
  "watchlog-agent.exe",
  "run-agent.ps1",
  "register-service.ps1",
  "apply-remote-update.ps1",
  "wl-upgrade.ps1",
  "watchlog.defaults.ini"
)

function Write-Repair([string]$Message) {
  $line = "{0}  {1}" -f (Get-Date -Format o), $Message
  try {
    New-Item -ItemType Directory -Force -Path $DataRoot | Out-Null
    Add-Content -LiteralPath $LogPath -Value $line
  } catch {}
  Write-Host $line
}

function Fail([int]$Code, [string]$Message) {
  Write-Repair "FAILURE($Code): $Message"
  exit $Code
}

function File-Version([string]$Path) {
  try { return ([string](Get-Item -LiteralPath $Path).VersionInfo.ProductVersion).Trim() }
  catch { return "" }
}

function Runtime-Version([string]$Path) {
  try {
    $out = & $Path --version 2>$null
    if ($LASTEXITCODE -ne 0) { return "" }
    return ([string]($out | Select-Object -First 1)).Trim()
  } catch { return "" }
}

function Protect-CandidateDirectory {
  # The candidate is executed as SYSTEM. Remove inherited/user-writable ACLs
  # before that launch to prevent local DLL/EXE replacement between extraction
  # and preflight.
  $system = New-Object System.Security.Principal.SecurityIdentifier("S-1-5-18")
  $admins = New-Object System.Security.Principal.SecurityIdentifier("S-1-5-32-544")
  $acl = New-Object System.Security.AccessControl.DirectorySecurity
  $acl.SetOwner($admins)
  $acl.SetAccessRuleProtection($true, $false)
  $inherit = [System.Security.AccessControl.InheritanceFlags]"ContainerInherit, ObjectInherit"
  $prop = [System.Security.AccessControl.PropagationFlags]::None
  $allow = [System.Security.AccessControl.AccessControlType]::Allow
  foreach ($sid in @($system,$admins)) {
    $rule = New-Object System.Security.AccessControl.FileSystemAccessRule(
      $sid, [System.Security.AccessControl.FileSystemRights]::FullControl,
      $inherit, $prop, $allow)
    [void]$acl.AddAccessRule($rule)
  }
  Set-Acl -LiteralPath $CandidateDir -AclObject $acl

  $check = Get-Acl -LiteralPath $CandidateDir
  $allowed = @("S-1-5-18","S-1-5-32-544")
  foreach ($rule in $check.Access) {
    $sid = try { $rule.IdentityReference.Translate([System.Security.Principal.SecurityIdentifier]).Value }
           catch { [string]$rule.IdentityReference }
    if ($rule.AccessControlType -eq "Allow" -and $allowed -notcontains $sid) {
      throw "candidate directory has unexpected writable principal $sid"
    }
  }
  Write-Repair "candidate directory ACL verified: SYSTEM + Administrators only"
}

function Invoke-UpgradeHelper([string]$Stage, [string[]]$Extra = @()) {
  $args = @(
    "-NoProfile","-ExecutionPolicy","Bypass",
    "-File",$UpgradeHelper,
    "-Stage",$Stage,
    "-InstallDir",$InstallDir,
    "-TaskName",$TaskName
  ) + $Extra
  & powershell.exe @args
  return $LASTEXITCODE
}

function Restore-Previous([string]$Why) {
  Write-Repair "restoring previous WatchLog: $Why"
  $rc = Invoke-UpgradeHelper "rollback"
  if ($rc -ne 0) {
    Write-Repair "ROLLBACK FAILURE($rc): previous payload restore/restart could not be proven"
    return $false
  }
  Write-Repair "previous WatchLog restored and running"
  return $true
}

function Run-Candidate-AsSystem {
  Remove-Item -LiteralPath $PreflightResult -Force -ErrorAction SilentlyContinue
  $argLine = '--preflight-existing-site --preflight-mode "passive" --config "{0}" --preflight-json "{1}"' -f $ConfigPath, $PreflightResult
  $action = New-ScheduledTaskAction -Execute $CandidateAgent -Argument $argLine
  $trigger = New-ScheduledTaskTrigger -Once -At (Get-Date).AddMinutes(1)
  $principal = New-ScheduledTaskPrincipal -UserId "SYSTEM" -LogonType ServiceAccount -RunLevel Highest
  $settings = New-ScheduledTaskSettingsSet -ExecutionTimeLimit (New-TimeSpan -Minutes 3) -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries

  Register-ScheduledTask -TaskName $PreflightTask -Action $action -Trigger $trigger -Principal $principal -Settings $settings -Force | Out-Null
  try {
    Start-ScheduledTask -TaskName $PreflightTask
    $deadline = (Get-Date).AddSeconds($PreflightTimeoutSec)
    while ((Get-Date) -lt $deadline) {
      if (Test-Path -LiteralPath $PreflightResult) {
        try {
          $obj = Get-Content -LiteralPath $PreflightResult -Raw | ConvertFrom-Json
          if ($null -ne $obj.ok) { return $obj }
        } catch {}
      }
      Start-Sleep -Milliseconds 500
    }
    return $null
  }
  finally {
    try { Stop-ScheduledTask -TaskName $PreflightTask -ErrorAction SilentlyContinue } catch {}
    try { Unregister-ScheduledTask -TaskName $PreflightTask -Confirm:$false -ErrorAction SilentlyContinue } catch {}
  }
}

function Run-RecorderCandidate {
  Remove-Item -LiteralPath $PreflightResult -Force -ErrorAction SilentlyContinue
  & $CandidateAgent --preflight-existing-site --preflight-mode recorder --config $ConfigPath --preflight-json $PreflightResult | Out-Host
  $rc = $LASTEXITCODE
  if ($rc -ne 0 -or -not (Test-Path -LiteralPath $PreflightResult)) { return $null }
  try { return Get-Content -LiteralPath $PreflightResult -Raw | ConvertFrom-Json }
  catch { return $null }
}

function Install-CandidatePayload {
  foreach ($name in $PayloadFiles) {
    $src = Join-Path $CandidateDir $name
    $dst = Join-Path $InstallDir $name
    if (-not (Test-Path -LiteralPath $src)) { throw "candidate payload missing $name" }
    Copy-Item -LiteralPath $src -Destination $dst -Force
  }
}

function Wait-NewRuntimeHealth([datetime]$StartedAtUtc) {
  $deadline = (Get-Date).AddSeconds($HealthTimeoutSec)
  while ((Get-Date) -lt $deadline) {
    try {
      if (Test-Path -LiteralPath $HealthPath) {
        $h = Get-Content -LiteralPath $HealthPath -Raw | ConvertFrom-Json
        $heartbeat = if ($h.heartbeat_at) { [DateTimeOffset]::Parse([string]$h.heartbeat_at).UtcDateTime } else { $null }
        $recorder = if ($h.recorder_seen_at) { [DateTimeOffset]::Parse([string]$h.recorder_seen_at).UtcDateTime } else { $null }
        $updater = if ($h.remote_update_poll_at) { [DateTimeOffset]::Parse([string]$h.remote_update_poll_at).UtcDateTime } else { $null }
        if ([string]$h.agent_version -eq $ExpectedVersion -and
            $heartbeat -and $heartbeat -ge $StartedAtUtc -and
            $recorder -and $recorder -ge $StartedAtUtc -and
            $updater -and $updater -ge $StartedAtUtc) {
          return $h
        }
      }
    } catch {}
    Start-Sleep -Seconds 1
  }
  return $null
}

try {
  Write-Repair "WatchLog Repair/Upgrade target=$ExpectedVersion candidate=$CandidateDir install=$InstallDir"

  foreach ($path in @($ConfigPath,$StatePath,$AgentKeyPath,$RecorderCredentialPath)) {
    if (-not (Test-Path -LiteralPath $path)) {
      Fail 20 "this PC is not ready for Repair/Upgrade (missing $(Split-Path -Leaf $path)); use the full WatchLog installer"
    }
  }
  foreach ($name in $PayloadFiles) {
    if (-not (Test-Path -LiteralPath (Join-Path $CandidateDir $name))) {
      Fail 21 "repair package is incomplete: missing $name"
    }
  }

  Protect-CandidateDirectory

  $fileVer = File-Version $CandidateAgent
  $runVer = Runtime-Version $CandidateAgent
  Write-Repair "candidate version file=$fileVer runtime=$runVer expected=$ExpectedVersion"
  if ($fileVer -ne $ExpectedVersion -or $runVer -ne $ExpectedVersion) {
    Fail 22 "candidate executable version does not match this Repair/Upgrade release"
  }

  Write-Repair "phase 1/2: passive candidate validation while current WatchLog remains untouched"
  $result = Run-Candidate-AsSystem
  if (-not $result -or -not [bool]$result.ok) {
    $detail = if ($result -and $result.error) { [string]$result.error } else { "passive candidate preflight timed out or returned no valid result" }
    Fail 30 ("candidate is not compatible with this site; installed WatchLog was NOT changed. " + $detail)
  }

  Write-Repair "passive preflight PASSED: identity + DPAPI + cloud + decoder + signed updater; current WatchLog still running"

  $rc = Invoke-UpgradeHelper "preflight"
  if ($rc -ne 0) {
    Fail 23 "candidate passed passive checks, but current WatchLog could not be safely paused/unlocked; no payload files were replaced"
  }

  Write-Repair "phase 2/2: current WatchLog paused/backed up; validating recorder/channels before replacing files"
  $recorderResult = Run-RecorderCandidate
  if (-not $recorderResult -or -not [bool]$recorderResult.ok) {
    $detail = if ($recorderResult -and $recorderResult.error) { [string]$recorderResult.error } else { "recorder candidate preflight failed or returned no valid result" }
    $restored = Restore-Previous $detail
    if (-not $restored) { Fail 31 "recorder preflight failed AND previous WatchLog could not be proven running" }
    Fail 30 "candidate recorder/channel validation failed; previous WatchLog was restored and kept"
  }

  Write-Repair "recorder preflight PASSED; beginning atomic payload replacement"

  try {
    Install-CandidatePayload
  } catch {
    $restored = Restore-Previous ("payload copy failed: " + $_.Exception.Message)
    if (-not $restored) { Fail 33 "payload copy failed AND rollback could not be proven" }
    Fail 32 "could not install candidate payload; previous WatchLog restored"
  }

  $rc = Invoke-UpgradeHelper "verify-version" @("-ExpectedVersion",$ExpectedVersion)
  if ($rc -ne 0) {
    $restored = Restore-Previous "installed version verification failed"
    if (-not $restored) { Fail 35 "version verification failed AND rollback could not be proven" }
    Fail 34 "installed candidate version could not be verified; previous WatchLog restored"
  }

  $started = [DateTime]::UtcNow
  try {
    & powershell.exe -NoProfile -ExecutionPolicy Bypass -File $RegisterService -InstallDir $InstallDir
    if ($LASTEXITCODE -ne 0) { throw "register-service returned $LASTEXITCODE" }
  } catch {
    $restored = Restore-Previous ("new background task failed: " + $_.Exception.Message)
    if (-not $restored) { Fail 37 "new task failed AND rollback could not be proven" }
    Fail 36 "new WatchLog could not start; previous WatchLog restored"
  }

  Write-Repair "new WatchLog started; waiting for cloud + recorder + online-update health proof"
  $health = Wait-NewRuntimeHealth $started
  if (-not $health) {
    $restored = Restore-Previous "new runtime did not prove heartbeat + recorder + remote-update polling"
    if (-not $restored) { Fail 39 "new runtime unhealthy AND rollback could not be proven" }
    Fail 38 "new WatchLog did not become fully healthy; previous WatchLog restored"
  }

  $rc = Invoke-UpgradeHelper "commit" @("-ExpectedVersion",$ExpectedVersion)
  if ($rc -ne 0) {
    $restored = Restore-Previous "final commit verification failed"
    if (-not $restored) { Fail 41 "commit failed AND rollback could not be proven" }
    Fail 40 "final verification failed; previous WatchLog restored"
  }

  Write-Repair "SUCCESS: WatchLog $ExpectedVersion healthy; cloud heartbeat + recorder + remote-update polling proven"
  exit 0
}
finally {
  try { Remove-Item -LiteralPath $PreflightResult -Force -ErrorAction SilentlyContinue } catch {}
}
