<#
  Windows unit tests for the transactional upgrade helper (installer/nsis/wl-upgrade.ps1).

  Proves the properties that make an upgrade incapable of false success and self-recovering,
  using REAL Windows file locks and REAL processes (no mocks):

    * a LOCKED watchlog-agent.exe makes preflight REFUSE to overwrite (old runtime preserved);
    * an unlocked binary lets preflight succeed and back up the current binary;
    * commit FAILS when the installed file/runtime version != the expected release
      (so a binary that was not actually replaced can never be reported as a successful upgrade);
    * commit FAILS when the binary is missing; usage error when -ExpectedVersion is absent;
    * rollback restores the previously working binary from the backup;
    * Stop-Agent terminates ONLY the exact watchlog-agent.exe under the install dir and leaves
      unrelated processes untouched (never a broad Python/system kill).

  Runs standalone: exits 0 on success, non-zero (with a summary) on any failure.
#>
$ErrorActionPreference = "Stop"
$here   = Split-Path -Parent $MyInvocation.MyCommand.Path
$script = Join-Path $here "..\installer\nsis\wl-upgrade.ps1"
if (-not (Test-Path $script)) { Write-Error "wl-upgrade.ps1 not found at $script"; exit 2 }

$fails = New-Object System.Collections.Generic.List[string]
function Check($name, [bool]$ok) {
  if ($ok) { Write-Host "PASS  $name" } else { Write-Host "FAIL  $name"; $fails.Add($name) }
}
function New-Sandbox { $t = Join-Path $env:TEMP ("wlup_" + [guid]::NewGuid().ToString('N').Substring(0,8)); New-Item -ItemType Directory -Force -Path $t | Out-Null; return $t }
function Run-Stage($installDir, $stage, [string[]]$extra) {
  $args = @("-NoProfile","-ExecutionPolicy","Bypass","-File",$script,"-Stage",$stage,"-InstallDir",$installDir,"-StopTimeoutSec","5","-StartTimeoutSec","4") + $extra
  return (Start-Process powershell.exe -ArgumentList $args -Wait -PassThru -WindowStyle Hidden).ExitCode
}

# 1 + 2 — locked vs unlocked preflight
$box = New-Sandbox
try {
  $agent = Join-Path $box "watchlog-agent.exe"
  Set-Content -LiteralPath $agent -Value "old" -Encoding ascii
  $fs = [System.IO.File]::Open($agent,'Open','ReadWrite','None')
  $locked = Run-Stage $box 'preflight' @()
  $fs.Close(); $fs.Dispose()
  Check "locked binary => preflight refuses (exit 10)" ($locked -eq 10)
  $free = Run-Stage $box 'preflight' @()
  Check "unlocked binary => preflight succeeds (exit 0)" ($free -eq 0)
  Check "preflight backs up the current binary" (Test-Path (Join-Path $box "watchlog-agent.exe.wlbak"))
} finally { Remove-Item -Recurse -Force $box -ErrorAction SilentlyContinue }

# 3 + 4 + 5 — version gate, missing binary, usage, rollback
$box = New-Sandbox
try {
  $agent = Join-Path $box "watchlog-agent.exe"
  Set-Content -LiteralPath $agent -Value "OLD-0.3.4" -Encoding ascii
  $null = Run-Stage $box 'preflight' @()
  $mism = Run-Stage $box 'verify-version' @("-ExpectedVersion","0.3.6")
  Check "verify-version with wrong/absent version => refuses false success (exit 11)" ($mism -eq 11)
  $usage = Run-Stage $box 'verify-version' @()
  Check "verify-version without -ExpectedVersion => usage error (exit 20)" ($usage -eq 20)
  # corrupt the (half-)installed binary then roll back to the backed-up old one
  Set-Content -LiteralPath $agent -Value "HALF-BROKEN" -Encoding ascii
  $rb = Run-Stage $box 'rollback' @()
  Check "rollback succeeds (exit 0)" ($rb -eq 0)
  Check "rollback restores the previous working binary" (((Get-Content -LiteralPath $agent -Raw).Trim()) -eq "OLD-0.3.4")
  Remove-Item -LiteralPath $agent -Force
  $missing = Run-Stage $box 'verify-version' @("-ExpectedVersion","0.3.6")
  Check "verify-version with missing binary => fails (exit 11)" ($missing -eq 11)
} finally { Remove-Item -Recurse -Force $box -ErrorAction SilentlyContinue }

# 6 — targeted kill: only the exact watchlog-agent.exe dies; unrelated processes survive
$box = New-Sandbox
try {
  $stub = Join-Path $box "watchlog-agent.exe"
  # cmd.exe launches cleanly from any path (unlike powershell.exe, which errors 0xc0000142 when
  # copied) so the stub raises no app-error dialog. It is a real, killable process named
  # watchlog-agent.exe living under the sandbox install dir.
  $cmd = Join-Path $env:SystemRoot "System32\cmd.exe"
  Copy-Item $cmd $stub -Force
  $victim   = Start-Process $stub -ArgumentList "/c","ping -n 130 127.0.0.1 >nul" -PassThru -WindowStyle Hidden
  $bystander= Start-Process $cmd -ArgumentList "/c","ping -n 130 127.0.0.1 >nul" -PassThru -WindowStyle Hidden
  Start-Sleep -Seconds 2
  $null = Run-Stage $box 'preflight' @()
  Start-Sleep -Seconds 1
  $victimDead    = $victim.HasExited -or (-not (Get-Process -Id $victim.Id -ErrorAction SilentlyContinue))
  $bystanderLive = -not $bystander.HasExited -and (Get-Process -Id $bystander.Id -ErrorAction SilentlyContinue)
  Check "targeted stop kills the exact watchlog-agent.exe" $victimDead
  Check "targeted stop leaves an unrelated process running (no broad kill)" ([bool]$bystanderLive)
  Stop-Process -Id $bystander.Id -Force -ErrorAction SilentlyContinue
  if (-not $victim.HasExited) { Stop-Process -Id $victim.Id -Force -ErrorAction SilentlyContinue }
} finally { Remove-Item -Recurse -Force $box -ErrorAction SilentlyContinue }

Write-Host ""
if ($fails.Count -eq 0) { Write-Host "wl-upgrade transactional tests: ALL PASS"; exit 0 }
else { Write-Host ("wl-upgrade transactional tests: {0} FAILED -> {1}" -f $fails.Count, ($fails -join '; ')); exit 1 }
