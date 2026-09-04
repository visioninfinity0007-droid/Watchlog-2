# Build the customer-facing WatchLog setup UI as a windowed one-file EXE.
# The long-running Site Agent is built separately; this executable exists only
# for setup/migration/diagnostics and therefore never opens a console window.

$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
Set-Location $root

Write-Host "Installing WatchLog setup UI build dependencies..." -ForegroundColor Cyan
python -m pip install --disable-pip-version-check --quiet pyinstaller requests pyside6

$icon = Join-Path $root "installer\setup.ico"
$args = @(
  "--onefile", "--windowed", "--name", "watchlog-setup-ui", "--clean", "--noconfirm",
  "--distpath", "dist", "--workpath", "build-setup-ui", "--specpath", "build-setup-ui",
  "--icon", $icon,
  "--add-data", "$icon;.",
  "--hidden-import", "requests",
  "--hidden-import", "PySide6.QtCore",
  "--hidden-import", "PySide6.QtGui",
  "--hidden-import", "PySide6.QtWidgets",
  "--collect-all", "PySide6",
  "--exclude-module", "torch", "--exclude-module", "ultralytics",
  "--exclude-module", "matplotlib", "--exclude-module", "pandas",
  "--exclude-module", "scipy", "--exclude-module", "pytest",
  "agent\setup_gui.py"
)

Write-Host "Freezing branded WatchLog setup UI..." -ForegroundColor Cyan
python -m PyInstaller @args
if ($LASTEXITCODE -ne 0) { throw "setup UI PyInstaller build failed (exit $LASTEXITCODE)" }

$exe = Join-Path $root "dist\watchlog-setup-ui.exe"
if (-not (Test-Path $exe)) { throw "setup UI build produced no exe" }
$bytes = (Get-Item $exe).Length
if ($bytes -lt 5MB) { throw "setup UI is suspiciously small ($bytes bytes)" }
Write-Host "Built $exe ($([math]::Round($bytes / 1MB, 1)) MB)" -ForegroundColor Green
