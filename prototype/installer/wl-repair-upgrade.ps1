<#
  WatchLog Existing-Site Repair/Upgrade orchestrator.

  Existing-site only. No discovery, no re-enrollment, no recorder credential entry.

  Safety order:
    1. Verify candidate payload/version.
    2. Run the staged candidate as SYSTEM against existing config/DPAPI identity
       while the current WatchLog remains running. No installed file is touched.
       A site with a recorder registry also has every configured recorder credential
       decrypted and loaded by the candidate (read-only).
    3. Candidate failure => stop immediately; current WatchLog remains untouched.
    4. Candidate pass => suspend WatchLog task, stop old runtime and back up old payload.
       The candidate then probes the recorder(s); with a registry, every configured
       recorder, which records the pre-upgrade baseline. A site without a registry has
       its legacy recorder staged into one: legacy read + verify, stable UUID,
       per-recorder DPAPI written + read back, registry published + re-read.
    5. Copy staged payload, verify version, register/start task.
    6. Require fresh cloud heartbeat + remote-update poll proof, and recorder proof:
       every recorder live, or on a multi-recorder site the continuity recorder plus
       every recorder that answered before the upgrade.
    7. Commit only after health proof; otherwise rollback (including a registry staged
       in step 4) and prove old Agent restarted. The legacy recorder settings are never
       retired here: the 5.1 runtime still runs a one-recorder site from them.
#>
[CmdletBinding()]
param(
  [Parameter(Mandatory=$true)][string]$CandidateDir,
  [Parameter(Mandatory=$true)][string]$InstallDir,
  [Parameter(Mandatory=$true)][string]$ExpectedVersion,
  [string]$TaskName = "WatchLog Agent",
  [int]$PreflightTimeoutSec = 75,
  [int]$HealthTimeoutSec = 120,
  [int]$RecorderPreflightAttempts = 3,
  [int]$RecorderRetryDelaySec = 5,
  [int]$RegistryStepTimeoutSec = 180
)

