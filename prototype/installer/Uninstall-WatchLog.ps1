# Removes the WatchLog agent, its startup task, and its files.
$ErrorActionPreference = "SilentlyContinue"
$admin = ([Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
if (-not $admin) { Write-Host "Run as Administrator."; Read-Host "Enter to close"; exit 1 }
schtasks /End /TN "WatchLog Agent" 2>$null
schtasks /Delete /TN "WatchLog Agent" /F 2>$null
Start-Sleep 2
Remove-Item -Recurse -Force (Join-Path $env:ProgramFiles "WatchLog") 2>$null
Write-Host "WatchLog removed. (Its local data in $env:ProgramData\WatchLog was kept; delete it manually if you want.)"
Read-Host "Enter to close"
