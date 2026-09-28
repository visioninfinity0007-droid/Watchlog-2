param(
    [string]$Username = "admin",
    [string]$Password = "WatchLog123!"
)

$ErrorActionPreference = "Stop"
$devices = @(
    @{Name="Lab router"; IP="10.77.0.50"; Port=80},
    @{Name="Dahua NVR"; IP="10.77.0.20"; Port=37777},
    @{Name="Hikvision NVR"; IP="10.77.0.21"; Port=8000},
    @{Name="ONVIF camera 1"; IP="10.77.0.31"; Port=80},
    @{Name="ONVIF camera 2"; IP="10.77.0.32"; Port=80}
)

foreach ($d in $devices) {
    $ok = Test-NetConnection -ComputerName $d.IP -Port $d.Port -InformationLevel Quiet -WarningAction SilentlyContinue
    if (-not $ok) { throw "$($d.Name) is not reachable at $($d.IP):$($d.Port)" }
    Write-Host "PASS  $($d.Name)  $($d.IP):$($d.Port)"
}

$curl = (Get-Command curl.exe -ErrorAction Stop).Source
$dahua = & $curl -sS --fail --digest -u "$($Username):$($Password)" "http://10.77.0.20/cgi-bin/magicBox.cgi?action=getSystemInfo"
if ($LASTEXITCODE -ne 0 -or $dahua -notmatch "DH-XVR1B08-I") { throw "Dahua Digest login failed" }
Write-Host "PASS  Dahua Digest login + identity"

$hik = & $curl -sS --fail --digest -u "$($Username):$($Password)" "http://10.77.0.21/ISAPI/System/deviceInfo"
if ($LASTEXITCODE -ne 0 -or $hik -notmatch "DS-7608NI-Q1") { throw "Hikvision Digest login failed" }
Write-Host "PASS  Hikvision Digest login + identity"

$wrong = & $curl -sS --digest -u "$($Username):definitely-wrong" -o NUL -w "%{http_code}" "http://10.77.0.20/cgi-bin/magicBox.cgi?action=getSystemInfo"
if ($wrong -ne "401") { throw "Wrong-password contract failed; expected final HTTP 401, got $wrong" }
Write-Host "PASS  Wrong credentials are rejected"

Write-Host ""
Write-Host "WatchLog device lab is ready for installer testing."
