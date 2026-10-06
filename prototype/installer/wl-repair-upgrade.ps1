<#
  WatchLog Existing-Site Repair/Upgrade orchestrator.

  Existing-site only. No discovery, no re-enrollment, no recorder credential entry.

  Safety order:
    0. Downgrade guard, ONLY for a candidate older than 5.1.0 (a 5.0.x Repair package):
       refuse a multi-recorder site, i.e. recorders.json (written by 5.1.0+) lists more
       than one configured recorder, or exists but cannot be read (Read-RecorderRegistry).
       A 5.1.x candidate manages multi-recorder sites, so the guard does not apply to it.
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
    6. Require fresh cloud heartbeat + remote-update poll proof, and recorder proof judged
       per recorder: every recorder that was live before the update (old Agent's protected
       runtime-health just before the pause, or the candidate's probe after it) must be
       live again; a recorder that was already offline before may stay offline and is
       reported as such, never counted as healthy. Markers older than the phase start or
       more than FutureSkewSec in the future are not proof.
    7. Commit only after health proof; otherwise rollback: restore the previous payload,
       remove a registry staged in step 4 BEFORE the old Agent starts, then restart it and
       require a fresh heartbeat from the restored version. Rollback success is reported
       only with that proof. The legacy recorder settings are never retired here: the 5.1
       runtime still runs a one-recorder site from them. An unexpected error after step 4
       paused WatchLog also restores the previous WatchLog, and a registry staging step
       that timed out is rolled back too. While paused, an armed recovery task restores the
       previous WatchLog if this orchestrator is killed or the PC loses power.
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
  [int]$RegistryStepTimeoutSec = 180,
  # A health marker this far in the future is not proof of anything after the start.
  [int]$FutureSkewSec = 120,
  # How long the restored previous Agent has to send a fresh heartbeat after a rollback.
  [int]$RollbackProofTimeoutSec = 240
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
$RecorderRegistryPath = Join-Path $DataRoot "recorders.json"
$CandidateAgent = Join-Path $CandidateDir "watchlog-agent.exe"
$CandidateSetupUi = Join-Path $CandidateDir "watchlog-setup-ui.exe"
$UpgradeHelper = Join-Path $CandidateDir "wl-upgrade.ps1"
$RegisterService = Join-Path $CandidateDir "register-service.ps1"
$PreflightResult = Join-Path $CandidateDir ("repair-preflight-" + [guid]::NewGuid().ToString("N") + ".json")
$PreflightTask = "WatchLog Candidate Preflight " + [guid]::NewGuid().ToString("N")
$RegistryPath = Join-Path $DataRoot "recorders.json"
# Multi-recorder state reported by the candidate's registry checks.
$script:RecorderBaseline = $null   # what must be live again for the commit gate
$script:RecorderReport = @()       # per-recorder lines for repair-upgrade-result.ini
$script:RegistryState = ""
$script:StagedRecorderId = ""      # registry staged by THIS repair; undone on rollback
$script:Paused = $false            # current WatchLog paused/backed up; restore it on any failure
$script:PrePause = $null           # old Agent's recorder state just before the pause (Get-PrePauseState)
$script:SingleRecorderRequired = $true  # one-recorder rule: must the recorder be live after?
$RemoteRoot = Join-Path $DataRoot "remote-update"
$MarkerPath = Join-Path $DataRoot "upgrade-in-progress.json"

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

function Get-VersionTuple([string]$Version) {
  # Lenient x.y.z: '5.1.0', 'v5.0.28', '5.1.1+abc' -> @(5,1,1). Non-numeric parts count as 0.
  $text = ([string]$Version).Trim().TrimStart('v','V').Split('+')[0].Split('-')[0]
  $parts = @($text.Split('.') | ForEach-Object { $n = 0; [void][int]::TryParse($_, [ref]$n); $n })
  while ($parts.Count -lt 3) { $parts += 0 }
  return @($parts[0], $parts[1], $parts[2])
}

function Test-VersionBelow([string]$Version, [string]$Floor) {
  $a = Get-VersionTuple $Version
  $b = Get-VersionTuple $Floor
  for ($i = 0; $i -lt 3; $i++) {
    if ($a[$i] -lt $b[$i]) { return $true }
    if ($a[$i] -gt $b[$i]) { return $false }
  }
  return $false
}

function ConvertTo-UtcOrNull($Value) {
  if (-not $Value) { return $null }
  try { return [DateTimeOffset]::Parse([string]$Value).UtcDateTime } catch { return $null }
}

function Test-FreshMarker($Value, [datetime]$NotBeforeUtc) {
  # A marker proves something happened after NotBefore only if it is not older than that and
  # not further in the future than the allowed clock skew (a future stamp proves nothing).
  $t = ConvertTo-UtcOrNull $Value
  if ($null -eq $t) { return $false }
  $skew = if ($FutureSkewSec -gt 0) { $FutureSkewSec } else { 120 }
  return ($t -ge $NotBeforeUtc -and $t -le [DateTime]::UtcNow.AddSeconds($skew))
}

function Get-PrePauseState {
  # What the OLD Agent itself last proved about its recorders, read from its protected
  # runtime-health file just before the pause. Judged only while that Agent was demonstrably
  # running (a heartbeat in the last 15 minutes, not future-dated). A recorder counts as live
  # before the update when its proof advanced at (or just before) the latest heartbeat.
  $state = @{ judged = $false; single_live = $true; rows = @{}; detail = "no runtime health" }
  try {
    if (-not (Test-Path -LiteralPath $HealthPath)) { return $state }
    $h = Get-Content -LiteralPath $HealthPath -Raw | ConvertFrom-Json
  } catch {
    $state.detail = "runtime health unreadable"
    return $state
  }
  $now = [DateTime]::UtcNow
  $beat = ConvertTo-UtcOrNull $h.heartbeat_at
  if ($null -eq $beat -or $beat -lt $now.AddMinutes(-15) -or $beat -gt $now.AddSeconds($FutureSkewSec)) {
    $state.detail = "the running Agent has no recent heartbeat; recorder state before the update cannot be judged"
    return $state
  }
  $since = $beat.AddSeconds(-150)
  $seen = ConvertTo-UtcOrNull $h.recorder_seen_at
  $state.judged = $true
  $state.single_live = [bool]($seen -and $seen -ge $since -and $seen -le $now.AddSeconds($FutureSkewSec))
  foreach ($row in @($h.recorders | Where-Object { $null -ne $_ })) {
    $id = [string]$row.local_id
    if (-not $id) { continue }
    $t = ConvertTo-UtcOrNull $row.last_live_at
    $state.rows[$id] = @{
      live = [bool]([bool]$row.live -and $t -and $t -ge $since -and $t -le $now.AddSeconds($FutureSkewSec))
      continuity = [bool]$row.continuity_owner
    }
  }
  $state.detail = "agent $([string]$h.agent_version), heartbeat $([string]$h.heartbeat_at), recorder live=$($state.single_live), $($state.rows.Count) recorder row(s)"
  return $state
}

function Test-LegacyRecorderOfflineBefore {
  # The legacy (continuity) recorder was demonstrably not live before the pause.
  $p = $script:PrePause
  if ($null -eq $p -or -not $p.judged) { return $false }
  $continuity = @($p.rows.Values | Where-Object { $_.continuity })
  if ($continuity.Count -gt 0) { return (-not $continuity[0].live) }
  return (-not $p.single_live)
}

function Read-RecorderRegistry([string]$Path) {
  # WatchLog 5.1.0+ keeps the site's recorders in recorders.json; 5.0.x never writes it.
  # This runtime ignores that file and runs only the legacy single recorder, and a site
  # with several configured recorders refuses its legacy cloud calls while the heartbeat
  # still looks online. So count the configured recorders the way 5.1 does (a row without
  # is_configured counts as configured). A missing file is a 5.0.x site. A file that
  # exists but cannot be read or understood is "invalid": the count cannot be proven, so
  # the caller refuses rather than guess.
  if (-not (Test-Path -LiteralPath $Path)) {
    return @{ state = "absent"; configured = 0; detail = "no recorders.json (5.0.x site)" }
  }
  try {
    $doc = [IO.File]::ReadAllText($Path) | ConvertFrom-Json
  } catch {
    return @{ state = "invalid"; configured = 0; detail = "recorders.json is not readable JSON: $($_.Exception.Message)" }
  }
  if ($null -eq $doc -or [string]$doc.schema -ne "watchlog.recorders.v1") {
    return @{ state = "invalid"; configured = 0; detail = "recorders.json has an unsupported schema" }
  }
  if (-not ($doc.recorders -is [System.Array])) {
    return @{ state = "invalid"; configured = 0; detail = "recorders.json has no recorder list" }
  }
  $configured = 0
  foreach ($row in $doc.recorders) {
    if (-not ($row -is [System.Management.Automation.PSCustomObject])) {
      return @{ state = "invalid"; configured = 0; detail = "recorders.json has a malformed recorder entry" }
    }
    $flag = $row.PSObject.Properties["is_configured"]
    if ($null -eq $flag -or [bool]$flag.Value) { $configured++ }
  }
  return @{ state = "ok"; configured = $configured; detail = "$configured configured recorder(s)" }
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

function Stop-ProcessTree($Process) {
  # The Setup UI is a one-file build: its real work runs in a child of the bootloader, so
  # stopping the bootloader alone would leave that child probing recorders.
  try {
    Start-Process -FilePath (Join-Path $env:SystemRoot "System32\taskkill.exe") -ArgumentList @("/PID", [string]$Process.Id, "/T", "/F") -WindowStyle Hidden -Wait
  } catch {}
  try { if (-not $Process.HasExited) { $Process.Kill() } } catch {}
}

function Invoke-CandidateSetupUi([string[]]$Arguments, [string]$Label) {
  # The candidate Setup UI runs the registry checks with the candidate's own registry,
  # credential and driver code. It is a windowed exe (& would not wait), and it is
  # bounded so an unreachable recorder cannot stall the repair. Every step gets its own
  # result file: a late write from an abandoned step can never answer a later one.
  $resultPath = Join-Path $CandidateDir ("repair-registry-" + [guid]::NewGuid().ToString("N") + ".json")
  $argList = @($Arguments) + @("--config", ('"' + $ConfigPath + '"'), "--result-json", ('"' + $resultPath + '"'))
  try {
    $p = Start-Process -FilePath $CandidateSetupUi -ArgumentList $argList -PassThru -WindowStyle Hidden
    $null = $p.Handle
    if (-not $p.WaitForExit($RegistryStepTimeoutSec * 1000)) {
      Stop-ProcessTree $p
      Write-Repair "$Label timed out after $RegistryStepTimeoutSec s"
      return $null
    }
    $rc = $p.ExitCode
  } catch {
    Write-Repair "$Label could not start: $($_.Exception.Message)"
    return $null
  }
  if (-not (Test-Path -LiteralPath $resultPath)) {
    Write-Repair "$Label returned no structured result (exit=$rc)"
    return $null
  }
  try {
    $obj = Get-Content -LiteralPath $resultPath -Raw | ConvertFrom-Json
  } catch {
    Write-Repair "$Label result parse failed: $($_.Exception.Message)"
    return $null
  } finally {
    Remove-Item -LiteralPath $resultPath -Force -ErrorAction SilentlyContinue
  }
  Write-Repair "$Label exit=$rc ok=$([bool]$obj.ok) error=$([string]$obj.error)"
  if ($obj.legacy_mirror) {
    # matches | differs | absent | unreadable. "differs" is reported, not fatal: Repair cannot
    # know which login is right; re-entering it in Manage Recorders rewrites both copies.
    Write-Repair "  legacy login copy: $([string]$obj.legacy_mirror)"
  }
  if ($obj.warning) { Write-Repair "  WARNING: $([string]$obj.warning)" }
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
  Set-MarkerField "registry_staged_id" ""
}

function Note-RegistryStaging($Staged) {
  # Remember a registry THIS run's --registry-migrate created, so Restore-Previous removes it.
  # The step undoes its own partial work when it fails, but not when it is stopped for running
  # too long or leaves no result. The registry did not exist before the step, so a
  # one-recorder registry present after such a step is the one it created
  # (--registry-rollback still keeps anything that is not that untouched, unbound recorder).
  if ($Staged -and [bool]$Staged.ok) {
    if ([bool]$Staged.migrated) {
      $script:StagedRecorderId = [string]$Staged.local_id
      $script:RegistryState = "staged"
      Set-MarkerField "registry_staged_id" $script:StagedRecorderId
      Write-Repair "existing recorder staged into the recorder registry; it is removed again if this update rolls back"
    }
    return
  }
  if ($Staged -and [bool]$Staged.undone) { return }
  try {
    if (-not (Test-Path -LiteralPath $RegistryPath)) { return }
    $rows = @((Get-Content -LiteralPath $RegistryPath -Raw | ConvertFrom-Json).recorders | Where-Object { $null -ne $_ })
  } catch {
    Write-Repair "registry staging left a recorder registry that could not be read: $($_.Exception.Message)"
    return
  }
  if ($rows.Count -eq 1 -and [string]$rows[0].local_id) {
    $script:StagedRecorderId = [string]$rows[0].local_id
    $script:RegistryState = "staged (unconfirmed)"
    Set-MarkerField "registry_staged_id" $script:StagedRecorderId
    Write-Repair "registry staging gave no confirmed result but left a recorder registry; it is removed with the rollback"
  }
}

function Set-MarkerField([string]$Name, $Value) {
  # The in-progress marker (written by the upgrade helper's preflight) tells the recovery
  # task what this run changed outside the payload, e.g. a staged recorder registry.
  try {
    if (-not (Test-Path -LiteralPath $MarkerPath)) { return }
    $m = Get-Content -LiteralPath $MarkerPath -Raw | ConvertFrom-Json
    $m | Add-Member -NotePropertyName $Name -NotePropertyValue $Value -Force
    $tmp = $MarkerPath + ".tmp"
    $m | ConvertTo-Json -Depth 4 | Set-Content -LiteralPath $tmp -Encoding UTF8
    Move-Item -LiteralPath $tmp -Destination $MarkerPath -Force
  } catch {
    Write-Repair "could not update the upgrade-in-progress marker: $($_.Exception.Message)"
  }
}

function Clear-StaleRemoteUpdate {
  # A remote update staged or applied before this Repair must not act on the repaired site:
  # the new launcher would apply an old staged package over the repaired Agent, or the
  # early-exit rule would put the old .remote.bak back. Runs while WatchLog is paused. A
  # request that was staged or applied but never confirmed is closed honestly as superseded.
  $pending = Join-Path $RemoteRoot "pending.json"
  $result = Join-Path $RemoteRoot "result.json"
  $requestId = ""
  try {
    if (Test-Path -LiteralPath $pending) {
      $p = Get-Content -LiteralPath $pending -Raw | ConvertFrom-Json
      if ([string]$p.request_id -match '^[0-9A-Fa-f-]{8,64}$') { $requestId = [string]$p.request_id }
    }
  } catch {}
  foreach ($name in @("pending.json","watchlog-agent.next.exe","watchlog-agent.next.exe.part","baseline.json")) {
    Remove-Item -LiteralPath (Join-Path $RemoteRoot $name) -Force -ErrorAction SilentlyContinue
  }
  foreach ($name in @("watchlog-agent.exe.remote.bak","watchlog-agent.next.verify")) {
    $path = Join-Path $InstallDir $name
    if (Test-Path -LiteralPath $path) {
      Remove-Item -LiteralPath $path -Force -ErrorAction SilentlyContinue
      Write-Repair "removed stale remote-update file $name"
    }
  }
  try {
    $r = $null
    if (Test-Path -LiteralPath $result) { $r = Get-Content -LiteralPath $result -Raw | ConvertFrom-Json }
    $open = ($r -and -not [bool]$r.committed -and -not [bool]$r.rollback_applied -and [bool]$r.ok)
    if ($open -or ((-not $r) -and $requestId)) {
      $id = if ($r) { [string]$r.request_id } else { $requestId }
      $now = [DateTimeOffset]::UtcNow.ToString("o")
      $obj = [ordered]@{
        schema = "watchlog.remote_update_result.v1"; request_id = $id; ok = $false
        detail = "superseded by Repair/Upgrade to $ExpectedVersion before this remote update was confirmed"
        applied_version = ""; completed_at = $now; health_not_before = $now; superseded = $true
      }
      New-Item -ItemType Directory -Force -Path $RemoteRoot | Out-Null
      ($obj | ConvertTo-Json -Compress) | Set-Content -LiteralPath ($result + ".tmp") -Encoding UTF8
      Move-Item -LiteralPath ($result + ".tmp") -Destination $result -Force
      Write-Repair "an unconfirmed remote update request was closed as superseded by this Repair/Upgrade"
    }
  } catch {
    Write-Repair "stale remote-update result could not be neutralised: $($_.Exception.Message)"
  }
}

function Restore-Previous([string]$Why) {
  # Order matters: (1) stop the new runtime and put the previous files back with the task
  # still suspended, (2) remove a recorder registry staged by this repair while nothing
  # runs (otherwise the restored Agent can bind to it and convert the site for good),
  # (3) only then restart the previous Agent and require a fresh heartbeat from it.
  $script:CurrentStage = "automatic recovery"
  $script:Paused = $false
  Write-Repair "restoring previous WatchLog: $Why"
  $restoreRc = Invoke-UpgradeHelper "rollback-restore"
  Undo-RegistryStaging
  $startRc = Invoke-UpgradeHelper "rollback-start" @("-ProveHealth","-ProofTimeoutSec",[string]$RollbackProofTimeoutSec)
  if ($restoreRc -eq 0 -and $startRc -eq 0) {
    $script:RecoveryState = "The previous WatchLog was restored and proven running: its Agent sent a fresh cloud heartbeat after the restore."
    Write-Repair "previous WatchLog restored; its Agent is proven running by a fresh heartbeat"
    return $true
  }
  if ($restoreRc -ne 0) {
    $script:RecoveryState = "AUTOMATIC RECOVERY FAILED: the previous WatchLog files could not all be put back (code $restoreRc). The background task was re-enabled (code $startRc). Do not uninstall WatchLog and do not run another installer. Contact WatchLog support with C:\ProgramData\WatchLog\repair-upgrade.log and upgrade.log."
  } elseif ($startRc -eq 15) {
    $script:RecoveryState = "The previous WatchLog files were restored and its background task is running, but its Agent did not send a cloud heartbeat within $RollbackProofTimeoutSec s, so recovery is NOT proven. Do not uninstall WatchLog. Check this site's heartbeat in the WatchLog portal; if it is still offline after 10 minutes, contact WatchLog support with C:\ProgramData\WatchLog\repair-upgrade.log and upgrade.log."
  } else {
    $script:RecoveryState = "AUTOMATIC RECOVERY FAILED: the previous WatchLog files were restored but its background task could not be started (code $startRc). Do not uninstall WatchLog. A recovery task retries every 5 minutes; contact WatchLog support with C:\ProgramData\WatchLog\repair-upgrade.log and upgrade.log."
  }
  Write-Repair "ROLLBACK FAILURE(restore=$restoreRc start=$startRc): previous WatchLog is not proven running"
  return $false
}

function Stop-Unexpected([string]$msg) {
  # An error none of the steps above expected. Once the current WatchLog is paused its task is
  # disabled and its payload backed up: restore that working state, never leave it switched off.
  if ($script:Paused) {
    $restored = Restore-Previous $msg
    if (-not $restored) { Fail 48 ($msg + "; previous WatchLog could not be proven running") }
    Fail 49 ($msg + "; previous WatchLog restored")
  }
  Fail 49 $msg
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
      before = $before
      after = "not checked"
      required = $true
    }
  })
}

