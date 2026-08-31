<#
  Assemble a WatchLog site installer to send to a client.

    powershell -ExecutionPolicy Bypass -File tools\make_installer.ps1 -Code WL-XXXX-XXXX -SiteName "AKSS Head Office"

  If Inno Setup (ISCC.exe) is installed it produces a branded
  dist-installer\WatchLog-Setup.exe. Otherwise it falls back to a
  dist-installer\WatchLog-Setup-<site>.zip. Either way the payload is the
  same and carries NO secret (publishable key is public; the code is
  single-use and expiring).
#>
param(
  [string]$Code = "",
  [string]$SiteName = "site",
  [string]$SupabaseUrl = "https://oyvgubyxmjlijiczjona.supabase.co",
  [string]$PublishableKey = ""
)
$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
Set-Location $root

if (-not $PublishableKey) {
  $line = Select-String -Path (Join-Path $root ".env") -Pattern '^SUPABASE_PUBLISHABLE_KEY=' | Select-Object -First 1
  if ($line) { $PublishableKey = ($line.Line -replace '^SUPABASE_PUBLISHABLE_KEY=','').Trim('"',"'"," ") }
}
if (-not $PublishableKey) { throw "No publishable key given and none found in .env" }

$exe = Join-Path $root "prototype\dist\watchlog-agent.exe"
if (-not (Test-Path $exe)) { throw "Build the exe first (prototype\agent\build_exe.ps1). Not found: $exe" }
$src = Join-Path $root "prototype\installer"

$stage = Join-Path $env:TEMP ("wl-stage-" + [guid]::NewGuid().ToString("N").Substring(0,8))
New-Item -ItemType Directory -Force -Path $stage | Out-Null
Copy-Item (Join-Path $src "*") $stage -Recurse -Force
Copy-Item $exe (Join-Path $stage "watchlog-agent.exe") -Force
$model = Join-Path $root "prototype\models\yolov8n.onnx"
if (Test-Path $model) { Copy-Item $model (Join-Path $stage "yolov8n.onnx") -Force }

$defaults = @"
; WatchLog site defaults. Public values only - safe to send.
[watchlog]
supabase_url = $SupabaseUrl
supabase_publishable_key = $PublishableKey
enrollment_code = $Code
"@
Set-Content -Path (Join-Path $stage "watchlog.defaults.ini") -Value $defaults -Encoding UTF8

$outDir = Join-Path $root "dist-installer"
New-Item -ItemType Directory -Force -Path $outDir | Out-Null
$safe = ($SiteName -replace '[^A-Za-z0-9_-]','-')

# Prefer a branded Setup.exe if Inno Setup is installed.
$iscc = @(
  "${env:ProgramFiles(x86)}\Inno Setup 6\ISCC.exe",
  "$env:ProgramFiles\Inno Setup 6\ISCC.exe"
) | Where-Object { Test-Path $_ } | Select-Object -First 1

if ($iscc) {
  & $iscc "/O$outDir" "/FWatchLog-Setup-$safe" (Join-Path $stage "watchlog.iss") | Out-Null
  $out = Join-Path $outDir "WatchLog-Setup-$safe.exe"
  Write-Host "  Built branded installer: $out" -ForegroundColor Green
} else {
  $zip = Join-Path $outDir "WatchLog-Setup-$safe.zip"
  if (Test-Path $zip) { Remove-Item $zip -Force }
  # the .iss/.ico are only needed to COMPILE; drop them from the zip payload
  Get-ChildItem $stage -Include *.iss,setup.ico -Recurse | Remove-Item -Force
  Compress-Archive -Path (Join-Path $stage "*") -DestinationPath $zip -Force
  $out = $zip
  Write-Host "  Inno Setup not found - built a zip instead: $out" -ForegroundColor Yellow
  Write-Host "  (Install Inno Setup 6 to produce a branded Setup.exe.)" -ForegroundColor Gray
}
Remove-Item -LiteralPath $stage -Recurse -Force

$mb = [math]::Round((Get-Item $out).Length / 1MB, 1)
Write-Host "  Size: $mb MB" -ForegroundColor Gray
if ($Code) { Write-Host "  Enrollment code baked in: $Code" -ForegroundColor Cyan }
else { Write-Host "  No code baked in - the client is asked for it during setup." -ForegroundColor Yellow }
