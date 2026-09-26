param(
    [string]$VMName = "WatchLog-Lab",
    [string]$SwitchName = "WatchLogLab",
    [string]$HostAddress = "10.77.0.1",
    [int]$PrefixLength = 24
)

$ErrorActionPreference = "Stop"
if (-not (Get-Command Get-VMSwitch -ErrorAction SilentlyContinue)) {
    throw "Hyper-V PowerShell tools are not installed. Enable Hyper-V first."
}

$switch = Get-VMSwitch -Name $SwitchName -ErrorAction SilentlyContinue
if (-not $switch) {
    Write-Host "Creating isolated Hyper-V switch '$SwitchName'..."
    $switch = New-VMSwitch -Name $SwitchName -SwitchType Internal
}

$alias = "vEthernet ($SwitchName)"
$existing = Get-NetIPAddress -InterfaceAlias $alias -AddressFamily IPv4 -ErrorAction SilentlyContinue |
    Where-Object { $_.IPAddress -eq $HostAddress }
if (-not $existing) {
    Write-Host "Assigning Windows lab address $HostAddress/$PrefixLength..."
    Get-NetIPAddress -InterfaceAlias $alias -AddressFamily IPv4 -ErrorAction SilentlyContinue |
        Where-Object { $_.PrefixOrigin -ne "WellKnown" } |
        Remove-NetIPAddress -Confirm:$false -ErrorAction SilentlyContinue
    New-NetIPAddress -InterfaceAlias $alias -IPAddress $HostAddress -PrefixLength $PrefixLength | Out-Null
}

$vm = Get-VM -Name $VMName -ErrorAction SilentlyContinue
if (-not $vm) {
    Write-Warning "Hyper-V network is ready, but VM '$VMName' does not exist yet."
    Write-Host "Create an Ubuntu VM with one normal Internet NIC and then rerun this script."
    exit 0
}

$adapter = Get-VMNetworkAdapter -VMName $VMName |
    Where-Object { $_.SwitchName -eq $SwitchName } |
    Select-Object -First 1
if (-not $adapter) {
    Write-Host "Adding isolated lab NIC to $VMName..."
    Add-VMNetworkAdapter -VMName $VMName -Name "WatchLog Lab NIC" -SwitchName $SwitchName
}

Set-VMNetworkAdapter -VMName $VMName -Name "WatchLog Lab NIC" -MacAddressSpoofing On

Write-Host ""
Write-Host "WatchLog isolated network ready."
Write-Host "Windows host: $HostAddress/$PrefixLength"
Write-Host "VM: $VMName (MAC spoofing enabled on WatchLog Lab NIC)"
Write-Host "Next: inside Ubuntu, assign 10.77.0.2/24 to the lab NIC and run testlab/linux/start-lab.sh"
