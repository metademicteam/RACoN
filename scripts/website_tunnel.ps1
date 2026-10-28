# Run the public RACoN website and API through a Cloudflare Tunnel on racon.metademic.org.
#
# Routes (see website\cloudflared\config.yml):
#   racon.metademic.org/v1/*   -> gateway 127.0.0.1:8000 (API keys, rate limits)
#   racon.metademic.org/health -> gateway 127.0.0.1:8000
#   racon.metademic.org/*      -> website container 127.0.0.1:8081
#
# One-time setup (see docs/WEBSITE_CLOUDFLARE.md):
#   cloudflared tunnel login
#   cloudflared tunnel create racon
#   cloudflared tunnel route dns racon racon.metademic.org
#   copy website\cloudflared\config.yml to %USERPROFILE%\.cloudflared\config.yml and set the tunnel id
#
# Usage:
#   .\scripts\website_tunnel.ps1 -Mode start   # website container + tunnel
#   .\scripts\website_tunnel.ps1 -Mode stop    # stop tunnel and website container
#   .\scripts\website_tunnel.ps1 -Mode status

param(
    [ValidateSet("start", "stop", "status")]
    [string]$Mode = "status",
    [string]$TunnelName = "racon"
)

$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
$compose = Join-Path $root "website\docker-compose.yml"
$cfConfig = Join-Path $env:USERPROFILE ".cloudflared\config.yml"

if (-not (Get-Command cloudflared -ErrorAction SilentlyContinue)) {
    Write-Host "cloudflared not found in PATH." -ForegroundColor Red
    exit 1
}

switch ($Mode) {
    "start" {
        if (-not (Test-Path $cfConfig)) {
            Write-Host "Missing $cfConfig. Follow docs/WEBSITE_CLOUDFLARE.md first." -ForegroundColor Red
            exit 1
        }
        docker compose -f $compose up -d
        $logDir = Join-Path $env:LOCALAPPDATA "RACoN\logs"
        New-Item -ItemType Directory -Path $logDir -Force | Out-Null
        $log = Join-Path $logDir "cloudflared.log"
        Start-Process -FilePath "cloudflared" -ArgumentList @("tunnel", "--config", $cfConfig, "run", $TunnelName) `
            -WindowStyle Hidden -RedirectStandardOutput $log -RedirectStandardError "$log.err" -PassThru |
            ForEach-Object { Set-Content -Path (Join-Path $logDir "cloudflared.pid") -Value $_.Id }
        Write-Host "Tunnel started. Log: $log" -ForegroundColor Green
    }
    "stop" {
        $pidFile = Join-Path $env:LOCALAPPDATA "RACoN\logs\cloudflared.pid"
        if (Test-Path $pidFile) {
            $procId = Get-Content $pidFile
            Stop-Process -Id $procId -Force -ErrorAction SilentlyContinue
            Remove-Item $pidFile -Force
        }
        docker compose -f $compose stop
        Write-Host "Tunnel and website stopped." -ForegroundColor Green
    }
    "status" {
        cloudflared tunnel info $TunnelName
        docker compose -f $compose ps
    }
}
