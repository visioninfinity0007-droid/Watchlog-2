# WatchLog - freeze the agent to a one-file Windows .exe.
#
#   Lean (diagnostics only; AI/analytics measurement pauses):
#     powershell -ExecutionPolicy Bypass -File agent\build_exe.ps1
#
#   Production build (bundles onnxruntime + numpy + PIL + tzdata + model):
#     powershell -ExecutionPolicy Bypass -File agent\build_exe.ps1 -WithAI
#
#   Developer build with an already-prepared interpreter (the exact-set lock check still runs):
#     ...\build_exe.ps1 -WithAI -Python <venv python.exe>
#
# The packaged entrypoint is release_agent.py. It delegates the normal runtime
# to analytics_agent.py and refuses the retired console setup wizard (--setup, or
# no recorder configured): it exits 2 without prompting or writing anything, because
# WatchLog Setup (watchlog-setup-ui.exe) configures the site.
# --selftest and every non-setup command still delegate to the existing core.
#
# Reproducibility (docs/release/WINDOWS_PACKAGING.md): the Agent is frozen in its OWN venv,
# created here from prototype/packaging/requirements-agent.lock (hash-locked, --no-deps, exact
# set verified). It never shares an environment with the Setup UI.

param(
  [switch]$WithAI,
  [string]$Python = "",
  [switch]$AllowPythonMismatch
)

$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
Set-Location $root
. (Join-Path $root "packaging\build_env.ps1")

Write-Host "Preparing the isolated Agent build environment (hash-locked)..." -ForegroundColor Cyan
$py = New-WatchLogBuildVenv -Name "agent" -Python $Python -AllowPythonMismatch:$AllowPythonMismatch

$common = @(
    "--onefile","--name","watchlog-agent","--console","--clean","--noconfirm",
    "--distpath","dist","--workpath","build","--specpath","build",
    "--hidden-import","requests",
    "--hidden-import","psutil",
    "--hidden-import","zoneinfo",
    "--hidden-import","cryptography.hazmat.primitives.asymmetric.ed25519",
    "--collect-all","tzdata",
    "--collect-all","cryptography",
    "--exclude-module","torch","--exclude-module","ultralytics",
    "--exclude-module","matplotlib","--exclude-module","tkinter",
    "--exclude-module","pandas","--exclude-module","scipy",
    "--exclude-module","pytest","--exclude-module","IPython",
    # setuptools is only the venv's installer; urllib3's optional `backports.zstd` import made
    # PyInstaller bundle it (~131 modules). Nothing at runtime uses it (supply-chain audit).
    "--exclude-module","setuptools","--exclude-module","pkg_resources",
    "--exclude-module","_distutils_hack"
)

$entry = "agent\release_agent.py"

# PE version metadata from the single version source (wl_version.py) so Windows
# and CI can read watchlog-agent.exe ProductVersion directly (no console tricks).
$agentVer = Get-WatchLogVersion -AgentDir (Join-Path $root "agent")

# Stamp the exact build identity (BUILD_SHA) into build_info.py, bundled into the exe, so the
# SHIPPED Agent reports its source commit on a customer machine (--version line 2, --selftest,
# support bundle). build_windows_release.ps1 fails the release if it differs from the source SHA.
$null = Write-WatchLogBuildInfo -AgentDir (Join-Path $root "agent")
$common += @("--hidden-import", "build_info")
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
    $ai = @(
        "--hidden-import","numpy",
        "--hidden-import","onnxruntime","--collect-all","onnxruntime",
        "--hidden-import","PIL.Image",
        "--hidden-import","imageio_ffmpeg","--collect-all","imageio_ffmpeg",
        "--add-data","$model;."
    )
    Write-Host "Freezing WatchLog agent + Analytics Studio runtime..." -ForegroundColor Cyan
    & $py -m PyInstaller @common @ai $entry
    if ($LASTEXITCODE -ne 0) { throw "Agent PyInstaller build failed (exit $LASTEXITCODE)" }
} else {
    $lean = @("--exclude-module","onnxruntime","--exclude-module","numpy","--exclude-module","PIL")
    Write-Host "Freezing lean diagnostic build (analytics measurement pauses without AI)..." -ForegroundColor Cyan
    & $py -m PyInstaller @common @lean $entry
    if ($LASTEXITCODE -ne 0) { throw "Agent PyInstaller build failed (exit $LASTEXITCODE)" }
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
