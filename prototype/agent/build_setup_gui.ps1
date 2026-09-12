# Build the customer-facing WatchLog setup UI as a windowed one-file EXE.
# The long-running Site Agent is built separately; this executable exists only
# for setup/migration/diagnostics and therefore never opens a console window.

$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
Set-Location $root

# Release reproducibility: 0.4.1 and the first 0.4.2 candidate had identical
# recorder-discovery source but were rebuilt on different mutable windows-latest
# images. Pin the freezer version that produced the known-good 0.4.1 release and
# explicitly retain every recorder-discovery/driver module in the frozen setup UI.
# psutil is intentionally included because reliable multi-NIC discovery on the
# customer PC must enumerate active adapters, not only the default internet route.
Write-Host "Installing WatchLog setup UI build dependencies..." -ForegroundColor Cyan
python -m pip install --disable-pip-version-check --quiet "pyinstaller==6.22.2" requests pyside6 psutil
if ($LASTEXITCODE -ne 0) { throw "setup UI dependency install failed (exit $LASTEXITCODE)" }
python -m pip show pyinstaller requests pyside6 psutil | Select-String '^(Name|Version):' | ForEach-Object { Write-Host "  $_" -ForegroundColor Gray }

$icon = Join-Path $root "installer\setup.ico"

# PE version metadata from the single version source (wl_version.py) so Windows
# and CI can read watchlog-setup-ui.exe ProductVersion directly (no console tricks
# on a windowed exe).
$uiVer = (Select-String -Path (Join-Path $root "agent\wl_version.py") -Pattern '^VERSION\s*=\s*"([^"]+)"').Matches[0].Groups[1].Value
if (-not $uiVer) { throw "could not read VERSION from wl_version.py" }
$uvt = ((($uiVer -split '[.+]') + @('0','0','0'))[0..2]) -join ','
New-Item -ItemType Directory -Force -Path (Join-Path $root "build-setup-ui") | Out-Null
$verFile = Join-Path $root "build-setup-ui\watchlog-setup-ui.version.txt"
@"
VSVersionInfo(ffi=FixedFileInfo(filevers=($uvt,0), prodvers=($uvt,0), mask=0x3f, flags=0x0, OS=0x40004, fileType=0x1, subtype=0x0, date=(0,0)),
  kids=[StringFileInfo([StringTable(u'040904B0', [
    StringStruct(u'CompanyName', u'Vision Infinity'),
    StringStruct(u'FileDescription', u'WatchLog Setup'),
    StringStruct(u'FileVersion', u'$uiVer'),
    StringStruct(u'InternalName', u'watchlog-setup-ui'),
    StringStruct(u'OriginalFilename', u'watchlog-setup-ui.exe'),
    StringStruct(u'ProductName', u'WatchLog'),
    StringStruct(u'ProductVersion', u'$uiVer')])]),
  VarFileInfo([VarStruct(u'Translation', [1033, 1200])])])
"@ | Set-Content -Path $verFile -Encoding UTF8

$args = @(
  "--onefile", "--windowed", "--name", "watchlog-setup-ui", "--clean", "--noconfirm",
  "--distpath", "dist", "--workpath", "build-setup-ui", "--specpath", "build-setup-ui",
  "--icon", $icon,
  "--version-file", $verFile,
  "--add-data", "$icon;.",
  "--hidden-import", "requests",
  "--hidden-import", "psutil",
  # Recorder setup is release-critical. Keep these explicit even though most
  # are statically imported, so a PyInstaller graph change cannot silently
  # strip discovery or a vendor driver from a future installer.
  "--hidden-import", "setup_backend",
  "--hidden-import", "discover",
  "--hidden-import", "wsdiscovery",
  "--hidden-import", "drivers",
  "--hidden-import", "drivers.base",
  "--hidden-import", "drivers.dahua",
  "--hidden-import", "drivers.hikvision",
  "--hidden-import", "drivers.onvif_driver",
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
