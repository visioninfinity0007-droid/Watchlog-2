<#
  WatchLog — Dahua recorder READ-ONLY probe.  Run ON SM-HP (the agent PC that can
  reach the recorder). Changes NOTHING on the recorder; it only reads config so we
  can plan the time + SMD fix precisely for THIS firmware.

  Usage (PowerShell on SM-HP):
    .\dahua_probe.ps1 -Recorder 192.168.100.80 -User admin -Password admin123

  If -Recorder is wrong/offline it auto-scans the local /24 for a Dahua (port 37777).
  The real recorder IP is also in C:\ProgramData\WatchLog\watchlog.ini (nvr host).
#>
param(
  [string]$Recorder = "192.168.100.80",
  [Parameter(Mandatory=$true)][string]$User,
  [Parameter(Mandatory=$true)][string]$Password
)
$ErrorActionPreference = "Continue"
$cred = New-Object System.Management.Automation.PSCredential($User,(ConvertTo-SecureString $Password -AsPlainText -Force))

function Test-Port($ip,$port,$ms=700){ $c=New-Object Net.Sockets.TcpClient; try{ if($c.ConnectAsync($ip,$port).Wait($ms)){return $true} }catch{}; finally{$c.Close()}; return $false }

if(-not (Test-Port $Recorder 80) -and -not (Test-Port $Recorder 37777)){
  Write-Host "[$Recorder] not responding on 80/37777 — scanning the local /24 for a Dahua (37777)..."
  $base = ($Recorder -replace '\.\d+$','')
  for($i=2;$i -lt 255;$i++){ $ip="$base.$i"; if(Test-Port $ip 37777 300){ Write-Host "  found Dahua at $ip"; $Recorder=$ip; break } }
}
Write-Host "=== Probing recorder: $Recorder ===`n"

function Cgi($path){
  try{
    $r = Invoke-WebRequest -Uri ("http://{0}{1}" -f $Recorder,$path) -Credential $cred -TimeoutSec 15 -UseBasicParsing
    return $r.Content.Trim()
  }catch{ return "ERR: " + $_.Exception.Message }
}

Write-Host "----- IDENTITY -----"
"deviceType     : " + (Cgi "/cgi-bin/magicBox.cgi?action=getDeviceType")
"softwareVersion: " + (Cgi "/cgi-bin/magicBox.cgi?action=getSoftwareVersion")
"serialNo       : " + (Cgi "/cgi-bin/magicBox.cgi?action=getSerialNo")

Write-Host "`n----- TIME / NTP (the wrong-time issue) -----"
"currentTime : " + (Cgi "/cgi-bin/global.cgi?action=getCurrentTime")
"Locales(TZ) :`n" + (Cgi "/cgi-bin/configManager.cgi?action=getConfig&name=Locales")
"NTP         :`n" + (Cgi "/cgi-bin/configManager.cgi?action=getConfig&name=NTP")

Write-Host "`n----- CHANNEL TITLES (to label cameras) -----"
Cgi "/cgi-bin/configManager.cgi?action=getConfig&name=ChannelTitle"

Write-Host "`n----- SMART/SMD + IVS RULES per channel (the 'vehicle in office' source) -----"
Cgi "/cgi-bin/configManager.cgi?action=getConfig&name=VideoAnalyseRule"

Write-Host "`n----- SMD dedicated config (if present) -----"
Cgi "/cgi-bin/configManager.cgi?action=getConfig&name=SmartMotionDetect"

Write-Host "`n----- MotionDetect (fallback) -----"
Cgi "/cgi-bin/configManager.cgi?action=getConfig&name=MotionDetect"

Write-Host "`n=== done — copy ALL output above and send it back ==="
