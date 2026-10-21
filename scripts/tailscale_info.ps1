# Tailscale Status & Remote Access Helper for RACoN
# This script detects your Tailscale IP and outputs the exact access URLs for other devices.

$ErrorActionPreference = "Continue"

Write-Host "========================================" -ForegroundColor Cyan
Write-Host "  RACoN Tailscale Network Inspector" -ForegroundColor Cyan
Write-Host "========================================" -ForegroundColor Cyan
Write-Host ""

$tailscaleBin = "C:\Program Files\Tailscale\tailscale.exe"
if (-not (Test-Path $tailscaleBin)) {
    $tailscaleBin = (Get-Command tailscale -ErrorAction SilentlyContinue | Select-Object -ExpandProperty Source)
}

if (-not $tailscaleBin) {
    Write-Host "[WARN] Tailscale binary not found. Please install Tailscale from https://tailscale.com" -ForegroundColor Yellow
    exit 1
}

try {
    $tsIp = & $tailscaleBin ip -4 2>&1
    $tsStatus = & $tailscaleBin status 2>&1 | Select-Object -First 1
} catch {
    Write-Host "[ERROR] Could not query Tailscale: $_" -ForegroundColor Red
    exit 1
}

Write-Host "Tailscale Device Info:" -ForegroundColor Green
Write-Host "  IP Address: $tsIp" -ForegroundColor Yellow
Write-Host "  Status:     $tsStatus" -ForegroundColor Yellow
Write-Host ""
Write-Host "URLs for Other Users on your Tailnet:" -ForegroundColor Cyan
Write-Host "  Web Dashboard:   http://${tsIp}:1420" -ForegroundColor White
Write-Host "  Coordinator API: http://${tsIp}:8765" -ForegroundColor White
Write-Host "  API Healthcheck: http://${tsIp}:8765/health" -ForegroundColor White
Write-Host "  API Docs (OpenAPI): http://${tsIp}:8765/docs" -ForegroundColor White
Write-Host ""
Write-Host "Authentication Token for Remote Users:" -ForegroundColor Cyan
Write-Host "  Header: X-RACON-TOKEN: racon-local-dev-token" -ForegroundColor Green
Write-Host ""
Write-Host "Optional: To share with users outside your Tailnet using Tailscale Funnel (Public HTTPS):" -ForegroundColor Gray
Write-Host "  & '$tailscaleBin' funnel 8765 on" -ForegroundColor Gray
Write-Host "  & '$tailscaleBin' funnel 1420 on" -ForegroundColor Gray
Write-Host ""
