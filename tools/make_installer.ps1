<#
  Compatibility wrapper for older release notes.

  WatchLog has one production installer technology: NSIS. This command now
  delegates to build_windows_release.ps1 instead of maintaining a second Inno
  Setup manifest with drifting version/publisher metadata.

    powershell -ExecutionPolicy Bypass -File tools\make_installer.ps1 -Code WL-XXXX-XXXX
#>
param(
  [string]$Code = "",
  [string]$SiteName = "site",  # retained so old commands do not fail; artifact name is canonical
  [string]$PublisherUrl = "https://watchlog.pk",
  [switch]$Lean,
  [string]$SignPfx = "",
  [string]$SignPassword = ""
)
$ErrorActionPreference = "Stop"
$script = Join-Path $PSScriptRoot "build_windows_release.ps1"
Write-Host "WatchLog release packaging now uses NSIS only." -ForegroundColor Cyan
Write-Host "Building canonical dist-installer\WatchLog-Setup.exe" -ForegroundColor Gray
& powershell -ExecutionPolicy Bypass -File $script -Code $Code -PublisherUrl $PublisherUrl -Lean:$Lean -SignPfx $SignPfx -SignPassword $SignPassword
if ($LASTEXITCODE -ne 0) { throw "WatchLog NSIS release build failed (exit $LASTEXITCODE)" }
