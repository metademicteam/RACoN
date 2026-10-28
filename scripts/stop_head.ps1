# Stop everything start_head.ps1 starts: tunnel, model head + gateway, coordinator, website container.

$ErrorActionPreference = "Continue"
$root = Split-Path -Parent $PSScriptRoot

Write-Host "=== RACoN head shutdown ===" -ForegroundColor Cyan

Get-Process cloudflared -ErrorAction SilentlyContinue | ForEach-Object {
    Stop-Process -Id $_.Id -Force
    Write-Host "[ok] Stopped Cloudflare tunnel"
}

Get-CimInstance Win32_Process -Filter "Name='python.exe'" |
    Where-Object { $_.CommandLine -like '*racon.coordinator.server*' -or $_.CommandLine -like '*racon.cluster serve*' } |
    ForEach-Object {
        Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue
    }
Write-Host "[ok] Stopped coordinator and gateway"

Get-Process llama-server -ErrorAction SilentlyContinue | ForEach-Object {
    Stop-Process -Id $_.Id -Force -ErrorAction SilentlyContinue
}
Write-Host "[ok] Stopped llama-server"

& docker compose -f (Join-Path $root "website\docker-compose.yml") stop 2>&1 | Out-Null
Write-Host "[ok] Website container stopped"

Write-Host "Done. Start again with .\scripts\start_head.ps1"
