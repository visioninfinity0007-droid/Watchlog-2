# WatchLog prototype — freeze the agent to a one-file Windows .exe.
#
# PyInstaller only, deliberately. NSIS packaging is Milestone 4; this is
# just enough to prove distribution to a machine that has never had
# Python installed.
#
#   powershell -ExecutionPolicy Bypass -File agent\build_exe.ps1
#
# Output: dist\watchlog-agent.exe
#
# The exe is UNSIGNED. Windows SmartScreen and most antivirus will flag a
# freshly-built unsigned PyInstaller binary. Discover that on your own
# second machine now, not in front of the client at M4. See README §Risks.

$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
Set-Location $root

Write-Host "Installing build dependencies..." -ForegroundColor Cyan
python -m pip install --disable-pip-version-check --quiet pyinstaller requests

Write-Host "Freezing agent..." -ForegroundColor Cyan
python -m PyInstaller `
    --onefile `
    --name watchlog-agent `
    --console `
    --clean `
    --noconfirm `
    --distpath dist `
    --workpath build `
    --specpath build `
    agent\watchlog_agent.py

$exe = Join-Path $root "dist\watchlog-agent.exe"
if (-not (Test-Path $exe)) { throw "build produced no exe" }

$mb = [math]::Round((Get-Item $exe).Length / 1MB, 1)
Write-Host ""
Write-Host "Built $exe ($mb MB)" -ForegroundColor Green
Write-Host ""
Write-Host "To ship it to a second machine, copy BOTH:" -ForegroundColor Yellow
Write-Host "  dist\watchlog-agent.exe"
Write-Host "  watchlog.ini   (with that machine's own enrollment code)"
Write-Host ""
Write-Host "The exe is unsigned. Expect a SmartScreen prompt." -ForegroundColor Yellow
