# WatchLog - build the production Windows release (NSIS installer).
#
#   powershell -ExecutionPolicy Bypass -File tools\build_windows_release.ps1
#   ...\build_windows_release.ps1 -Code WL-XXXX-XXXX -PublisherUrl https://watchlog.pk
#   ...\build_windows_release.ps1 -Lean                 # data-only exe (filter fails open)
#   ...\build_windows_release.ps1 -SignPfx cert.pfx -SignPassword ****   # signed
#
# Steps: build the agent exe (AI build by default) -> stage the payload ->
# compile watchlog.nsi with makensis -> WatchLog-Setup.exe + SHA256.
# Everything is config-driven; no hostnames are hardcoded in the installer.

param(
  [string]$Code = "",
  [string]$PublisherUrl = "https://watchlog.161.97.175.15.sslip.io",
  [switch]$Lean,
  [string]$SignPfx = "",
  [string]$SignPassword = ""
)

$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
Set-Location $root

# --- 1) build the agent exe ---------------------------------------------
$buildArgs = @()
if (-not $Lean) { $buildArgs += "-WithAI" }
Write-Host "Building the agent exe..." -ForegroundColor Cyan
& powershell -ExecutionPolicy Bypass -File "prototype\agent\build_exe.ps1" @buildArgs
$exe = Join-Path $root "prototype\dist\watchlog-agent.exe"
if (-not (Test-Path $exe)) { throw "agent exe not built at $exe" }

# --- 2) stage the payload beside the .nsi -------------------------------
$stage = Join-Path $env:TEMP ("wl-nsis-" + [guid]::NewGuid().ToString('N').Substring(0,8))
New-Item -ItemType Directory -Force -Path $stage | Out-Null
$inst = Join-Path $root "prototype\installer"
Copy-Item $exe (Join-Path $stage "watchlog-agent.exe")
foreach ($f in @("run-agent.cmd","register-service.ps1","READ ME FIRST.txt","setup.ico")) {
  Copy-Item (Join-Path $inst $f) (Join-Path $stage $f)
}
Copy-Item (Join-Path $inst "nsis\watchlog.nsi") (Join-Path $stage "watchlog.nsi")

# watchlog.defaults.ini - public values only (publishable key is public),
# generated from .env like the Inno packager. The setup wizard overwrites
# the recorder-specific fields at install time.
$env = @{}
Get-Content (Join-Path $root ".env") | ForEach-Object {
  if ($_ -match '^\s*([A-Za-z0-9_]+)\s*=\s*(.*)$') { $env[$Matches[1]] = $Matches[2].Trim().Trim('"').Trim("'") }
}
$supaUrl = $env["SUPABASE_URL"]; $pubKey = $env["SUPABASE_PUBLISHABLE_KEY"]
if (-not $pubKey) { throw "no SUPABASE_PUBLISHABLE_KEY in .env" }
@"
; WatchLog site defaults. Public values only - safe to ship.
[watchlog]
supabase_url = $supaUrl
supabase_publishable_key = $pubKey
enrollment_code = $Code
nvr_driver = auto
"@ | Set-Content -Path (Join-Path $stage "watchlog.defaults.ini") -Encoding UTF8

# --- 3) compile with makensis -------------------------------------------
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

Write-Host "Compiling installer with makensis..." -ForegroundColor Cyan
Push-Location $stage
& $makensis "/DICON=setup.ico" "/DPUBLISHER_URL=$PublisherUrl" "/DOUTFILE=$setup" "watchlog.nsi" | Out-Host
$rc = $LASTEXITCODE
Pop-Location
if ($rc -ne 0 -or -not (Test-Path $setup)) { throw "makensis failed (exit $rc)" }

# --- 4) checksum ---------------------------------------------------------
$hash = (Get-FileHash $setup -Algorithm SHA256).Hash
Set-Content -Path "$setup.sha256" -Value "$hash  WatchLog-Setup.exe" -Encoding ascii
$mb = [math]::Round((Get-Item $setup).Length / 1MB, 1)
Write-Host ""
Write-Host "Built $setup ($mb MB)" -ForegroundColor Green
Write-Host "  SHA256 $hash" -ForegroundColor Green

# --- 5) optional signing -------------------------------------------------
if ($SignPfx) {
  $signtool = (Get-Command signtool -ErrorAction SilentlyContinue).Source
  if ($signtool) {
    & $signtool sign /f $SignPfx /p $SignPassword /fd SHA256 /tr http://timestamp.digicert.com /td SHA256 $setup
    Write-Host "Signed." -ForegroundColor Green
  } else { Write-Host "signtool not found; UNSIGNED (SmartScreen will warn)." -ForegroundColor Yellow }
} else {
  Write-Host "No cert (-SignPfx) given; UNSIGNED (SmartScreen will warn on fresh PCs)." -ForegroundColor Yellow
}

Remove-Item -Recurse -Force $stage
