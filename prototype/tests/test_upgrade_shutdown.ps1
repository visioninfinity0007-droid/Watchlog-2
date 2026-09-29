<#
  Windows regression for the "installer stuck updating files" failure class.

  Starts a target WatchLog agent/UI/launcher plus a same-named agent outside the
  install directory. Preflight must stop only the target processes, unlock and
  back up the full payload, and rollback must restore that payload.
#>
$ErrorActionPreference = "Stop"

$RepoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..\..")).Path
$Helper = Join-Path $RepoRoot "prototype\installer\nsis\wl-upgrade.ps1"
$Root = Join-Path $env:TEMP ("watchlog-upgrade-test-" + [guid]::NewGuid().ToString("N"))
$Install = Join-Path $Root "WatchLog"
$Other = Join-Path $Root "Other"
$Data = Join-Path $Root "ProgramData"
$TaskName = "WatchLog Upgrade Test " + [guid]::NewGuid().ToString("N")
$procs = @()

function Assert([bool]$ok, [string]$message) {
  if (-not $ok) { throw "ASSERTION FAILED: $message" }
}

function Alive($p) {
  if (-not $p) { return $false }
  return $null -ne (Get-Process -Id $p.Id -ErrorAction SilentlyContinue)
}

function ExactProcessCount([string]$ExpectedPath) {
  $want = [System.IO.Path]::GetFullPath($ExpectedPath)
  try {
    return @(
      Get-CimInstance Win32_Process -ErrorAction SilentlyContinue |
        Where-Object {
          $_.ExecutablePath -and
          ([System.IO.Path]::GetFullPath([string]$_.ExecutablePath) -ieq $want)
        }
    ).Count
  } catch {
    return -1
  }
}

