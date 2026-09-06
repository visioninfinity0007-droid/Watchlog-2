# WatchLog - freeze the agent to a one-file Windows .exe.
#
#   Lean (diagnostics only; AI/analytics measurement pauses):
#     powershell -ExecutionPolicy Bypass -File agent\build_exe.ps1
#
#   Production build (bundles onnxruntime + numpy + PIL + tzdata + model):
#     powershell -ExecutionPolicy Bypass -File agent\build_exe.ps1 -WithAI
#
# The packaged entrypoint is release_agent.py. It delegates the normal runtime
# to analytics_agent.py and gives the NSIS --setup path strict finite-process
# semantics: setup failure returns non-zero; successful setup validates WatchLog
# enrollment and returns control to the installer instead of running forever.
# --selftest and every non-setup command still delegate to the existing core.

param([switch]$WithAI)

$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
Set-Location $root

$common = @(
    "--onefile","--name","watchlog-agent","--console","--clean","--noconfirm",
    "--distpath","dist","--workpath","build","--specpath","build",
    "--hidden-import","requests",
    "--hidden-import","zoneinfo",
    "--collect-all","tzdata",
    "--exclude-module","torch","--exclude-module","ultralytics",
    "--exclude-module","matplotlib","--exclude-module","tkinter",
    "--exclude-module","pandas","--exclude-module","scipy",
    "--exclude-module","pytest","--exclude-module","IPython"
)

$entry = "agent\release_agent.py"

# PE version metadata from the single version source (wl_version.py) so Windows
# and CI can read watchlog-agent.exe ProductVersion directly (no console tricks).
$agentVer = (Select-String -Path (Join-Path $root "agent\wl_version.py") -Pattern '^VERSION\s*=\s*"([^"]+)"').Matches[0].Groups[1].Value
if (-not $agentVer) { throw "could not read VERSION from wl_version.py" }
$vt = ((($agentVer -split '[.+]') + @('0','0','0'))[0..2]) -join ','
New-Item -ItemType Directory -Force -Path (Join-Path $root "build") | Out-Null
$verFile = Join-Path $root "build\watchlog-agent.version.txt"
@"
VSVersionInfo(ffi=FixedFileInfo(filevers=($vt,0), prodvers=($vt,0), mask=0x3f, flags=0x0, OS=0x40004, fileType=0x1, subtype=0x0, date=(0,0)),
  kids=[StringFileInfo([StringTable(u'040904B0', [
    StringStruct(u'CompanyName', u'Vision Infinity'),
    StringStruct(u'FileDescription', u'WatchLog Site Agent'),
    StringStruct(u'FileVersion', u'$agentVer'),
    StringStruct(u'InternalName', u'watchlog-agent'),
    StringStruct(u'OriginalFilename', u'watchlog-agent.exe'),
    StringStruct(u'ProductName', u'WatchLog'),
    StringStruct(u'ProductVersion', u'$agentVer')])]),
  VarFileInfo([VarStruct(u'Translation', [1033, 1200])])])
"@ | Set-Content -Path $verFile -Encoding UTF8
$common += @("--version-file", $verFile)

if ($WithAI) {
    $model = Join-Path $root "models\yolov8n.onnx"
    if (-not (Test-Path $model)) {
        throw "AI build needs the model at prototype\models\yolov8n.onnx. Export it once:
  yolo export model=yolov8n.pt format=onnx
then copy it there."
    }
    Write-Host "Installing production dependencies (onnxruntime, numpy, pillow, tzdata)..." -ForegroundColor Cyan
    python -m pip install --disable-pip-version-check --quiet pyinstaller requests onnxruntime numpy pillow tzdata
    $ai = @(
        "--hidden-import","numpy",
        "--hidden-import","onnxruntime","--collect-all","onnxruntime",
        "--hidden-import","PIL.Image",
        "--add-data","$model;."
    )
    Write-Host "Freezing WatchLog agent + Analytics Studio runtime..." -ForegroundColor Cyan
    python -m PyInstaller @common @ai $entry
} else {
    $lean = @("--exclude-module","onnxruntime","--exclude-module","numpy","--exclude-module","PIL")
    Write-Host "Installing lean build dependencies..." -ForegroundColor Cyan
    python -m pip install --disable-pip-version-check --quiet pyinstaller requests tzdata
    Write-Host "Freezing lean diagnostic build (analytics measurement pauses without AI)..." -ForegroundColor Cyan
    python -m PyInstaller @common @lean $entry
}

$exe = Join-Path $root "dist\watchlog-agent.exe"
if (-not (Test-Path $exe)) { throw "build produced no exe" }
$mb = [math]::Round((Get-Item $exe).Length / 1MB, 1)
Write-Host ""
Write-Host "Built $exe ($mb MB)" -ForegroundColor Green
if ($WithAI) {
    Write-Host "Verifying packaged on-site AI (running --selftest)..." -ForegroundColor Cyan
    & $exe --selftest
    if ($LASTEXITCODE -ne 0) { throw "AI self-test failed (exit $LASTEXITCODE)" }
    Write-Host "AI self-test PASSED." -ForegroundColor Green
} else {
    Write-Host "Lean build: incident filter fails open and analytics measurement pauses. Use -WithAI for release." -ForegroundColor Yellow
}
