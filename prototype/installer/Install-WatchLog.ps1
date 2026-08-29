<#
  WatchLog — site installer.

  Run by the client, once, on the Windows PC at their site. It:
    1. copies the agent into  C:\Program Files\WatchLog
    2. runs the setup wizard  (finds the recorder, asks for its login and
       the one-time setup code, tests the connection, links the site)
    3. registers a task that starts the agent at every boot, as SYSTEM,
       and restarts it if it ever stops
    4. starts it now

  It needs Administrator rights (to install and to create a startup task).
  The wrapper "Install WatchLog.cmd" elevates for you; if you run this .ps1
  directly, run it from an elevated PowerShell.

  Nothing about the site's network is needed in advance — no IP, no port
  forwarding. The agent finds the recorder itself and only ever connects
  OUTWARD to WatchLog.
#>

$ErrorActionPreference = "Stop"
$here = Split-Path -Parent $MyInvocation.MyCommand.Path

function Fail($msg) { Write-Host "`n  ERROR: $msg" -ForegroundColor Red; Read-Host "`n  Press Enter to close"; exit 1 }

# --- must be admin --------------------------------------------------------
$admin = ([Security.Principal.WindowsPrincipal] `
    [Security.Principal.WindowsIdentity]::GetCurrent()
    ).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
if (-not $admin) { Fail "Please run this as Administrator (use 'Install WatchLog.cmd')." }

Write-Host ""
Write-Host "  ============================================" -ForegroundColor Cyan
Write-Host "     WatchLog  -  site setup" -ForegroundColor Cyan
Write-Host "  ============================================" -ForegroundColor Cyan
Write-Host ""

$Install = Join-Path $env:ProgramFiles "WatchLog"
$Data    = Join-Path $env:ProgramData  "WatchLog"
$Exe     = Join-Path $Install "watchlog-agent.exe"
$Ini     = Join-Path $Install "watchlog.ini"

# --- files must be beside this script ------------------------------------
$srcExe = Join-Path $here "watchlog-agent.exe"
if (-not (Test-Path $srcExe)) { Fail "watchlog-agent.exe is missing from this folder." }

Write-Host "  Installing to $Install ..." -ForegroundColor Gray
New-Item -ItemType Directory -Force -Path $Install | Out-Null
New-Item -ItemType Directory -Force -Path $Data    | Out-Null

Copy-Item $srcExe $Exe -Force
Copy-Item (Join-Path $here "run-agent.cmd") (Join-Path $Install "run-agent.cmd") -Force
# The false-alarm model is optional. If shipped, copy it; if not, the agent
# still runs and reports every event (it just does not filter yet).
$srcModel = Join-Path $here "yolov8n.onnx"
if (Test-Path $srcModel) {
    Copy-Item $srcModel (Join-Path $Install "yolov8n.onnx") -Force
    Write-Host "  False-alarm filter model found and installed." -ForegroundColor Gray
} else {
    Write-Host "  No filter model in this package - the agent will report every" -ForegroundColor DarkYellow
    Write-Host "  event until the model is added. Data still flows normally." -ForegroundColor DarkYellow
}

# --- seed the config that this package was built with ---------------------
# watchlog.defaults.ini carries the WatchLog cloud address, the PUBLIC
# publishable key, and (optionally) a pre-filled one-time code. It never
# contains a secret. The wizard reads these, then adds the recorder details
# it discovers and writes the final watchlog.ini.
$defaults = Join-Path $here "watchlog.defaults.ini"
if (Test-Path $defaults) { Copy-Item $defaults $Ini -Force }
else { Fail "watchlog.defaults.ini is missing from this folder." }

# --- run the wizard -------------------------------------------------------
Write-Host ""
Write-Host "  Now finding your recorder and linking this site." -ForegroundColor Cyan
Write-Host "  Have ready: the recorder's admin password, and the setup code" -ForegroundColor Gray
Write-Host "  we gave you (unless it is already filled in)." -ForegroundColor Gray
Write-Host ""

Push-Location $Install
try {
    & $Exe --setup
    $rc = $LASTEXITCODE
} finally { Pop-Location }

if ($rc -ne 0) { Fail "Setup did not complete. Nothing was scheduled. You can re-run this installer to try again." }
if (-not (Test-Path $Ini)) { Fail "Setup did not save a configuration. Please re-run." }

# --- register the startup task -------------------------------------------
Write-Host ""
Write-Host "  Setting WatchLog to start automatically at every boot..." -ForegroundColor Gray

$taskName = "WatchLog Agent"
schtasks /Query /TN $taskName >$null 2>&1
if ($LASTEXITCODE -eq 0) { schtasks /Delete /TN $taskName /F >$null 2>&1 }

$action    = New-ScheduledTaskAction -Execute "$Install\run-agent.cmd"
$trigger   = New-ScheduledTaskTrigger -AtStartup
$principal = New-ScheduledTaskPrincipal -UserId "SYSTEM" -LogonType ServiceAccount -RunLevel Highest
$settings  = New-ScheduledTaskSettingsSet `
                -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries `
                -StartWhenAvailable -RestartCount 999 -RestartInterval (New-TimeSpan -Minutes 1) `
                -ExecutionTimeLimit (New-TimeSpan -Seconds 0)

Register-ScheduledTask -TaskName $taskName -Action $action -Trigger $trigger `
    -Principal $principal -Settings $settings -Force | Out-Null

Start-ScheduledTask -TaskName $taskName

Start-Sleep -Seconds 3
$state = (Get-ScheduledTask -TaskName $taskName).State

Write-Host ""
Write-Host "  ============================================" -ForegroundColor Green
Write-Host "     WatchLog is installed and running." -ForegroundColor Green
Write-Host "  ============================================" -ForegroundColor Green
Write-Host ""
Write-Host "  It starts automatically every time this PC boots." -ForegroundColor Gray
Write-Host "  Task state: $state" -ForegroundColor Gray
Write-Host "  Log file:   $Data\agent.log" -ForegroundColor Gray
Write-Host ""
Write-Host "  Your site should appear in the WatchLog portal within a minute." -ForegroundColor Gray
Write-Host "  You can close this window." -ForegroundColor Gray
Write-Host ""
Read-Host "  Press Enter to finish"