try {
  New-Item -ItemType Directory -Force -Path $Install, $Other, $Data | Out-Null

  $cmd = Join-Path $env:SystemRoot "System32\cmd.exe"
  Copy-Item $cmd (Join-Path $Install "watchlog-agent.exe")
  Copy-Item $cmd (Join-Path $Install "watchlog-setup-ui.exe")
  Copy-Item $cmd (Join-Path $Other "watchlog-agent.exe")

  @'
$agent = Join-Path $PSScriptRoot "watchlog-agent.exe"
& $agent /d /c "ping -n 300 127.0.0.1 >nul"
'@ | Set-Content (Join-Path $Install "run-agent.ps1") -Encoding UTF8
  Set-Content (Join-Path $Install "register-service.ps1") '# OLD register' -Encoding UTF8
  Set-Content (Join-Path $Install "apply-remote-update.ps1") '# OLD updater' -Encoding UTF8
  Set-Content (Join-Path $Install "wl-upgrade.ps1") '# OLD helper' -Encoding UTF8
  Set-Content (Join-Path $Install "READ ME FIRST.txt") 'OLD README' -Encoding UTF8
  Set-Content (Join-Path $Install "setup.ico") 'OLD ICON'
  Set-Content (Join-Path $Install "watchlog.defaults.ini") 'OLD DEFAULTS' -Encoding UTF8

  $setup = Start-Process -FilePath (Join-Path $Install "watchlog-setup-ui.exe") -ArgumentList @('/d','/c','ping -t 127.0.0.1 >NUL') -PassThru -WindowStyle Hidden
  $launcherScript = '"' + (Join-Path $Install "run-agent.ps1") + '"'
  $launcher = Start-Process -FilePath "powershell.exe" -ArgumentList @('-NoProfile','-ExecutionPolicy','Bypass','-File',$launcherScript) -PassThru -WindowStyle Hidden
  $otherAgent = Start-Process -FilePath (Join-Path $Other "watchlog-agent.exe") -ArgumentList @('/d','/c','ping -t 127.0.0.1 >NUL') -PassThru -WindowStyle Hidden
  # Reproduce the field failure: an AV/indexing-like process can retain an
  # exclusive payload handle beyond the old hard-coded 10 second unlock window.
  # Use a real helper script + ready marker so this test proves the lock was
  # acquired before preflight begins (inline -Command quoting was race-prone).
  $lockTarget = Join-Path $Install "READ ME FIRST.txt"
  $lockReady = Join-Path $Root "payload-lock.ready"
  $lockScriptPath = Join-Path $Root "hold-payload-lock.ps1"
  @'
param([string]$Path, [string]$Ready)
$fs = [System.IO.File]::Open(
  $Path,
  [System.IO.FileMode]::Open,
  [System.IO.FileAccess]::Read,
  [System.IO.FileShare]::None
)
try {
  Set-Content -LiteralPath $Ready -Value "locked" -Encoding ASCII
  Start-Sleep -Seconds 12
}
finally {
  $fs.Dispose()
}
'@ | Set-Content -LiteralPath $lockScriptPath -Encoding UTF8

  $lockHolder = Start-Process -FilePath "powershell.exe" -ArgumentList @(
    '-NoProfile','-ExecutionPolicy','Bypass','-File',
    ('"{0}"' -f $lockScriptPath),
    ('"{0}"' -f $lockTarget),
    ('"{0}"' -f $lockReady)
  ) -PassThru -WindowStyle Hidden
  $procs = @($setup,$launcher,$otherAgent,$lockHolder)

  $readyDeadline = (Get-Date).AddSeconds(5)
  while (-not (Test-Path -LiteralPath $lockReady) -and (Get-Date) -lt $readyDeadline) {
    Start-Sleep -Milliseconds 100
  }
  Assert (Test-Path -LiteralPath $lockReady) "delayed-lock fixture never acquired the payload file"
  Assert (Alive $lockHolder) "delayed-lock fixture exited before preflight"

  Start-Sleep -Seconds 2
  Assert ((ExactProcessCount (Join-Path $Install "watchlog-agent.exe")) -ge 1) "target agent child did not start through run-agent.ps1"
  Assert (Alive $setup) "target setup UI did not start"
  Assert (Alive $launcher) "target launcher did not start"
  Assert (Alive $otherAgent) "same-named outside agent did not start"

  $preflightStarted = Get-Date
  & powershell.exe -NoProfile -ExecutionPolicy Bypass -File $Helper -Stage preflight -InstallDir $Install -TaskName $TaskName -StopTimeoutSec 20 -DataRootOverride $Data
  $preflightElapsed = ((Get-Date) - $preflightStarted).TotalSeconds
  Assert ($LASTEXITCODE -eq 0) "preflight returned $LASTEXITCODE"
  Assert ($preflightElapsed -ge 8) "preflight did not actually wait for delayed payload lock ($([math]::Round($preflightElapsed,1))s)"

  Start-Sleep -Milliseconds 500
  Assert ((ExactProcessCount (Join-Path $Install "watchlog-agent.exe")) -eq 0) "target agent survived preflight"
  Assert (-not (Alive $setup)) "target setup UI survived preflight"
  Assert (-not (Alive $launcher)) "target run-agent.ps1 launcher survived preflight"
  Assert (Alive $otherAgent) "preflight killed same-named process outside InstallDir"
  Assert (-not (Alive $lockHolder)) "delayed lock holder should have exited after its 12-second hold"

  $manifest = Join-Path $Data "upgrade-backup\manifest.json"
  Assert (Test-Path $manifest) "payload rollback manifest was not created"

  foreach ($name in @(
    "watchlog-agent.exe","watchlog-setup-ui.exe","run-agent.ps1",
    "register-service.ps1","apply-remote-update.ps1","wl-upgrade.ps1",
    "READ ME FIRST.txt","setup.ico","watchlog.defaults.ini"
  )) {
    $path = Join-Path $Install $name
    $fs = [System.IO.File]::Open($path,[System.IO.FileMode]::Open,[System.IO.FileAccess]::ReadWrite,[System.IO.FileShare]::None)
    $fs.Close()
    $fs.Dispose()
  }

  Set-Content (Join-Path $Install "READ ME FIRST.txt") 'NEW README' -Encoding UTF8
  Set-Content (Join-Path $Install "run-agent.ps1") '# NEW runner' -Encoding UTF8
  Remove-Item (Join-Path $Install "setup.ico") -Force

  & powershell.exe -NoProfile -ExecutionPolicy Bypass -File $Helper -Stage rollback -InstallDir $Install -TaskName $TaskName -StopTimeoutSec 6 -DataRootOverride $Data
  # This isolated test deliberately starts with no Scheduled Task. Field recovery
  # must restore the payload AND reconstruct/start the WatchLog supervision task
  # instead of leaving the site dark because a prior task was missing/disabled.
  Assert ($LASTEXITCODE -eq 0) "rollback should repair/restart the missing task, got $LASTEXITCODE"
  $repairedTask = Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
  Assert ($null -ne $repairedTask) "rollback did not recreate the missing WatchLog task"
  Assert ([string]$repairedTask.State -eq "Running") "recreated WatchLog task is not Running"

  Assert ((Get-Content (Join-Path $Install "READ ME FIRST.txt") -Raw).Trim() -eq "OLD README") "README was not restored"
  $restoredRunner = Get-Content (Join-Path $Install "run-agent.ps1") -Raw
  Assert ($restoredRunner -match 'watchlog-agent\.exe' -and $restoredRunner -match 'ping -n 300') "runner was not restored"
  Assert (Test-Path (Join-Path $Install "setup.ico")) "deleted payload file was not restored"
  Assert (-not (Test-Path (Join-Path $Data "upgrade-backup"))) "rollback backup was not cleared"
  Assert (Alive $otherAgent) "rollback touched same-named process outside InstallDir"

  Write-Host "PASS: upgrade preflight stops target WatchLog runtime/UI/launcher, leaves unrelated process alone, unlocks payload, and rollback restores all files."
  exit 0
}
finally {
  try { Stop-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue | Out-Null } catch {}
  try { Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false -ErrorAction SilentlyContinue } catch {}
  foreach ($p in $procs) {
    if ($p) {
      try { Stop-Process -Id $p.Id -Force -ErrorAction SilentlyContinue } catch {}
    }
  }
  try { Remove-Item -LiteralPath $Root -Recurse -Force -ErrorAction SilentlyContinue } catch {}
}
