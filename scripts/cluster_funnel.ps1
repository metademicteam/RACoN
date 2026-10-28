# RACoN cluster public API via Tailscale Funnel. Publishes ONLY the gateway port.
#
# Usage:
#   .\scripts\cluster_funnel.ps1 -Mode status   # show current funnel config
#   .\scripts\cluster_funnel.ps1 -Mode on       # publish gateway (default port 8000)
#   .\scripts\cluster_funnel.ps1 -Mode off      # stop publishing and clear serve config

param(
    [ValidateSet("status", "on", "off")]
    [string]$Mode = "status",
    [int]$GatewayPort = 8000
)

$ErrorActionPreference = "Stop"

if (-not (Get-Command tailscale -ErrorAction SilentlyContinue)) {
    Write-Host "ERROR: Tailscale CLI not found in PATH." -ForegroundColor Red
    exit 1
}

switch ($Mode) {
    "status" {
        tailscale funnel status
    }
    "on" {
        Write-Host "Publishing gateway http://127.0.0.1:$GatewayPort via Funnel (HTTPS 443)..." -ForegroundColor Yellow
        tailscale funnel --bg $GatewayPort
        tailscale funnel status
    }
    "off" {
        tailscale funnel reset
        Write-Host "Funnel disabled." -ForegroundColor Green
    }
}
