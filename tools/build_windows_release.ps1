# WatchLog - build the production Windows release (NSIS only).
#
#   powershell -ExecutionPolicy Bypass -File tools\build_windows_release.ps1
#   ...\build_windows_release.ps1 -Code WL-XXXX-XXXX -PublisherUrl https://watchlog.pk
#   ...\build_windows_release.ps1 -Lean
#   ...\build_windows_release.ps1 -SignPfx cert.pfx -SignPassword ****
#
# One authoritative path: agent exe -> staged public config -> NSIS ->
# WatchLog-Setup.exe -> SHA256 -> optional Authenticode signing.

param(
  [string]$Code = "",
  [string]$PublisherUrl = "https://watchlog.pk",
  [switch]$Lean,
  [string]$SignPfx = "",
  [string]$SignPassword = ""
)
$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
Set-Location $root

# 1) Build the Site Agent executable. The AI build is the production default.
$buildArgs = @()
if (-not $Lean) { $buildArgs += "-WithAI" }
Write-Host "Building WatchLog Site Agent..." -ForegroundColor Cyan
& powershell -ExecutionPolicy Bypass -File "prototype\agent\build_exe.ps1" @buildArgs
$exe = Join-Path $root "prototype\dist\watchlog-agent.exe"
if (-not (Test-Path $exe)) { throw "agent exe not built at $exe" }

# 2) Stage the exact NSIS payload.
$stage = Join-Path $env:TEMP ("wl-nsis-" + [guid]::NewGuid().ToString('N').Substring(0,8))
New-Item -ItemType Directory -Force -Path $stage | Out-Null
$inst = Join-Path $root "prototype\installer"
Copy-Item $exe (Join-Path $stage "watchlog-agent.exe")
foreach ($f in @("run-agent.cmd","register-service.ps1","READ ME FIRST.txt","setup.ico")) {
  Copy-Item (Join-Path $inst $f) (Join-Path $stage $f)
}
Copy-Item (Join-Path $inst "nsis\watchlog.nsi") (Join-Path $stage "watchlog.nsi")

# Public defaults only. Recorder credentials are collected and proven locally
# during first run and are never baked into a release artifact.
$cfg = @{}
Get-Content (Join-Path $root ".env") | ForEach-Object {
  if ($_ -match '^\s*([A-Za-z0-9_]+)\s*=\s*(.*)$') { $cfg[$Matches[1]] = $Matches[2].Trim().Trim('"').Trim("'") }
}
$supaUrl = $cfg["SUPABASE_URL"]; $pubKey = $cfg["SUPABASE_PUBLISHABLE_KEY"]
if (-not $supaUrl) { throw "no SUPABASE_URL in .env" }
if (-not $pubKey) { throw "no SUPABASE_PUBLISHABLE_KEY in .env" }
@"
; WatchLog public installation defaults.
[watchlog]
supabase_url = $supaUrl
supabase_publishable_key = $pubKey
enrollment_code = $Code
nvr_driver = auto
"@ | Set-Content -Path (Join-Path $stage "watchlog.defaults.ini") -Encoding UTF8

# 3) Compile with NSIS.
$out = Join-Path $root "dist-installer"
New-Item -ItemType Directory -Force -Path $out | Out-Null
$setup = Join-Path $out "WatchLog-Setup.exe"
$makensis = (Get-Command makensis -ErrorAction SilentlyContinue).Source
if (-not $makensis) {
  foreach ($p in @("$env:ProgramFiles\NSIS\makensis.exe","${env:ProgramFiles(x86)}\NSIS\makensis.exe")) {
    if (Test-Path $p) { $makensis = $p; break }
  }
}
if (-not $makensis) { throw "makensis not found. Install NSIS: winget install NSIS.NSIS" }

Write-Host "Compiling WatchLog-Setup.exe with NSIS..." -ForegroundColor Cyan
Push-Location $stage
& $makensis "/DICON=setup.ico" "/DPUBLISHER_URL=$PublisherUrl" "/DOUTFILE=$setup" "watchlog.nsi" | Out-Host
$rc = $LASTEXITCODE
Pop-Location
if ($rc -ne 0 -or -not (Test-Path $setup)) { throw "makensis failed (exit $rc)" }

# 4) Checksum.
$hash = (Get-FileHash $setup -Algorithm SHA256).Hash
Set-Content -Path "$setup.sha256" -Value "$hash  WatchLog-Setup.exe" -Encoding ascii
$mb = [math]::Round((Get-Item $setup).Length / 1MB, 1)
Write-Host ""
Write-Host "Built $setup ($mb MB)" -ForegroundColor Green
Write-Host "  SHA256 $hash" -ForegroundColor Green

# 5) Optional Authenticode signing. Until a certificate is supplied this is
# the one known release blocker that can trigger SmartScreen on a new PC.
if ($SignPfx) {
  $signtool = (Get-Command signtool -ErrorAction SilentlyContinue).Source
  if ($signtool) {
    & $signtool sign /f $SignPfx /p $SignPassword /fd SHA256 /tr http://timestamp.digicert.com /td SHA256 $setup
    if ($LASTEXITCODE -ne 0) { throw "signtool failed" }
    Write-Host "Signed." -ForegroundColor Green
  } else { throw "-SignPfx was supplied but signtool was not found" }
} else {
  Write-Host "No signing certificate supplied; release is unsigned and may trigger SmartScreen." -ForegroundColor Yellow
}
Remove-Item -Recurse -Force $stage
