param(
    [ValidateSet("dahua","hikvision","onvif1","onvif2")]
    [string]$Device = "dahua",
    [ValidateSet("healthy","slow-login","storage-fault","archive-empty","camera-2-offline","recording-3-off")]
    [string]$Scenario = "healthy"
)

$ips = @{
    dahua = "10.77.0.20"
    hikvision = "10.77.0.21"
    onvif1 = "10.77.0.31"
    onvif2 = "10.77.0.32"
}
$ip = $ips[$Device]
$uri = "http://$($ip):9001/scenario/$Scenario"
Invoke-RestMethod -Method Post -Uri $uri -TimeoutSec 5 | ConvertTo-Json -Depth 5