$ErrorActionPreference = "Stop"
$CandidateDir = [IO.Path]::GetFullPath($CandidateDir).TrimEnd('\')
$InstallDir = [IO.Path]::GetFullPath($InstallDir).TrimEnd('\')
$DataRoot = Join-Path $env:ProgramData "WatchLog"
$LogPath = Join-Path $DataRoot "repair-upgrade.log"
$ResultPath = Join-Path $DataRoot "repair-upgrade-result.ini"
$HealthPath = Join-Path $DataRoot "Secrets\runtime-health.json"
$script:CurrentStage = "initial checks"
$script:RecoveryState = "Your installed WatchLog has not been replaced."
$ConfigPath = Join-Path $InstallDir "watchlog.ini"
$StatePath = Join-Path $DataRoot "agent_state.json"
$AgentKeyPath = Join-Path $DataRoot "Secrets\agent_key.dpapi"
$RecorderCredentialPath = Join-Path $DataRoot "Secrets\nvr_credential.dpapi"
$CandidateAgent = Join-Path $CandidateDir "watchlog-agent.exe"
$CandidateSetupUi = Join-Path $CandidateDir "watchlog-setup-ui.exe"
$UpgradeHelper = Join-Path $CandidateDir "wl-upgrade.ps1"
$RegisterService = Join-Path $CandidateDir "register-service.ps1"
$PreflightResult = Join-Path $CandidateDir ("repair-preflight-" + [guid]::NewGuid().ToString("N") + ".json")
$PreflightTask = "WatchLog Candidate Preflight " + [guid]::NewGuid().ToString("N")
$RegistryPath = Join-Path $DataRoot "recorders.json"
$RegistryResult = Join-Path $CandidateDir ("repair-registry-" + [guid]::NewGuid().ToString("N") + ".json")
# Multi-recorder state reported by the candidate's registry checks.
$script:RecorderBaseline = $null   # what must be live again for the commit gate
$script:RecorderReport = @()       # per-recorder lines for repair-upgrade-result.ini
$script:RegistryState = ""
$script:StagedRecorderId = ""      # registry staged by THIS repair; undone on rollback

$PayloadFiles = @(
  "watchlog-agent.exe",
  "watchlog-setup-ui.exe",
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

function Clean-IniValue([string]$Value) {
  if ($null -eq $Value) { return "" }
  $clean = ([string]$Value).Replace("`r"," ").Replace("`n"," ").Trim()
  # Keep the support result single-line and INI-safe. Detailed diagnostics remain in repair-upgrade.log.
  return $clean.Replace("=","-")
}

function Write-Result([string]$Status, [int]$Code, [string]$Stage, [string]$Message, [string]$Recovery) {
  try {
    New-Item -ItemType Directory -Force -Path $DataRoot | Out-Null
    $tmp = $ResultPath + ".tmp"
    $lines = @(
      "[repair]",
      "status=$(Clean-IniValue $Status)",
      "code=$Code",
      "stage=$(Clean-IniValue $Stage)",
      "message=$(Clean-IniValue $Message)",
      "recovery=$(Clean-IniValue $Recovery)",
      "log=$(Clean-IniValue $LogPath)"
    )
    if ($script:RegistryState) { $lines += "registry=$(Clean-IniValue $script:RegistryState)" }
    # Per-recorder outcome (display name and local id only, never a recorder address).
    $report = @($script:RecorderReport | Where-Object { $null -ne $_ })
    if ($report.Count -gt 0) {
      $lines += @("", "[recorders]", "count=$($report.Count)",
                  "not_live_after=$(@($report | Where-Object { $_.after -ne 'live' }).Count)")
      $i = 0
      foreach ($r in $report) {
        $i++
        $continuity = if ($r.continuity) { "yes" } else { "no" }
        $lines += ("recorder{0}={1} | id={2} | continuity={3} | credential={4} | before={5} | after={6}" -f
                   $i, (Clean-IniValue $r.name), (Clean-IniValue $r.local_id), $continuity,
                   (Clean-IniValue $r.credential), (Clean-IniValue $r.before), (Clean-IniValue $r.after))
      }
    }
    $lines | Set-Content -LiteralPath $tmp -Encoding ASCII
    Move-Item -LiteralPath $tmp -Destination $ResultPath -Force
  } catch {}
}

function Fail([int]$Code, [string]$Message) {
  Write-Repair "FAILURE($Code) stage=$($script:CurrentStage): $Message"
  Write-Result "failed" $Code $script:CurrentStage $Message $script:RecoveryState
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
    "-TaskName",$TaskName,
    "-PayloadProfile","repair"
  ) + $Extra
  $output = & powershell.exe @args 2>&1
  $rc = $LASTEXITCODE
  foreach ($line in @($output)) {
    if ($null -ne $line -and -not [string]::IsNullOrWhiteSpace([string]$line)) {
      Write-Repair ("upgrade-helper: " + [string]$line)
    }
  }
  return $rc
}

function Invoke-CandidateSetupUi([string[]]$Arguments, [string]$Label) {
  # The candidate Setup UI runs the registry checks with the candidate's own registry,
  # credential and driver code. It is a windowed exe (& would not wait), and it is
  # bounded so an unreachable recorder cannot stall the repair.
  Remove-Item -LiteralPath $RegistryResult -Force -ErrorAction SilentlyContinue
  $argList = @($Arguments) + @("--config", ('"' + $ConfigPath + '"'), "--result-json", ('"' + $RegistryResult + '"'))
  try {
    $p = Start-Process -FilePath $CandidateSetupUi -ArgumentList $argList -PassThru -WindowStyle Hidden
    $null = $p.Handle
    if (-not $p.WaitForExit($RegistryStepTimeoutSec * 1000)) {
      try { $p.Kill() } catch {}
      Write-Repair "$Label timed out after $RegistryStepTimeoutSec s"
      return $null
    }
    $rc = $p.ExitCode
  } catch {
    Write-Repair "$Label could not start: $($_.Exception.Message)"
    return $null
  }
  if (-not (Test-Path -LiteralPath $RegistryResult)) {
    Write-Repair "$Label returned no structured result (exit=$rc)"
    return $null
  }
  try {
    $obj = Get-Content -LiteralPath $RegistryResult -Raw | ConvertFrom-Json
  } catch {
    Write-Repair "$Label result parse failed: $($_.Exception.Message)"
    return $null
  }
  Write-Repair "$Label exit=$rc ok=$([bool]$obj.ok) error=$([string]$obj.error)"
  foreach ($r in @($obj.recorders | Where-Object { $null -ne $_ })) {
    Write-Repair ("  recorder '{0}' continuity={1} credential={2} live={3} {4}" -f
                  [string]$r.display_name, [bool]$r.continuity_owner, [string]$r.credential,
                  [string]$r.live, [string]$r.detail)
  }
  return $obj
}

function Undo-RegistryStaging {
  # A registry staged by THIS repair is not part of the previous working state.
  if (-not $script:StagedRecorderId) { return }
  $undo = Invoke-CandidateSetupUi @("--registry-rollback", $script:StagedRecorderId) "registry staging rollback"
  if ($undo -and [bool]$undo.ok) {
    $script:RegistryState = "staging removed"
    Write-Repair "registry staged by this repair removed; previous recorder settings unchanged"
  } else {
    $script:RegistryState = "staging kept"
    $action = if ($undo) { [string]$undo.action } else { "no result" }
    Write-Repair "registry staged by this repair was kept ($action); the previous WatchLog does not use it"
  }
  $script:StagedRecorderId = ""
}

function Restore-Previous([string]$Why) {
  $script:CurrentStage = "automatic recovery"
  Write-Repair "restoring previous WatchLog: $Why"
  $rc = Invoke-UpgradeHelper "rollback"
  Undo-RegistryStaging
  if ($rc -ne 0) {
    $script:RecoveryState = "Automatic recovery could not be proven. Do not uninstall WatchLog; use the support log."
    Write-Repair "ROLLBACK FAILURE($rc): previous payload restore/restart could not be proven"
    return $false
  }
  $script:RecoveryState = "The previous WatchLog was restored and its Agent restart was verified."
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
  $attempts = [Math]::Max(1, $RecorderPreflightAttempts)
  $lastResult = $null

  for ($attempt = 1; $attempt -le $attempts; $attempt++) {
    Remove-Item -LiteralPath $PreflightResult -Force -ErrorAction SilentlyContinue
    Write-Repair "recorder preflight attempt $attempt/$attempts"
    $output = & $CandidateAgent --preflight-existing-site --preflight-mode recorder --config $ConfigPath --preflight-json $PreflightResult 2>&1
    $rc = $LASTEXITCODE
    foreach ($line in @($output)) {
      if ($null -ne $line -and -not [string]::IsNullOrWhiteSpace([string]$line)) {
        Write-Repair ("recorder-preflight: " + [string]$line)
      }
    }

    # The Agent deliberately exits 2 when a preflight check fails but still writes
    # a structured JSON result. Read that result even on non-zero exit so the field
    # log/UI preserves the real recorder/auth/channel failure instead of "no result".
    if (Test-Path -LiteralPath $PreflightResult) {
      try {
        $obj = Get-Content -LiteralPath $PreflightResult -Raw | ConvertFrom-Json
        $lastResult = $obj
        Write-Repair "recorder preflight exit=$rc ok=$([bool]$obj.ok) error=$([string]$obj.error)"
        if ([bool]$obj.ok) { return $obj }
      } catch {
        Write-Repair "recorder preflight result parse failed: $($_.Exception.Message)"
      }
    } else {
      Write-Repair "recorder preflight returned no structured result (exit=$rc)"
    }

    # Hikvision/Dahua can keep the previous HTTP/SDK session alive briefly after
    # the old Agent is stopped. A one-shot probe creates false failures on real
    # sites, so retry the same read-only proof without weakening the safety gate.
    if ($attempt -lt $attempts) {
      Write-Repair "recorder preflight not ready; retrying in $RecorderRetryDelaySec s"
      Start-Sleep -Seconds ([Math]::Max(1, $RecorderRetryDelaySec))
    }
  }

  return $lastResult
}

function Run-RegistryRecorderCandidate {
  # Same transient session-handoff tolerance as the Agent's recorder preflight.
  $attempts = [Math]::Max(1, $RecorderPreflightAttempts)
  $last = $null
  for ($attempt = 1; $attempt -le $attempts; $attempt++) {
    $last = Invoke-CandidateSetupUi @("--registry-selftest","--existing-site","--preflight-mode","recorder") "registry recorder probe $attempt/$attempts"
    if ($last -and [bool]$last.ok) { return $last }
    if ($attempt -lt $attempts) { Start-Sleep -Seconds ([Math]::Max(1, $RecorderRetryDelaySec)) }
  }
  return $last
}

function Set-RecorderReport($Registry) {
  if ($null -eq $Registry) { return }
  $script:RecorderReport = @(foreach ($r in @($Registry.recorders | Where-Object { $null -ne $_ })) {
    $before = if ($null -eq $r.live) { "not probed" } elseif ([bool]$r.live) { "live" } else { "offline ($([string]$r.detail))" }
    [pscustomobject]@{
      name = [string]$r.display_name
      local_id = [string]$r.local_id
      continuity = [bool]$r.continuity_owner
      credential = [string]$r.credential
      live_marker = [string]$r.live_marker
      before = $before
      after = "not checked"
    }
  })
}

function Set-RecorderBaseline($Registry) {
  # The commit gate compares the new runtime with this: the continuity recorder plus
  # every recorder that answered the candidate's probe before anything was replaced.
  $script:RecorderBaseline = $null
  $rows = @($Registry.recorders | Where-Object { $null -ne $_ })
  if ($rows.Count -le 1) { return }
  if (-not [bool]$Registry.live_markers) {
    Write-Repair "per-recorder live proof unavailable (recovery disabled); the commit gate requires every recorder"
    return
  }
  $script:RecorderBaseline = @(foreach ($r in $rows) {
    [pscustomobject]@{
      local_id = [string]$r.local_id
      required = ([bool]$r.continuity_owner -or [bool]$r.live)
      live_marker = [string]$r.live_marker
    }
  })
  $required = @($script:RecorderBaseline | Where-Object { $_.required }).Count
  Write-Repair "recorder baseline: $required of $($rows.Count) recorder(s) must be live again after the update"
}

function Read-LiveMarker([string]$Path) {
  # The runtime's per-recorder last-live marker (written at each heartbeat while that
  # recorder's own transport is live).
  try {
    if (-not $Path -or -not (Test-Path -LiteralPath $Path)) { return $null }
    $m = Get-Content -LiteralPath $Path -Raw | ConvertFrom-Json
    if (-not $m.last_live) { return $null }
    return [DateTimeOffset]::Parse([string]$m.last_live).UtcDateTime
  } catch { return $null }
}

function Test-RecorderProof($h, [datetime]$StartedAtUtc) {
  # Every configured recorder live: the single-recorder rule, and the strongest proof.
  $recorder = if ($h.recorder_seen_at) { [DateTimeOffset]::Parse([string]$h.recorder_seen_at).UtcDateTime } else { $null }
  if ($recorder -and $recorder -ge $StartedAtUtc) { return $true }
  # Multi-recorder: the continuity recorder plus every recorder that answered before the
  # upgrade must be live again. A recorder already offline before is reported, never a
  # reason for a site-wide rollback. The protected live count bounds the markers.
  if ($null -eq $script:RecorderBaseline -or -not [bool]$h.multi_recorder) { return $false }
  $required = @($script:RecorderBaseline | Where-Object { $_.required })
  if ($required.Count -eq 0 -or [int]$h.recorders_live -lt $required.Count) { return $false }
  foreach ($r in $required) {
    $seen = Read-LiveMarker $r.live_marker
    if (-not $seen -or $seen -lt $StartedAtUtc) { return $false }
  }
  return $true
}

function Update-RecorderReportAfter([datetime]$StartedAtUtc) {
  $allLive = $false
  try {
    $h = Get-Content -LiteralPath $HealthPath -Raw | ConvertFrom-Json
    $seen = if ($h.recorder_seen_at) { [DateTimeOffset]::Parse([string]$h.recorder_seen_at).UtcDateTime } else { $null }
    $allLive = [bool]([string]$h.agent_version -eq $ExpectedVersion -and $seen -and $seen -ge $StartedAtUtc)
  } catch {}
  foreach ($r in @($script:RecorderReport | Where-Object { $null -ne $_ })) {
    $marker = Read-LiveMarker $r.live_marker
    $r.after = if ($allLive -or ($marker -and $marker -ge $StartedAtUtc)) { "live" } else { "not seen since the update" }
    Write-Repair "  recorder '$($r.name)' before=$($r.before) after=$($r.after)"
  }
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
        $updater = if ($h.remote_update_poll_at) { [DateTimeOffset]::Parse([string]$h.remote_update_poll_at).UtcDateTime } else { $null }
        if ([string]$h.agent_version -eq $ExpectedVersion -and
            $heartbeat -and $heartbeat -ge $StartedAtUtc -and
            $updater -and $updater -ge $StartedAtUtc -and
            (Test-RecorderProof $h $StartedAtUtc)) {
          return $h
        }
      }
    } catch {}
    Start-Sleep -Seconds 1
  }
  return $null
}

