# WatchLog - build the production Windows release (NSIS only).
#
#   powershell -ExecutionPolicy Bypass -File tools\build_windows_release.ps1
#   ...\build_windows_release.ps1 -Code WL-XXXX-XXXX -PublisherUrl https://<production-domain>
#   ...\build_windows_release.ps1 -SupabaseUrl https://<project>.supabase.co -SupabasePublishableKey <public-key>
#   ...\build_windows_release.ps1 -Lean
#   ...\build_windows_release.ps1 -SignPfx cert.pfx -SignPassword ****
#
# Authoritative path:
#   AI Site Agent -> branded setup UI -> optional inner signing -> staged
#   public config -> NSIS -> optional installer signing -> FINAL SHA256.

param(
  [string]$Code = "",
  [string]$PublisherUrl = "",
  [string]$SupabaseUrl = "",
  [string]$SupabasePublishableKey = "",
  [switch]$Lean,
  [string]$SignPfx = "",
  [string]$SignPassword = ""
)
$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
Set-Location $root

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
    throw "signature verification failed for ${Path}: $($signature.Status) $($signature.StatusMessage)"
  }
  Write-Host "  Authenticode signature valid." -ForegroundColor Green
}

function Read-DotEnv([string]$Path) {
  $values = @{}
  if (-not (Test-Path $Path)) { return $values }
  Get-Content $Path | ForEach-Object {
    if ($_ -match '^\s*([A-Za-z0-9_]+)\s*=\s*(.*)$') {
      $values[$Matches[1]] = $Matches[2].Trim().Trim('"').Trim("'")
    }
  }
  return $values
}

# 1) Build the operational Site Agent. Production defaults to AI enabled.
$buildArgs = @()
if (-not $Lean) { $buildArgs += "-WithAI" }
Write-Host "Building WatchLog Site Agent..." -ForegroundColor Cyan
& powershell -ExecutionPolicy Bypass -File "prototype\agent\build_exe.ps1" @buildArgs
if ($LASTEXITCODE -ne 0) { throw "Site Agent build failed (exit $LASTEXITCODE)" }
$agentExe = Join-Path $root "prototype\dist\watchlog-agent.exe"
if (-not (Test-Path $agentExe)) { throw "agent exe not built at $agentExe" }
$agentBytes = (Get-Item $agentExe).Length
$minimumAgentBytes = if ($Lean) { 1MB } else { 5MB }
if ($agentBytes -lt $minimumAgentBytes) {
  throw "agent executable is suspiciously small ($agentBytes bytes); refusing to package a stub/incomplete build"
}

# 2) Build the customer-facing windowed setup application.
Write-Host "Building branded WatchLog setup UI..." -ForegroundColor Cyan
& powershell -ExecutionPolicy Bypass -File "prototype\agent\build_setup_gui.ps1"
if ($LASTEXITCODE -ne 0) { throw "WatchLog setup UI build failed (exit $LASTEXITCODE)" }
$setupUiExe = Join-Path $root "prototype\dist\watchlog-setup-ui.exe"
if (-not (Test-Path $setupUiExe)) { throw "setup UI exe not built at $setupUiExe" }
$setupUiBytes = (Get-Item $setupUiExe).Length
if ($setupUiBytes -lt 5MB) {
  throw "setup UI is suspiciously small ($setupUiBytes bytes); refusing to package an incomplete build"
}

# Sign both inner executables before embedding them in NSIS.
Sign-WatchLogArtifact $agentExe
Sign-WatchLogArtifact $setupUiExe

