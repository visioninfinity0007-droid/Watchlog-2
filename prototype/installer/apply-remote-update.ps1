<#
  Apply a WatchLog remote-update package that has already been selected from the
  locally configured signed manifest and SHA-256 verified by the running agent.

  This script is called ONLY by run-agent.ps1 between agent runs, so no
  watchlog-agent.exe process is alive while the binary is replaced.
#>
param([string]$InstallDir = "$env:ProgramFiles\WatchLog")

$ErrorActionPreference = "Stop"
$data = Join-Path $env:ProgramData "WatchLog"
$root = Join-Path $data "remote-update"
$pendingPath = Join-Path $root "pending.json"
$resultPath = Join-Path $root "result.json"
$packagePath = Join-Path $root "watchlog-agent.next.exe"
$agentPath = Join-Path $InstallDir "watchlog-agent.exe"
$backupPath = Join-Path $InstallDir "watchlog-agent.exe.remote.bak"
# The staged package is copied here (Program Files: administrators only) and verified on
# THIS copy, so nothing in ProgramData can change between verification and installation.
$verifyPath = Join-Path $InstallDir "watchlog-agent.next.verify"

# Only SYSTEM or Administrators may have staged an update that SYSTEM is about to run.
# %ProgramData% lets standard users create files in new folders, so without these checks a
# local account could plant pending.json plus its own exe and get it run as SYSTEM.
$TrustedSids = @('S-1-5-18', 'S-1-5-32-544')

function Get-OwnerSid([string]$Path) {
  return (Get-Acl -LiteralPath $Path).GetOwner([Security.Principal.SecurityIdentifier]).Value
}

function Get-UntrustedStageReason {
  $acl = Get-Acl -LiteralPath $root
  $owner = $acl.GetOwner([Security.Principal.SecurityIdentifier]).Value
  if ($TrustedSids -notcontains $owner) { return "staging folder is owned by $owner" }
  if (-not $acl.AreAccessRulesProtected) { return "staging folder inherits permissions" }
  foreach ($rule in $acl.Access) {
    if ($rule.AccessControlType -ne 'Allow') { continue }
    $sid = $rule.IdentityReference.Translate([Security.Principal.SecurityIdentifier]).Value
    if ($TrustedSids -notcontains $sid) { return "staging folder grants access to $sid" }
  }
  foreach ($file in @($pendingPath, $packagePath)) {
    if (Test-Path -LiteralPath $file) {
      $fileOwner = Get-OwnerSid $file
      if ($TrustedSids -notcontains $fileOwner) {
        return "$(Split-Path -Leaf $file) is owned by $fileOwner"
      }
    }
  }
  return $null
}

function Write-Result([string]$RequestId, [bool]$Ok, [string]$Detail, [string]$Version = "", [int]$HealthDelaySec = 0) {
  New-Item -ItemType Directory -Force -Path $root | Out-Null
  $now = [DateTimeOffset]::UtcNow
  $obj = [ordered]@{
    schema = "watchlog.remote_update_result.v1"
    request_id = $RequestId
    ok = $Ok
    detail = $Detail
    applied_version = $Version
    completed_at = $now.ToString("o")
    health_not_before = $now.AddSeconds([Math]::Max(0,$HealthDelaySec)).ToString("o")
  }
  $json = $obj | ConvertTo-Json -Compress
  $tmp = "$resultPath.tmp"
  Set-Content -LiteralPath $tmp -Value $json -Encoding UTF8
  Move-Item -LiteralPath $tmp -Destination $resultPath -Force
}

if (-not (Test-Path $pendingPath)) { exit 0 }

$untrusted = $null
try { $untrusted = Get-UntrustedStageReason } catch { $untrusted = "staging folder permissions unreadable: $($_.Exception.Message)" }
if ($untrusted) {
  # Never read, never run: discard the stage. The running Agent re-stages a genuine update
  # into a protected folder at its next poll.
  Remove-Item -LiteralPath $pendingPath -Force -ErrorAction SilentlyContinue
  Remove-Item -LiteralPath $packagePath -Force -ErrorAction SilentlyContinue
  Write-Result "" $false ("remote update refused: " + $untrusted) ""
  exit 3
}