try {
  Remove-Item -LiteralPath $ResultPath -Force -ErrorAction SilentlyContinue
  Write-Repair "WatchLog Repair/Upgrade target=$ExpectedVersion candidate=$CandidateDir install=$InstallDir"

  $script:CurrentStage = "existing-site readiness checks"

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

  $script:CurrentStage = "candidate integrity and version checks"
  Protect-CandidateDirectory

  $fileVer = File-Version $CandidateAgent
  $runVer = Runtime-Version $CandidateAgent
  Write-Repair "candidate version file=$fileVer runtime=$runVer expected=$ExpectedVersion"
  if ($fileVer -ne $ExpectedVersion -or $runVer -ne $ExpectedVersion) {
    Fail 22 "candidate executable version does not match this Repair/Upgrade release"
  }
  # The Setup UI (Manage Recorders, Site Status) is replaced together with the Agent. It is
  # a windowed exe whose --version output cannot be captured, so prove its file version.
  $uiVer = File-Version $CandidateSetupUi
  Write-Repair "candidate setup UI version file=$uiVer expected=$ExpectedVersion"
  if ($uiVer -ne $ExpectedVersion) {
    Fail 22 "candidate Setup UI version does not match this Repair/Upgrade release"
  }

  $script:CurrentStage = "passive compatibility validation"
  Write-Repair "phase 1/2: passive candidate validation while current WatchLog remains untouched"
  $result = Run-Candidate-AsSystem
  if (-not $result -or -not [bool]$result.ok) {
    $detail = if ($result -and $result.error) { [string]$result.error } else { "passive candidate preflight timed out or returned no valid result" }
    Fail 30 ("candidate is not compatible with this site; installed WatchLog was NOT changed. " + $detail)
  }

  Write-Repair "passive preflight PASSED: identity + DPAPI + cloud + decoder + signed updater; current WatchLog still running"

  # The Agent preflight proves the legacy singleton recorder only. With a recorder
  # registry the candidate runtime uses EVERY configured recorder, so prove that too
  # before the live site is paused.
  if (Test-Path -LiteralPath $RegistryPath) {
    $script:CurrentStage = "recorder registry validation"
    $reg = Invoke-CandidateSetupUi @("--registry-selftest","--existing-site","--preflight-mode","passive") "registry preflight (passive)"
    Set-RecorderReport $reg
    if (-not $reg -or -not [bool]$reg.ok) {
      $detail = if ($reg -and $reg.error) { [string]$reg.error } else { "registry preflight timed out or returned no valid result" }
      Fail 30 ("candidate cannot run this site's recorders; installed WatchLog was NOT changed. " + $detail)
    }
    $script:RegistryState = "present ($(@($reg.recorders | Where-Object { $null -ne $_ }).Count) configured recorder(s))"
    Write-Repair "registry preflight PASSED: every configured recorder credential decrypts and loads in the candidate"
  }

  $script:CurrentStage = "pause and unlock current WatchLog"
  $rc = Invoke-UpgradeHelper "preflight"
  if ($rc -ne 0) {
    Fail 23 "candidate passed passive checks, but current WatchLog could not be safely paused/unlocked; no payload files were replaced"
  }

  $script:RecoveryState = "The previous WatchLog payload is backed up and can be restored automatically."
  $script:CurrentStage = "recorder and channel validation"
  Write-Repair "phase 2/2: current WatchLog paused/backed up; validating recorder/channels before replacing files"
  $recorderResult = Run-RecorderCandidate
  if (-not $recorderResult -or -not [bool]$recorderResult.ok) {
    $detail = if ($recorderResult -and $recorderResult.error) { [string]$recorderResult.error } else { "recorder candidate preflight failed or returned no valid result" }
    $restored = Restore-Previous $detail
    if (-not $restored) { Fail 31 "recorder preflight failed AND previous WatchLog could not be proven running" }
    Fail 30 "candidate recorder/channel validation failed; previous WatchLog was restored and kept"
  }

  Write-Repair "recorder preflight PASSED"

  # Re-read: Manage Recorders may have created the registry before the pause closed it.
  if (Test-Path -LiteralPath $RegistryPath) {
    $script:CurrentStage = "recorder registry probe"
    Write-Repair "probing every configured recorder with the candidate before replacing files"
    $reg = Run-RegistryRecorderCandidate
    Set-RecorderReport $reg
    if (-not $reg -or -not [bool]$reg.ok) {
      $detail = if ($reg -and $reg.error) { [string]$reg.error } else { "registry recorder probe failed or returned no valid result" }
      $restored = Restore-Previous $detail
      if (-not $restored) { Fail 31 "registry recorder probe failed AND previous WatchLog could not be proven running" }
      Fail 30 "candidate could not reach the original WatchLog recorder through the recorder registry; previous WatchLog was restored and kept"
    }
    Set-RecorderBaseline $reg
  }

  # Transactional staging of the legacy singleton into the recorder registry. The candidate
  # reads and verifies the legacy credential, creates the stable local UUID, writes and
  # reads back the per-recorder credential, then publishes and re-reads the registry; any
  # failure there removes what it created. The new Agent must then boot and pass the
  # health gate with it, otherwise Restore-Previous removes it again. The legacy settings
  # stay: retirement waits for a runtime that boots one-recorder sites from the registry.
  if (-not (Test-Path -LiteralPath $RegistryPath)) {
    $script:CurrentStage = "recorder registry staging"
    Write-Repair "staging the existing recorder into the recorder registry; legacy recorder settings are kept"
    $staged = Invoke-CandidateSetupUi @("--registry-migrate") "registry staging"
    if (-not $staged -or -not [bool]$staged.ok) {
      $detail = if ($staged -and $staged.error) { [string]$staged.error } else { "registry staging failed or returned no valid result" }
      $restored = Restore-Previous ("registry staging failed: " + $detail)
      if (-not $restored) { Fail 43 "registry staging failed AND previous WatchLog could not be proven running" }
      Fail 42 "the existing recorder could not be prepared for this release; previous WatchLog restored"
    }
    if ([bool]$staged.migrated) {
      $script:StagedRecorderId = [string]$staged.local_id
      $script:RegistryState = "staged"
      Write-Repair "existing recorder staged into the recorder registry; it is removed again if this update rolls back"
    }
  }

  Write-Repair "beginning atomic payload replacement"
  $script:CurrentStage = "install candidate files"
  try {
    Install-CandidatePayload
  } catch {
    $restored = Restore-Previous ("payload copy failed: " + $_.Exception.Message)
    if (-not $restored) { Fail 33 "payload copy failed AND rollback could not be proven" }
    Fail 32 "could not install candidate payload; previous WatchLog restored"
  }

  $script:CurrentStage = "verify installed version"
  $rc = Invoke-UpgradeHelper "verify-version" @("-ExpectedVersion",$ExpectedVersion)
  if ($rc -ne 0) {
    $restored = Restore-Previous "installed version verification failed"
    if (-not $restored) { Fail 35 "version verification failed AND rollback could not be proven" }
    Fail 34 "installed candidate version could not be verified; previous WatchLog restored"
  }

  $script:CurrentStage = "start updated WatchLog"
  $started = [DateTime]::UtcNow
  try {
    & powershell.exe -NoProfile -ExecutionPolicy Bypass -File $RegisterService -InstallDir $InstallDir
    if ($LASTEXITCODE -ne 0) { throw "register-service returned $LASTEXITCODE" }
  } catch {
    $restored = Restore-Previous ("new background task failed: " + $_.Exception.Message)
    if (-not $restored) { Fail 37 "new task failed AND rollback could not be proven" }
    Fail 36 "new WatchLog could not start; previous WatchLog restored"
  }

  $script:CurrentStage = "prove updated WatchLog health"
  Write-Repair "new WatchLog started; waiting for cloud + recorder + online-update health proof"
  $health = Wait-NewRuntimeHealth $started
  Update-RecorderReportAfter $started
  if (-not $health) {
    $restored = Restore-Previous "new runtime did not prove heartbeat + recorder + remote-update polling"
    if (-not $restored) { Fail 39 "new runtime unhealthy AND rollback could not be proven" }
    Fail 38 "new WatchLog did not become fully healthy; previous WatchLog restored"
  }

  $script:CurrentStage = "final commit verification"
  $rc = Invoke-UpgradeHelper "commit" @("-ExpectedVersion",$ExpectedVersion)
  if ($rc -ne 0) {
    $restored = Restore-Previous "final commit verification failed"
    if (-not $restored) { Fail 41 "commit failed AND rollback could not be proven" }
    Fail 40 "final verification failed; previous WatchLog restored"
  }

  $script:CurrentStage = "complete"
  $script:RecoveryState = "WatchLog $ExpectedVersion is installed and healthy."
  $message = "WatchLog $ExpectedVersion updated successfully."
  $notLive = @($script:RecorderReport | Where-Object { $null -ne $_ -and $_.after -ne "live" }).Count
  if ($notLive -gt 0) {
    # Degraded, not regressed: only recorders that were already unreachable before.
    $message += " $notLive recorder(s) that could not be reached before the update are still not reachable."
  }
  Write-Repair "SUCCESS: WatchLog $ExpectedVersion healthy; cloud heartbeat + recorder + remote-update polling proven (recorders still not reachable: $notLive)"
  Write-Result "success" 0 $script:CurrentStage $message $script:RecoveryState
  exit 0
}
catch {
  $msg = "unexpected Repair/Upgrade error: " + $_.Exception.Message
  Fail 49 $msg
}
finally {
  try { Remove-Item -LiteralPath $PreflightResult -Force -ErrorAction SilentlyContinue } catch {}
  try { Remove-Item -LiteralPath $RegistryResult -Force -ErrorAction SilentlyContinue } catch {}
}
