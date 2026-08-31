# WatchLog — freeze the agent to a one-file Windows .exe.
#
#   Lean (data only, filter fails open):
#     powershell -ExecutionPolicy Bypass -File agent\build_exe.ps1
#
#   Production AI build (bundles onnxruntime + numpy + PIL + yolov8n.onnx):
#     powershell -ExecutionPolicy Bypass -File agent\build_exe.ps1 -WithAI
#
# The AI build is the shippable one: it carries the on-site false-alarm
# filter and its model, so events are filtered before they leave the site.
# The lean build still works — the filter fails open (agent/vision.py) — but
# it reports every event unfiltered, so it is for testing only.
#
# The exe is UNSIGNED. SmartScreen shows "Windows protected your PC"; a
# code-signing certificate removes that and is the right fix before rollout.

param([switch]$WithAI)

$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
Set-Location $root

$common = @(
    "--onefile","--name","watchlog-agent","--console","--clean","--noconfirm",
    "--distpath","dist","--workpath","build","--specpath","build",
    "--hidden-import","requests",
    "--exclude-module","torch","--exclude-module","ultralytics",
    "--exclude-module","matplotlib","--exclude-module","tkinter",
    "--exclude-module","pandas","--exclude-module","scipy",
    "--exclude-module","pytest","--exclude-module","IPython"
)

if ($WithAI) {
    $model = Join-Path $root "models\yolov8n.onnx"
    if (-not (Test-Path $model)) {
        throw "AI build needs the model at prototype\models\yolov8n.onnx. Export it once:
  yolo export model=yolov8n.pt format=onnx   (in a torch/ultralytics env)
then copy it there."
    }
    Write-Host "Installing AI build dependencies (onnxruntime, numpy, pillow)..." -ForegroundColor Cyan
    python -m pip install --disable-pip-version-check --quiet pyinstaller requests onnxruntime numpy pillow
    # numpy is imported dynamically in vision.py, so it must be a hidden import;
    # onnxruntime ships native DLLs, so collect all of it; PIL is imported lazily.
    $ai = @(
        "--hidden-import","numpy",
        "--hidden-import","onnxruntime","--collect-all","onnxruntime",
        "--hidden-import","PIL.Image",
        "--add-data","$model;."
    )
    Write-Host "Freezing agent (production AI build)..." -ForegroundColor Cyan
    python -m PyInstaller @common @ai agent\watchlog_agent.py
} else {
    $lean = @("--exclude-module","onnxruntime","--exclude-module","numpy","--exclude-module","PIL")
    Write-Host "Installing build dependencies..." -ForegroundColor Cyan
    python -m pip install --disable-pip-version-check --quiet pyinstaller requests
    Write-Host "Freezing agent (lean; filter fails open)..." -ForegroundColor Cyan
    python -m PyInstaller @common @lean agent\watchlog_agent.py
}

$exe = Join-Path $root "dist\watchlog-agent.exe"
if (-not (Test-Path $exe)) { throw "build produced no exe" }
$mb = [math]::Round((Get-Item $exe).Length / 1MB, 1)
Write-Host ""
Write-Host "Built $exe ($mb MB)" -ForegroundColor Green
if ($WithAI) {
    Write-Host "Verifying the AI filter is packaged (running --selftest)..." -ForegroundColor Cyan
    & $exe --selftest
    if ($LASTEXITCODE -ne 0) { throw "AI self-test failed (exit $LASTEXITCODE) — the filter is not correctly packaged" }
    Write-Host "AI self-test PASSED." -ForegroundColor Green
} else {
    Write-Host "Lean build — filter fails open. Use -WithAI for the shippable build." -ForegroundColor Yellow
}
