# Install and start the racon.metademic.org Cloudflare tunnel at user sign-in.
# Runs without administrator rights (per-user scheduled task).
#
# Usage:
#   .\scripts\install_cloudflare_tunnel.ps1
#   .\scripts\install_cloudflare_tunnel.ps1 -TunnelName racon

param(
    [string]$TunnelName = "racon",
    [string]$TaskName = "RACoN Cloudflare Tunnel"
)

$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
$sourceConfig = Join-Path $root "website\cloudflared\config.yml"
$cloudflaredDir = Join-Path $env:USERPROFILE ".cloudflared"

$exe = (Get-Command cloudflared -ErrorAction SilentlyContinue).Source
if (-not $exe) {
    Write-Host "cloudflared is not installed or not on PATH." -ForegroundColor Red
    exit 1
}
if (-not (Test-Path $sourceConfig)) {
    Write-Host "Tunnel config not found: $sourceConfig" -ForegroundColor Red
    exit 1
}

New-Item -ItemType Directory -Path $cloudflaredDir -Force | Out-Null
Copy-Item $sourceConfig (Join-Path $cloudflaredDir "config.yml") -Force

$action = New-ScheduledTaskAction -Execute $exe -Argument "tunnel run $TunnelName"
$trigger = New-ScheduledTaskTrigger -AtLogOn -User $env:USERNAME
$settings = New-ScheduledTaskSettingsSet `
    -StartWhenAvailable `
    -RestartCount 3 `
    -RestartInterval (New-TimeSpan -Minutes 1) `
    -ExecutionTimeLimit ([TimeSpan]::Zero)
Register-ScheduledTask `
    -TaskName $TaskName `
    -Action $action `
    -Trigger $trigger `
    -Settings $settings `
    -Description "Runs the $TunnelName Cloudflare tunnel at sign-in" `
    -Force | Out-Null

Start-ScheduledTask -TaskName $TaskName
Start-Sleep -Seconds 5

Get-ScheduledTask -TaskName $TaskName | Format-Table TaskName, State
cloudflared tunnel info $TunnelName
Write-Host "Tunnel task installed. Public URL: https://racon.metademic.org" -ForegroundColor Green