function Set-LegacyRecorderReport([string]$Before) {
  # A one-recorder site without a registry: one report row for the legacy recorder, so the
  # result file and the installer say plainly what was and was not proven about it.
  $script:RecorderReport = @([pscustomobject]@{
    name = "Recorder"; local_id = ""; continuity = $true; credential = "ok"
    before = $Before; after = "not checked"; required = $script:SingleRecorderRequired
  })
}

function Set-RecorderBaseline($Registry) {
  # The commit gate compares the new runtime with this, recorder by recorder: a recorder is
  # required to be live again when it was live before the update, by the candidate's probe
  # after the pause OR by the old Agent's own protected proof just before it. A recorder that
  # was offline by both is not required, and is reported as still offline, never as healthy.
  # Without recent proof from the old Agent the continuity recorder stays required.
  # $script:RecorderRegressions: recorders required before that the candidate cannot reach.
  $rows = @($Registry.recorders | Where-Object { $null -ne $_ })
  $p = $script:PrePause
  $script:RecorderRegressions = @()
  $script:RecorderBaseline = @(foreach ($r in $rows) {
    $id = [string]$r.local_id
    $probeLive = [bool]$r.live
    $before = $null
    if ($p -and $p.judged) {
      if ($p.rows.ContainsKey($id)) { $before = [bool]$p.rows[$id].live }
      elseif ([bool]$r.continuity_owner -and $p.rows.Count -eq 0) { $before = [bool]$p.single_live }
    }
    $unjudged = ($null -eq $before -and [bool]$r.continuity_owner)
    $required = ($probeLive -or $before -eq $true -or $unjudged)
    if ($required -and -not $probeLive) { $script:RecorderRegressions += [string]$r.display_name }
    [pscustomobject]@{ local_id = $id; required = $required }
  })
  foreach ($r in @($script:RecorderReport | Where-Object { $null -ne $_ })) {
    $b = @($script:RecorderBaseline | Where-Object { $_.local_id -eq $r.local_id })
    if ($b.Count -gt 0) { $r.required = [bool]$b[0].required }
    if (-not $r.required) { $r.before = "offline before the update ($($r.before -replace '^offline \((.*)\)$','$1'))" }
  }
  $required = @($script:RecorderBaseline | Where-Object { $_.required }).Count
  Write-Repair "recorder baseline: $required of $($rows.Count) recorder(s) must be live again after the update"
}

