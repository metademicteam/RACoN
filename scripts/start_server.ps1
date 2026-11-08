# Start the RACoN server stack on this Windows PC and print the tailnet URLs.
# Usage:  .\scripts\start_server.ps1
$ErrorActionPreference = "Stop"

$root = Split-Path -Parent $PSScriptRoot
Push-Location $root
try {
    if (-not (Test-Path ".env")) {
        Copy-Item ".env.example" ".env"
        Write-Host "Created .env from .env.example. Edit AUTH_TOKEN, RACON_CLIENT_TOKEN and POSTGRES_PASSWORD before exposing the server." -ForegroundColor Yellow
    }

    function Get-EnvValue([string]$Name, [string]$Default) {
        $line = Get-Content ".env" | Where-Object { $_ -match "^\s*$([regex]::Escape($Name))\s*=" } | Select-Object -First 1
        if (-not $line) { return $Default }
        $value = ($line -split "=", 2)[1].Trim().Trim('"').Trim("'")
        if ([string]::IsNullOrWhiteSpace($value)) { return $Default }
        return $value
    }

    $token = Get-EnvValue "AUTH_TOKEN" ""
    $clientToken = Get-EnvValue "RACON_CLIENT_TOKEN" ""
    $joinCode = Get-EnvValue "RACON_JOIN_CODE" "RCN-PUBLIC-JOIN"
    $pgPassword = Get-EnvValue "POSTGRES_PASSWORD" "racon"

    if ([string]::IsNullOrWhiteSpace($token)) {
        throw "AUTH_TOKEN is not set in .env. Add a long random administrator token first."
    }
    if ($token -eq "racon-local-dev-token" -or $token -eq "replace-with-a-long-random-admin-token") {
        Write-Host "WARNING: AUTH_TOKEN is a placeholder. Edit .env before exposing this server." -ForegroundColor Yellow
    }
    if ([string]::IsNullOrWhiteSpace($clientToken)) {
        Write-Host "WARNING: RACON_CLIENT_TOKEN is not set. Desktop and web clients will be rejected." -ForegroundColor Yellow
    }
    if ($pgPassword -eq "racon" -or $pgPassword -eq "change-this-password") {
        Write-Host "WARNING: POSTGRES_PASSWORD is the default. Edit .env before storing important data." -ForegroundColor Yellow
    }

    $ip = "127.0.0.1"
    try {
        $candidate = (& tailscale ip -4 2>$null | Select-Object -First 1)
        if ($candidate) { $ip = $candidate.Trim() }
    } catch {
        Write-Host "Tailscale CLI not found; using 127.0.0.1. Install Tailscale to reach the server from other machines." -ForegroundColor Yellow
    }

    Write-Host "Building and starting RACoN (PostgreSQL + coordinator + web UI)..." -ForegroundColor Cyan
    docker compose up -d --build
    if ($LASTEXITCODE -ne 0) { throw "docker compose failed with exit code $LASTEXITCODE" }

    Write-Host "Waiting for the coordinator health check..." -ForegroundColor Cyan
    $deadline = (Get-Date).AddSeconds(240)
    $healthy = $false
    while ((Get-Date) -lt $deadline) {
        try {
            $health = Invoke-RestMethod "http://127.0.0.1:8765/health" -TimeoutSec 3
            if ($health.status -eq "ok") { $healthy = $true; break }
        } catch { }
        Start-Sleep -Seconds 3
    }

    if (-not $healthy) {
        Write-Host "Coordinator did not become healthy. Recent logs:" -ForegroundColor Red
        docker compose logs --tail 60 racn-coordinator
        throw "Startup failed"
    }

    $encodedClientToken = [uri]::EscapeDataString($clientToken)
    Write-Host ""
    Write-Host "RACoN server is up." -ForegroundColor Green
    if ($clientToken) {
        Write-Host "  Desktop UI : http://${ip}:3000/?token=$encodedClientToken"
    } else {
        Write-Host "  Desktop UI : http://${ip}:3000/  (set RACON_CLIENT_TOKEN to connect)"
    }
    Write-Host "  Admin app  : http://${ip}:8765/admin   (token: $token)"
    Write-Host "  Worker API : http://${ip}:8765"
    Write-Host ""
    Write-Host "Join a worker on another machine:" -ForegroundColor Cyan
    Write-Host "  `$env:RACON_COORDINATOR_URL = `"http://${ip}:8765`""
    Write-Host "  `$env:RACON_JOIN_CODE = `"$joinCode`""
    Write-Host "  .\.venv\Scripts\python.exe -m racon --headless"
    Write-Host ""
    Write-Host "Logs: docker compose logs -f racn-coordinator"
}
finally {
    Pop-Location
}