$requestId = ""
$target = ""
try {
  $pending = Get-Content -LiteralPath $pendingPath -Raw | ConvertFrom-Json
  $requestId = [string]$pending.request_id
  $target = [string]$pending.target_version
  $expectedHash = ([string]$pending.sha256).ToUpperInvariant()
  if (-not $requestId -or -not $target -or $expectedHash -notmatch '^[0-9A-F]{64}$') {
    throw "pending update metadata is invalid"
  }
  if (-not (Test-Path $packagePath)) { throw "staged update package is missing" }

  Remove-Item -LiteralPath $verifyPath -Force -ErrorAction SilentlyContinue
  Copy-Item -LiteralPath $packagePath -Destination $verifyPath -Force
  $gotHash = (Get-FileHash -LiteralPath $verifyPath -Algorithm SHA256).Hash.ToUpperInvariant()
  if ($gotHash -ne $expectedHash) { throw "staged package SHA-256 does not match" }

  $packageVersion = ([string](Get-Item -LiteralPath $verifyPath).VersionInfo.ProductVersion).Trim()
  if ($packageVersion -ne $target) {
    throw "staged package ProductVersion '$packageVersion' does not match '$target'"
  }

  # Idempotent recovery: if a previous pass already placed the target binary,
  # record success rather than touching it again.
  if (Test-Path $agentPath) {
    $installedVersion = ([string](Get-Item -LiteralPath $agentPath).VersionInfo.ProductVersion).Trim()
    $installedHash = (Get-FileHash -LiteralPath $agentPath -Algorithm SHA256).Hash.ToUpperInvariant()
    if ($installedVersion -eq $target -and $installedHash -eq $expectedHash) {
      Write-Result $requestId $true "update already applied and verified; awaiting runtime health confirmation" $target 60
      Remove-Item -LiteralPath $pendingPath -Force -ErrorAction SilentlyContinue
      Remove-Item -LiteralPath $packagePath -Force -ErrorAction SilentlyContinue
      exit 0
    }
  }

  if (-not (Test-Path $agentPath)) { throw "current WatchLog agent binary is missing" }
  # Every request gets a fresh rollback image of the version actually running now.
  # Never reuse a stale backup left by an older failed attempt.
  Remove-Item -LiteralPath $backupPath -Force -ErrorAction SilentlyContinue
  Copy-Item -LiteralPath $agentPath -Destination $backupPath -Force

  Copy-Item -LiteralPath $verifyPath -Destination $agentPath -Force

  $installedVersion = ([string](Get-Item -LiteralPath $agentPath).VersionInfo.ProductVersion).Trim()
  $installedHash = (Get-FileHash -LiteralPath $agentPath -Algorithm SHA256).Hash.ToUpperInvariant()
  if ($installedVersion -ne $target -or $installedHash -ne $expectedHash) {
    throw "installed binary failed version/hash verification"
  }

  $runtimeVersion = ""
  try {
    $runtimeVersion = (& $agentPath --version 2>$null | Select-Object -First 1).ToString().Trim()
  } catch { }
  if ($runtimeVersion -ne $target) {
    throw "installed runtime --version '$runtimeVersion' does not match '$target'"
  }

  Write-Result $requestId $true "signed remote update applied; awaiting runtime health confirmation" $target 60
  Remove-Item -LiteralPath $pendingPath -Force -ErrorAction SilentlyContinue
  Remove-Item -LiteralPath $packagePath -Force -ErrorAction SilentlyContinue
  Remove-Item -LiteralPath $verifyPath -Force -ErrorAction SilentlyContinue
  exit 0
}
catch {
  $detail = $_.Exception.Message
  try {
    if (Test-Path $backupPath) {
      Copy-Item -LiteralPath $backupPath -Destination $agentPath -Force
    }
  } catch {
    $detail = "$detail; rollback failed: $($_.Exception.Message)"
  }
  if ($requestId) {
    Write-Result $requestId $false ("remote update rolled back: " + $detail) ""
  }
  Remove-Item -LiteralPath $pendingPath -Force -ErrorAction SilentlyContinue
  Remove-Item -LiteralPath $packagePath -Force -ErrorAction SilentlyContinue
  Remove-Item -LiteralPath $verifyPath -Force -ErrorAction SilentlyContinue
  exit 2
}
