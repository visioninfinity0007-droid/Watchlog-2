<#
  Assemble a WatchLog site installer to send to a client.

    powershell -ExecutionPolicy Bypass -File tools\make_installer.ps1 -Code WL-XXXX-XXXX -SiteName "AKSS Head Office"

  -Code      one-time enrollment code from the portal for this site.
             Optional - leave it out and the client types it during setup.
  -SiteName  used to name the output zip.

  Sources the installer scripts from prototype\installer\ (tracked in git)
  and the freshly built exe from prototype\dist\, and produces
  dist-installer\WatchLog-Setup-<site>.zip.

  The zip carries NO secret: the publishable key is public by design, and
  the enrollment code is single-use and expiring.
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

# Assemble a clean staging folder
$stage = Join-Path $env:TEMP ("wl-stage-" + [guid]::NewGuid().ToString("N").Substring(0,8))
New-Item -ItemType Directory -Force -Path $stage | Out-Null
Copy-Item (Join-Path $src "*") $stage -Recurse -Force
Copy-Item $exe (Join-Path $stage "watchlog-agent.exe") -Force

# Optional false-alarm model, if one has been placed in prototype\models\
$model = Join-Path $root "prototype\models\yolov8n.onnx"
if (Test-Path $model) { Copy-Item $model (Join-Path $stage "yolov8n.onnx") -Force }

# The defaults the installer seeds - public values only.
$defaults = @"
; WatchLog site defaults. Public values only - safe to send.
; The setup wizard reads these, then adds the recorder details it finds.
[watchlog]
supabase_url = $SupabaseUrl
supabase_publishable_key = $PublishableKey
enrollment_code = $Code
"@
Set-Content -Path (Join-Path $stage "watchlog.defaults.ini") -Value $defaults -Encoding UTF8

$outDir = Join-Path $root "dist-installer"
New-Item -ItemType Directory -Force -Path $outDir | Out-Null
$safe = ($SiteName -replace '[^A-Za-z0-9_-]','-')
$zip = Join-Path $outDir "WatchLog-Setup-$safe.zip"
if (Test-Path $zip) { Remove-Item $zip -Force }
Compress-Archive -Path (Join-Path $stage "*") -DestinationPath $zip -Force
Remove-Item -LiteralPath $stage -Recurse -Force

$mb = [math]::Round((Get-Item $zip).Length / 1MB, 1)
Write-Host ""
Write-Host "  Built: $zip ($mb MB)" -ForegroundColor Green
if ($Code) { Write-Host "  Enrollment code baked in: $Code" -ForegroundColor Cyan }
else { Write-Host "  No code baked in - the client is asked for it during setup." -ForegroundColor Yellow }
Write-Host "  Send the whole .zip. The client unzips and runs 'Install WatchLog.cmd' as admin." -ForegroundColor Gray