# 3) Stage the exact NSIS payload.
$stage = Join-Path $env:TEMP ("wl-nsis-" + [guid]::NewGuid().ToString('N').Substring(0,8))
New-Item -ItemType Directory -Force -Path $stage | Out-Null
try {
  $inst = Join-Path $root "prototype\installer"
  Copy-Item $agentExe (Join-Path $stage "watchlog-agent.exe")
  Copy-Item $setupUiExe (Join-Path $stage "watchlog-setup-ui.exe")
  foreach ($f in @("run-agent.ps1","register-service.ps1","READ ME FIRST.txt","setup.ico")) {
    Copy-Item (Join-Path $inst $f) (Join-Path $stage $f)
  }
  Copy-Item (Join-Path $inst "nsis\watchlog.nsi") (Join-Path $stage "watchlog.nsi")

  # Public defaults only. Recorder credentials are collected/protected locally
  # by the graphical setup app and are never baked into a release artifact.
  $cfg = Read-DotEnv (Join-Path $root ".env")
  $supaUrl = $SupabaseUrl
  if (-not $supaUrl) { $supaUrl = $env:SUPABASE_URL }
  if (-not $supaUrl) { $supaUrl = $cfg["SUPABASE_URL"] }
  $pubKey = $SupabasePublishableKey
  if (-not $pubKey) { $pubKey = $env:SUPABASE_PUBLISHABLE_KEY }
  if (-not $pubKey) { $pubKey = $cfg["SUPABASE_PUBLISHABLE_KEY"] }
  if (-not $supaUrl) { throw "no Supabase URL supplied (-SupabaseUrl, SUPABASE_URL, or .env)" }
  if (-not $pubKey) { throw "no Supabase publishable key supplied (-SupabasePublishableKey, SUPABASE_PUBLISHABLE_KEY, or .env)" }

  # Fail-closed guards against the 0.3.3 argument-binding class of bug: a leaked
  # parameter name ('-SupabaseUrl', '-SignPfx', ...) must never be baked into a
  # shipped config.
  if ($supaUrl -notmatch '^https://') { throw "SupabaseUrl is not an https URL: '$supaUrl' (argument-binding leak?)" }
  if ($pubKey -match '^\s*-' -or $pubKey.Length -lt 20) { throw "publishable key looks wrong/leaked: '$pubKey'" }
  if ($Code -and $Code -notmatch '^WL-') { throw "enrollment code is not a WL- code: '$Code' (argument-binding leak?)" }
  if ($PublisherUrl -and $PublisherUrl -notmatch '^https://') { throw "PublisherUrl is not an https URL: '$PublisherUrl' (argument-binding leak?)" }

  $defaultsPath = Join-Path $stage "watchlog.defaults.ini"
  @"
; WatchLog public installation defaults.
[watchlog]
supabase_url = $supaUrl
supabase_publishable_key = $pubKey
enrollment_code = $Code
nvr_driver = auto
"@ | Set-Content -Path $defaultsPath -Encoding UTF8

  # Verify the STAGED config before packaging (defense in depth).
  $staged = Get-Content $defaultsPath -Raw
  if ($staged -notmatch '(?m)^supabase_url = https://') { throw "staged watchlog.defaults.ini has an invalid supabase_url:`n$staged" }
  if ($staged -match '(?m)^enrollment_code = -')       { throw "staged watchlog.defaults.ini has a leaked enrollment_code:`n$staged" }
  Write-Host "Staged public config verified (supabase_url https, no leaked args)." -ForegroundColor Green

  # 4) Compile the final installer with NSIS.
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
  # Single version source: pass wl_version.py's VERSION into NSIS.
  $verFile = Join-Path $root "prototype\agent\wl_version.py"
  $verMatch = Select-String -Path $verFile -Pattern '^VERSION\s*=\s*"([^"]+)"'
  if (-not $verMatch) { throw "could not read VERSION from $verFile" }
  $appVersion = $verMatch.Matches[0].Groups[1].Value
  Write-Host "  Version (from wl_version.py): $appVersion" -ForegroundColor Gray
  $nsisArgs = @("/DICON=setup.ico", "/DOUTFILE=$setup", "/DAPPVERSION=$appVersion")
  if ($PublisherUrl) { $nsisArgs += "/DPUBLISHER_URL=$PublisherUrl" }
  Push-Location $stage
  try {
    $nsisOut = & $makensis @nsisArgs "watchlog.nsi" 2>&1 | Out-String
    $rc = $LASTEXITCODE
  } finally {
    Pop-Location
  }
  Write-Host $nsisOut
  if ($rc -ne 0 -or -not (Test-Path $setup)) { throw "makensis failed (exit $rc)" }
  # Zero-warning release gate: an ignored NSIS warning is exactly how the
  # invalid $PROGRAMDATA paths shipped in 0.3.3.
  $nsisWarnings = ($nsisOut -split "`r?`n") | Where-Object { $_ -match 'warning \d+:' }
  if ($nsisWarnings) { throw "NSIS emitted warnings (fatal for a release build):`n$($nsisWarnings -join "`n")" }
  Write-Host "  NSIS compiled with zero warnings." -ForegroundColor Green

  $setupBytes = (Get-Item $setup).Length
  $minimumSetupBytes = if ($Lean) { 5MB } else { 10MB }
  if ($setupBytes -lt $minimumSetupBytes) {
    throw "WatchLog-Setup.exe is suspiciously small ($setupBytes bytes); refusing to publish a stub/incomplete installer"
  }

  # 5) Sign final installer, then calculate checksum of the exact distributed bytes.
  Sign-WatchLogArtifact $setup
  $hash = (Get-FileHash $setup -Algorithm SHA256).Hash
  Set-Content -Path "$setup.sha256" -Value "$hash  WatchLog-Setup.exe" -Encoding ascii
  $mb = [math]::Round($setupBytes / 1MB, 1)
  Write-Host ""
  Write-Host "Built $setup ($mb MB)" -ForegroundColor Green
  Write-Host "  Site Agent $([math]::Round($agentBytes / 1MB, 1)) MB" -ForegroundColor Gray
  Write-Host "  Setup UI $([math]::Round($setupUiBytes / 1MB, 1)) MB" -ForegroundColor Gray
  Write-Host "  FINAL SHA256 $hash" -ForegroundColor Green
  if ($SignPfx) { Write-Host "  Agent + setup UI + installer signatures verified." -ForegroundColor Green }
  else { Write-Host "  UNSIGNED: supply -SignPfx for a production release." -ForegroundColor Yellow }
  if ($PublisherUrl) { Write-Host "  Publisher URL $PublisherUrl" -ForegroundColor Gray }
  else { Write-Host "  Publisher URL omitted (supply -PublisherUrl for production metadata)." -ForegroundColor Yellow }
} finally {
  if (Test-Path $stage) { Remove-Item -Recurse -Force $stage }
}