function Get-RecorderRegressions($Registry) {
  Set-RecorderBaseline $Registry
  return @($script:RecorderRegressions)
}

function Get-RuntimeRecorderRow($h, [string]$LocalId) {
  # This recorder's row in the protected runtime-health file (Secrets ACL), matched by
  # local id. The last_live.json markers under ProgramData are not used: a standard user
  # can create files there, so they are no proof.
  foreach ($row in @($h.recorders | Where-Object { $null -ne $_ })) {
    if ([string]$row.local_id -eq $LocalId) { return $row }
  }
  return $null
}

function Get-RuntimeRecorderSeen($h, [string]$LocalId) {
  # When the new runtime last saw this recorder's own event stream live, or $null.
  $row = Get-RuntimeRecorderRow $h $LocalId
  if ($null -eq $row -or -not $row.last_live_at) { return $null }
  return (ConvertTo-UtcOrNull $row.last_live_at)
}

function Test-RecorderLiveAfter($h, [string]$LocalId, [datetime]$StartedAtUtc) {
  $row = Get-RuntimeRecorderRow $h $LocalId
  if ($null -eq $row -or -not [bool]$row.live) { return $false }
  return (Test-FreshMarker $row.last_live_at $StartedAtUtc)
}

function Test-RecorderProof($h, [datetime]$StartedAtUtc) {
  # Every configured recorder live (recorder_seen_at advances only then): the strongest proof.
  if (Test-FreshMarker $h.recorder_seen_at $StartedAtUtc) { return $true }
  if (-not [bool]$h.multi_recorder) {
    # One recorder: required only if it was live before the update.
    return ($script:SingleRecorderRequired -eq $false)
  }
  # Multi-recorder: each recorder judged independently against the pre-update baseline, by
  # its own protected runtime-health row. Offline before = may stay offline.
  if ($null -eq $script:RecorderBaseline) { return $false }
  foreach ($r in @($script:RecorderBaseline | Where-Object { $_.required })) {
    if (-not (Test-RecorderLiveAfter $h $r.local_id $StartedAtUtc)) { return $false }
  }
  return $true
}

