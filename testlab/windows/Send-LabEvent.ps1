param(
    [ValidateSet("dahua","hikvision")]
    [string]$Device = "dahua",
    [ValidateSet("motion","person","vehicle","video-loss","tamper")]
    [string]$Event = "motion",
    [ValidateRange(1,64)]
    [int]$Channel = 1
)

$ip = if ($Device -eq "dahua") { "10.77.0.20" } else { "10.77.0.21" }
$uri = "http://$($ip):9001/event/$($Event)?channel=$Channel"
Invoke-RestMethod -Method Post -Uri $uri -TimeoutSec 5 | ConvertTo-Json -Depth 5
