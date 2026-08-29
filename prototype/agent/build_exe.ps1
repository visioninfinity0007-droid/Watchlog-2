# WatchLog — freeze the agent to a one-file Windows .exe.
#
#   powershell -ExecutionPolicy Bypass -File agent\build_exe.ps1
#
# Output: dist\watchlog-agent.exe  (about 16 MB)
#
# LEAN ON PURPOSE. The exe carries the agent, its drivers, discovery and
# the setup wizard - and nothing heavy. The false-alarm filter fails open
# when its runtime is absent (see agent/vision.py), so torch, ultralytics,
# numpy, PIL and onnxruntime are all EXCLUDED here. That keeps the client
# download small and the build fast. Data flows without them; the filter
# is enabled later by shipping a build that bundles onnxruntime + the
# yolov8n.onnx model. See the note at the bottom.
#
# The exe is UNSIGNED. Windows SmartScreen will show "Windows protected
# your PC" - the client clicks More info > Run anyway. A code-signing
# certificate removes that prompt and is the right fix before wide rollout.

$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
Set-Location $root

Write-Host "Installing build dependencies..." -ForegroundColor Cyan
python -m pip install --disable-pip-version-check --quiet pyinstaller requests

Write-Host "Freezing agent (lean)..." -ForegroundColor Cyan
python -m PyInstaller `
    --onefile --name watchlog-agent --console --clean --noconfirm `
    --distpath dist --workpath build --specpath build `
    --hidden-import requests `
    --exclude-module torch --exclude-module ultralytics --exclude-module onnxruntime `
    --exclude-module numpy --exclude-module PIL --exclude-module matplotlib `
    --exclude-module tkinter --exclude-module pandas --exclude-module scipy `
    --exclude-module pytest --exclude-module IPython `
    agent\watchlog_agent.py

$exe = Join-Path $root "dist\watchlog-agent.exe"
if (-not (Test-Path $exe)) { throw "build produced no exe" }
$mb = [math]::Round((Get-Item $exe).Length / 1MB, 1)
Write-Host ""
Write-Host "Built $exe ($mb MB)" -ForegroundColor Green
Write-Host ""
Write-Host "To make a client installer zip:" -ForegroundColor Yellow
Write-Host "  powershell -ExecutionPolicy Bypass -File tools\make_installer.ps1 -Code WL-XXXX-XXXX -SiteName 'Client Site'"
Write-Host ""
Write-Host "TO ENABLE THE FALSE-ALARM FILTER later:" -ForegroundColor Yellow
Write-Host "  1. export a model:  yolo export model=yolov8n.pt format=onnx  (needs ultralytics once)"
Write-Host "  2. put it at        prototype\models\yolov8n.onnx"
Write-Host "  3. rebuild adding:  --collect-binaries onnxruntime --hidden-import onnxruntime"
Write-Host "     and drop --exclude-module for onnxruntime/numpy/PIL"
Write-Host "  The packager then bundles the model automatically."