function Update-RecorderReportAfter([datetime]$StartedAtUtc) {
  $allLive = $false
  $h = $null
  try {
    $read = Get-Content -LiteralPath $HealthPath -Raw | ConvertFrom-Json
    if ([string]$read.agent_version -eq $ExpectedVersion) { $h = $read }
    $allLive = [bool]($h -and (Test-FreshMarker $h.recorder_seen_at $StartedAtUtc))
  } catch {}
  foreach ($r in @($script:RecorderReport | Where-Object { $null -ne $_ })) {
    $own = if ($h -and $r.local_id) { Test-RecorderLiveAfter $h $r.local_id $StartedAtUtc } else { $false }
    if ($allLive -or $own) { $r.after = "live" }
    elseif ($r.required) { $r.after = "not seen since the update" }
    else { $r.after = "still offline (it was offline before the update; not verified by this update)" }
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
  # Exact version, a cloud heartbeat and a remote-update poll that both happened after the
  # start (not older, not future-dated), and the per-recorder proof.
  $deadline = (Get-Date).AddSeconds($HealthTimeoutSec)
  while ((Get-Date) -lt $deadline) {
    try {
      if (Test-Path -LiteralPath $HealthPath) {
        $h = Get-Content -LiteralPath $HealthPath -Raw | ConvertFrom-Json
        if ([string]$h.agent_version -eq $ExpectedVersion -and
            (Test-FreshMarker $h.heartbeat_at $StartedAtUtc) -and
            (Test-FreshMarker $h.remote_update_poll_at $StartedAtUtc) -and
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

  # Downgrade guard, for a candidate OLDER than 5.1.0 only: first, before the candidate runs
  # or anything is paused, refuse a site that a 5.1.0+ Agent has already set up with more than
  # one recorder (5.0.x runs only the legacy singleton). A 5.1.x candidate manages every
  # configured recorder; its registry is validated by the candidate itself below.
  $script:CurrentStage = "site recorder check"
  $registry = Read-RecorderRegistry $RecorderRegistryPath
  Write-Repair "recorder registry: state=$($registry.state) $($registry.detail)"
  if (Test-VersionBelow $ExpectedVersion "5.1.0") {
    if ($registry.state -eq "invalid") {
      Fail 25 "WatchLog could not read this site's recorder list, so it cannot confirm that the site uses only one recorder. WatchLog $ExpectedVersion was not installed. Install 5.1.0 or later, or ask support to check C:\ProgramData\WatchLog\recorders.json."
    }
    if ($registry.configured -gt 1) {
      Fail 24 "This site uses more than one recorder; WatchLog $ExpectedVersion cannot manage it. Disable the extra recorders in Manage Recorders first, or install 5.1.0 or later."
    }
  } else {
    Write-Repair "WatchLog $ExpectedVersion manages multi-recorder sites: the 5.0.x downgrade guard does not apply"
  }

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

  # What the running Agent last proved about its recorders, read before it is paused: the
  # "live before" half of the per-recorder baseline.
  $script:PrePause = Get-PrePauseState
  Write-Repair "recorder state before the update: judged=$($script:PrePause.judged) $($script:PrePause.detail)"

  $script:CurrentStage = "pause and unlock current WatchLog"
  # -ArmRecovery: while WatchLog is paused a recovery task restores it if this process is
  # killed or the PC loses power, so the site is never left with a disabled task.
  $rc = Invoke-UpgradeHelper "preflight" @("-ArmRecovery","-ProveHealth","-OwnerPid",[string]$PID)
  if ($rc -ne 0) {
    Fail 23 "candidate passed passive checks, but current WatchLog could not be safely paused/unlocked; no payload files were replaced"
  }
  $script:Paused = $true
  Clear-StaleRemoteUpdate

  $script:RecoveryState = "The previous WatchLog payload is backed up and can be restored automatically."
  $script:CurrentStage = "recorder and channel validation"
  Write-Repair "phase 2/2: current WatchLog paused/backed up; validating recorder/channels before replacing files"
  $recorderResult = Run-RecorderCandidate
  if (-not $recorderResult -or -not [bool]$recorderResult.ok) {
    $detail = if ($recorderResult -and $recorderResult.error) { [string]$recorderResult.error } else { "recorder candidate preflight failed or returned no valid result" }
    if (Test-LegacyRecorderOfflineBefore) {
      # Not a regression: the running Agent itself could not see this recorder before the
      # pause. It may stay offline; it is not validated and is never reported as healthy.
      $script:SingleRecorderRequired = $false
      Write-Repair "recorder preflight failed ($detail), but the recorder was already offline before the update; continuing without validating it"
    } else {
      $restored = Restore-Previous $detail
      if (-not $restored) { Fail 31 "recorder preflight failed AND previous WatchLog could not be proven running" }
      Fail 30 "candidate recorder/channel validation failed; previous WatchLog was restored and kept"
    }
  } else {
    Write-Repair "recorder preflight PASSED"
  }
  if (-not (Test-Path -LiteralPath $RegistryPath)) {
    $legacyBefore = if ($script:SingleRecorderRequired) { "live" } else { "offline before the update" }
    Set-LegacyRecorderReport $legacyBefore
  }

  # Re-read: Manage Recorders may have created the registry before the pause closed it.
  if (Test-Path -LiteralPath $RegistryPath) {
    $script:CurrentStage = "recorder registry probe"
    Write-Repair "probing every configured recorder with the candidate before replacing files"
    $reg = Run-RegistryRecorderCandidate
    Set-RecorderReport $reg
    $rows = @(if ($reg) { $reg.recorders | Where-Object { $null -ne $_ } })
    $credentialsOk = ($rows.Count -gt 0 -and @($rows | Where-Object { [string]$_.credential -ne "ok" }).Count -eq 0)
    if (-not $reg -or (-not [bool]$reg.ok -and -not $credentialsOk)) {
      $detail = if ($reg -and $reg.error) { [string]$reg.error } else { "registry recorder probe failed or returned no valid result" }
      $restored = Restore-Previous $detail
      if (-not $restored) { Fail 31 "registry recorder probe failed AND previous WatchLog could not be proven running" }
      Fail 30 "candidate could not reach the original WatchLog recorder through the recorder registry; previous WatchLog was restored and kept"
    }
    $regressed = @(Get-RecorderRegressions $reg)
    if ($regressed.Count -gt 0) {
      $who = $regressed -join ", "
      $restored = Restore-Previous "recorder(s) that were live before the update are not reachable by the new version: $who"
      if (-not $restored) { Fail 31 "a recorder that was live before the update is unreachable AND previous WatchLog could not be proven running" }
      Fail 30 "the new version cannot reach recorder(s) that were live before the update ($who); previous WatchLog was restored and kept"
    }
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
    Note-RegistryStaging $staged
    if (-not $staged -or -not [bool]$staged.ok) {
      $detail = if ($staged -and $staged.error) { [string]$staged.error } else { "registry staging failed or returned no valid result" }
      $restored = Restore-Previous ("registry staging failed: " + $detail)
      if (-not $restored) { Fail 43 "registry staging failed AND previous WatchLog could not be proven running" }
      Fail 42 "the existing recorder could not be prepared for this release; previous WatchLog restored"
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
  $script:Paused = $false            # committed: the new WatchLog is the working state now

  $script:CurrentStage = "complete"
  $script:RecoveryState = "WatchLog $ExpectedVersion is installed and healthy."
  $message = "WatchLog $ExpectedVersion updated successfully."
  $notLive = @($script:RecorderReport | Where-Object { $null -ne $_ -and $_.after -ne "live" }).Count
  if ($notLive -gt 0) {
    # Degraded, not regressed: only recorders that were already unreachable before. They are
    # not validated by this update and are never reported as healthy.
    $message += " $notLive recorder(s) that could not be reached before the update are still not reachable and were not checked by this update."
  }
  Write-Repair "SUCCESS: WatchLog $ExpectedVersion healthy; cloud heartbeat + recorder + remote-update polling proven (recorders still not reachable: $notLive)"
  Write-Result "success" 0 $script:CurrentStage $message $script:RecoveryState
  exit 0
}
catch {
  $msg = "unexpected Repair/Upgrade error: " + $_.Exception.Message
  Stop-Unexpected $msg
}
finally {
  try { Remove-Item -LiteralPath $PreflightResult -Force -ErrorAction SilentlyContinue } catch {}
  try { Remove-Item -Path (Join-Path $CandidateDir "repair-registry-*.json") -Force -ErrorAction SilentlyContinue } catch {}
}
