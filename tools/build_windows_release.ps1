# WatchLog - build the production Windows release (NSIS only).
#
#   powershell -ExecutionPolicy Bypass -File tools\build_windows_release.ps1
#   ...\build_windows_release.ps1 -Code WL-XXXX-XXXX -PublisherUrl https://<production-domain>
#   ...\build_windows_release.ps1 -Lean
#   ...\build_windows_release.ps1 -SignPfx cert.pfx -SignPassword ****
#
# One authoritative path: agent exe -> optional Authenticode signing -> staged
# public config -> NSIS -> optional installer signing -> FINAL SHA256.

param(
  [string]$Code = "",
  [string]$PublisherUrl = "",
  [switch]$Lean,
  [string]$SignPfx = "",
  [string]$SignPassword = ""
)
$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
Set-Location $root

# Resolve signing once. A release that was explicitly asked to sign must fail
# closed if the Windows SDK signing tool is unavailable.
$signtool = $null
if ($SignPfx) {
  $signtool = (Get-Command signtool -ErrorAction SilentlyContinue).Source
  if (-not $signtool) {
    $sdkRoots = @(
      "$env:ProgramFiles\Windows Kits\10\bin",
      "${env:ProgramFiles(x86)}\Windows Kits\10\bin"
    ) | Where-Object { Test-Path $_ }
    foreach ($sdkRoot in $sdkRoots) {
      $candidate = Get-ChildItem $sdkRoot -Filter signtool.exe -Recurse -ErrorAction SilentlyContinue |
        Where-Object { $_.FullName -match '\\x64\\signtool\.exe$' } |
        Sort-Object FullName -Descending | Select-Object -First 1
      if ($candidate) { $signtool = $candidate.FullName; break }
    }
  }
  if (-not $signtool) { throw "-SignPfx was supplied but signtool was not found" }
  if (-not (Test-Path $SignPfx)) { throw "signing certificate not found: $SignPfx" }
}

function Sign-WatchLogArtifact([string]$Path) {
  if (-not $SignPfx) { return }
  Write-Host "Signing $([IO.Path]::GetFileName($Path))..." -ForegroundColor Cyan
  & $signtool sign /f $SignPfx /p $SignPassword /fd SHA256 `
      /tr http://timestamp.digicert.com /td SHA256 $Path | Out-Host
  if ($LASTEXITCODE -ne 0) { throw "signtool failed for $Path" }
  $signature = Get-AuthenticodeSignature -FilePath $Path
  if ($signature.Status -ne "Valid") {
    throw "signature verification failed for $Path: $($signature.Status) $($signature.StatusMessage)"
  }
  Write-Host "  Authenticode signature valid." -ForegroundColor Green
}

# 1) Build the Site Agent executable. The AI build is the production default.
$buildArgs = @()
if (-not $Lean) { $buildArgs += "-WithAI" }
Write-Host "Building WatchLog Site Agent..." -ForegroundColor Cyan
& powershell -ExecutionPolicy Bypass -File "prototype\agent\build_exe.ps1" @buildArgs
if ($LASTEXITCODE -ne 0) { throw "Site Agent build failed (exit $LASTEXITCODE)" }
$exe = Join-Path $root "prototype\dist\watchlog-agent.exe"
if (-not (Test-Path $exe)) { throw "agent exe not built at $exe" }

# Sign the inner executable BEFORE it is embedded in the installer. Signing only
# the outer setup leaves the long-running executable itself unsigned.
Sign-WatchLogArtifact $exe

# 2) Stage the exact NSIS payload.
$stage = Join-Path $env:TEMP ("wl-nsis-" + [guid]::NewGuid().ToString('N').Substring(0,8))
New-Item -ItemType Directory -Force -Path $stage | Out-Null
try {
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

  # 3) Compile with NSIS. The publisher URL is deliberately omitted unless a
  # real production URL is supplied; release metadata must never ship a fake URL.
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
  $nsisArgs = @("/DICON=setup.ico", "/DOUTFILE=$setup")
  if ($PublisherUrl) { $nsisArgs += "/DPUBLISHER_URL=$PublisherUrl" }
  Push-Location $stage
  try {
    & $makensis @nsisArgs "watchlog.nsi" | Out-Host
    $rc = $LASTEXITCODE
  } finally {
    Pop-Location
  }
  if ($rc -ne 0 -or -not (Test-Path $setup)) { throw "makensis failed (exit $rc)" }

  # 4) Sign the FINAL installer. The checksum is intentionally generated only
  # after this step because Authenticode changes the file bytes.
  Sign-WatchLogArtifact $setup

  # 5) Final checksum of the exact artifact that will be distributed.
  $hash = (Get-FileHash $setup -Algorithm SHA256).Hash
  Set-Content -Path "$setup.sha256" -Value "$hash  WatchLog-Setup.exe" -Encoding ascii
  $mb = [math]::Round((Get-Item $setup).Length / 1MB, 1)
  Write-Host ""
  Write-Host "Built $setup ($mb MB)" -ForegroundColor Green
  Write-Host "  FINAL SHA256 $hash" -ForegroundColor Green
  if ($SignPfx) { Write-Host "  Agent + installer Authenticode signatures verified." -ForegroundColor Green }
  else { Write-Host "  UNSIGNED: supply -SignPfx for a production release." -ForegroundColor Yellow }
  if ($PublisherUrl) { Write-Host "  Publisher URL $PublisherUrl" -ForegroundColor Gray }
  else { Write-Host "  Publisher URL omitted (supply -PublisherUrl for production metadata)." -ForegroundColor Yellow }
} finally {
  if (Test-Path $stage) { Remove-Item -Recurse -Force $stage }
}
