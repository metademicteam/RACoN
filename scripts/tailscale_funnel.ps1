# RACoN — Tailscale Funnel Setup & Controller
# Exposes RACoN Desktop App and Coordinator to the Public Internet securely via HTTPS.
#
# Usage:
#   .\scripts\tailscale_funnel.ps1               # Status and active URLs
#   .\scripts\tailscale_funnel.ps1 -Mode webapp  # Route public Funnel to Desktop UI (Port 1420)
#   .\scripts\tailscale_funnel.ps1 -Mode backend # Route public Funnel to Coordinator (Port 8443 / 8765)
#   .\scripts\tailscale_funnel.ps1 -Mode off     # Disable public Funnel

param(
    [ValidateSet("status", "webapp", "backend", "off")]
    [string]$Mode = "status",
    [int]$Port = 1420
)

Write-Host "==========================================" -ForegroundColor Cyan
Write-Host "  RACoN Tailscale Funnel Controller" -ForegroundColor Cyan
Write-Host "==========================================" -ForegroundColor Cyan
Write-Host ""

# Check Tailscale CLI
try {
    $tsStatus = tailscale status 2>&1
} catch {
    Write-Host "ERROR: Tailscale CLI not found or not in PATH." -ForegroundColor Red
    exit 1
}

if ($Mode -eq "status") {
    Write-Host "Active Funnel Status:" -ForegroundColor Yellow
    tailscale funnel status
    Write-Host ""
    Write-Host "Local Nodes on Tailnet:" -ForegroundColor Yellow
    tailscale status | Select-Object -First 5
    Write-Host ""
    Write-Host "Public Domain: https://desktop-b7l73cl.tail6cb521.ts.net" -ForegroundColor Green
    Write-Host ""
    Write-Host "Commands:" -ForegroundColor Cyan
    Write-Host "  To route Webapp (port 1420): .\scripts\tailscale_funnel.ps1 -Mode webapp"
    Write-Host "  To route Backend (port 8765): .\scripts\tailscale_funnel.ps1 -Mode backend"
    Write-Host "  To turn off Funnel:          .\scripts\tailscale_funnel.ps1 -Mode off"
    exit 0
}

if ($Mode -eq "webapp") {
    Write-Host "Routing Public HTTPS Funnel to RACoN Desktop UI (http://127.0.0.1:1420)..." -ForegroundColor Green
    tailscale serve --bg --set-path / http://127.0.0.1:1420
    tailscale funnel --bg 443 on
    Write-Host ""
    Write-Host "SUCCESS! Public Funnel is active at:" -ForegroundColor Green
    Write-Host "  https://desktop-b7l73cl.tail6cb521.ts.net" -ForegroundColor Cyan
    Write-Host "Anyone anywhere on the internet can now open your desktop app!" -ForegroundColor Green
    exit 0
}

if ($Mode -eq "backend") {
    Write-Host "Routing Public HTTPS Funnel to RACoN Coordinator Backend (http://127.0.0.1:8765)..." -ForegroundColor Green
    tailscale serve --bg --set-path / http://127.0.0.1:8765
    tailscale funnel --bg 443 on
    Write-Host ""
    Write-Host "SUCCESS! Coordinator API is publicly available at:" -ForegroundColor Green
    Write-Host "  https://desktop-b7l73cl.tail6cb521.ts.net" -ForegroundColor Cyan
    exit 0
}

if ($Mode -eq "off") {
    Write-Host "Disabling Tailscale Funnel..." -ForegroundColor Yellow
    tailscale funnel 443 off
    tailscale serve reset
    Write-Host "Funnel disabled. App is now restricted to private Tailnet / Localhost." -ForegroundColor Green
    exit 0
}
