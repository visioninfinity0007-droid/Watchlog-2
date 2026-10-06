# WatchLog - isolated, hash-locked build environments for the Windows EXEs.
#
# Dot-source from a build script:   . (Join-Path $root "packaging\build_env.ps1")
#
# New-WatchLogBuildVenv creates a FRESH venv for one EXE (agent or setup-ui), installs ONLY the
# committed lock with pip --require-hashes --no-deps --only-binary=:all:, runs pip check, then
# proves the installed set equals the lock exactly (no missing, extra or different package) and
# records the environment (Python, pip, every installed distribution) for the release manifest.
# The Agent and the Setup UI never share an environment, so an Agent-only package (onnxruntime,
# numpy, Pillow, imageio-ffmpeg, cryptography) cannot be collected into the Setup UI.
#
# Every native command's output goes to Out-Host: the function returns ONLY the venv python path.
#
# The base interpreter must be CPython 3.12.10 (the release toolchain) unless
# -AllowPythonMismatch is passed (developer builds only; a cp312 wheel then fails to install
# anyway, so the lock still decides). -Python <venv python.exe> reuses an already-prepared
# interpreter instead of creating one (developer use); the exact-set check still runs.

$script:WatchLogReleasePython = "3.12.10"

function New-WatchLogBuildVenv {
  param(
    [Parameter(Mandatory)] [ValidateSet("agent", "setup-ui")] [string]$Name,
    [string]$Python = "",
    [switch]$AllowPythonMismatch
  )
  $ErrorActionPreference = "Stop"
  $proto = Split-Path -Parent $PSScriptRoot
  $repo = Split-Path -Parent $proto
  $lock = Join-Path $PSScriptRoot "requirements-$Name.lock"
  if (-not (Test-Path $lock)) { throw "dependency lock not found: $lock" }
  $tool = Join-Path $repo "tools\lock_build_deps.py"

  if ($Python) {
    if (-not (Test-Path $Python)) { throw "-Python interpreter not found: $Python" }
    $venvPy = (Resolve-Path $Python).Path
    Write-Host "  Using prepared interpreter for $Name (developer build): $venvPy" -ForegroundColor Yellow
  } else {
    $base = $env:WATCHLOG_BUILD_PYTHON
    if (-not $base) { $base = (Get-Command python -ErrorAction Stop).Source }
    $baseVer = (& $base -c "import platform; print(platform.python_version())").Trim()
    if ($baseVer -ne $script:WatchLogReleasePython) {
      if (-not $AllowPythonMismatch) {
        throw "release builds use CPython $script:WatchLogReleasePython; '$base' is $baseVer (pass -AllowPythonMismatch for a developer build)"
      }
      Write-Host "  WARNING: building with Python $baseVer, not $script:WatchLogReleasePython (developer build)" -ForegroundColor Yellow
    }
    $venv = Join-Path $proto "build-venvs\$Name"
    if (Test-Path $venv) { Remove-Item -Recurse -Force $venv }
    Write-Host "  Creating isolated $Name build venv ($baseVer) at $venv" -ForegroundColor Gray
    & $base -m venv $venv | Out-Host
    if ($LASTEXITCODE -ne 0) { throw "venv creation failed for $Name (exit $LASTEXITCODE)" }
    $venvPy = Join-Path $venv "Scripts\python.exe"
    $ok = $false
    foreach ($attempt in 1..3) {
      & $venvPy -m pip install --disable-pip-version-check --no-input --quiet `
          --require-hashes --no-deps --only-binary=:all: -r $lock | Out-Host
      if ($LASTEXITCODE -eq 0) { $ok = $true; break }
      Write-Host "  pip install from the $Name lock failed (attempt $attempt of 3); retrying in 8 s" -ForegroundColor Yellow
      Start-Sleep -Seconds 8
    }
    if (-not $ok) { throw "installing the $Name build lock failed; nothing outside the lock is ever installed" }
  }

  & $venvPy -m pip check | Out-Host
  if ($LASTEXITCODE -ne 0) { throw "pip check failed in the $Name build venv: the lock is not self-consistent" }
  $record = Join-Path $proto "dist\build-env-$Name.json"
  New-Item -ItemType Directory -Force -Path (Split-Path -Parent $record) | Out-Null
  & $venvPy $tool --verify-installed $lock --record $record --env-name $Name | Out-Host
  if ($LASTEXITCODE -ne 0) { throw "the $Name build venv does not match its lock exactly (see above)" }
  return $venvPy
}

function Write-WatchLogBuildInfo {
  # Stamp the exact build identity into a bundled module so the SHIPPED exe reports its source
  # commit on a customer machine that has no build environment. Both EXEs bundle this file.
  param([Parameter(Mandatory)] [string]$AgentDir)
  $proto = Split-Path -Parent $AgentDir
  $buildSha = $env:WATCHLOG_BUILD_SHA
  if (-not $buildSha) { $buildSha = $env:GITHUB_SHA }
  if (-not $buildSha) { try { $buildSha = (git -C $proto rev-parse HEAD 2>$null) } catch { $buildSha = "" } }
  $buildChannel = $env:WATCHLOG_BUILD_CHANNEL
  if (-not $buildChannel) { $buildChannel = "production" }
  $buildSha = "$buildSha".Trim()
  $buildInfo = Join-Path $AgentDir "build_info.py"
  @"
# GENERATED at build time by build_exe.ps1 / build_setup_gui.ps1 - do not edit, do not commit (gitignored).
BUILD_SHA = "$buildSha"
BUILD_CHANNEL = "$("$buildChannel".Trim())"
"@ | Set-Content -Path $buildInfo -Encoding UTF8
  Write-Host "  Build SHA stamped into build_info.py: $buildSha" -ForegroundColor Gray
  return $buildSha
}

function Get-WatchLogVersion {
  # The single product version source: prototype/agent/wl_version.py VERSION.
  param([Parameter(Mandatory)] [string]$AgentDir)
  $m = Select-String -Path (Join-Path $AgentDir "wl_version.py") -Pattern '^VERSION\s*=\s*"([^"]+)"'
  if (-not $m) { throw "could not read VERSION from wl_version.py" }
  return $m.Matches[0].Groups[1].Value
}
