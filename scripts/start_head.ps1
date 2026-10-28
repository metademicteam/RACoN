# Start everything the head PC serves: website container, coordinator, model head + API gateway,
# and the Cloudflare tunnel. Safe to run repeatedly: anything already listening is left alone.
#
# Run manually:   .\scripts\start_head.ps1
# Install at logon: .\scripts\install_head_autostart.ps1

param(
    [int]$ReadyTimeoutSeconds = 180
)

$ErrorActionPreference = "Continue"
$root = Split-Path -Parent $PSScriptRoot
$python = Join-Path $root ".venv\Scripts\python.exe"
$logDir = Join-Path $env:LOCALAPPDATA "RACoN\logs"
New-Item -ItemType Directory -Path $logDir -Force | Out-Null

function Test-Port([int]$Port) {
    # A TCP connect is more reliable than Get-NetTCPConnection here: some listeners are owned by
    # PID 0 (port proxies) and CIM queries miss them.
    $client = New-Object System.Net.Sockets.TcpClient
    try {
        $async = $client.BeginConnect("127.0.0.1", $Port, $null, $null)
        $ok = $async.AsyncWaitHandle.WaitOne(700)
        if ($ok) { $client.EndConnect($async) }
        return $ok
    } catch {
        return $false
    } finally {
        $client.Close()
    }
}

function Wait-Http([string]$Url, [int]$Seconds) {
    $deadline = (Get-Date).AddSeconds($Seconds)
    while ((Get-Date) -lt $deadline) {
        try {
            $resp = Invoke-WebRequest -Uri $Url -UseBasicParsing -TimeoutSec 3
            if ($resp.StatusCode -eq 200) { return $true }
        } catch {
        }
        Start-Sleep -Seconds 2
    }
    return $false
}

Write-Host "=== RACoN head startup ===" -ForegroundColor Cyan

# 1. Docker website container
$dockerReady = $false
for ($i = 0; $i -lt 60; $i++) {
    & docker info --format "{{.ServerVersion}}" 2>$null | Out-Null
    if ($LASTEXITCODE -eq 0) { $dockerReady = $true; break }
    Start-Sleep -Seconds 2
}
if ($dockerReady) {
    & docker compose -f (Join-Path $root "website\docker-compose.yml") up -d 2>&1 | Out-Null
    Write-Host "[ok] Website container is running on 127.0.0.1:8081"
} else {
    Write-Warning "Docker Desktop is not running; the website will be unavailable until it starts."
}

# 2. Coordinator (worker ingress + admin API)
if (Test-Port 8765) {
    Write-Host "[ok] Coordinator already listening on 8765"
} else {
    Start-Process -FilePath $python -ArgumentList @("-m", "racon.coordinator.server") `
        -WorkingDirectory $root -WindowStyle Hidden `
        -RedirectStandardOutput (Join-Path $logDir "coordinator.log") `
        -RedirectStandardError (Join-Path $logDir "coordinator.log.err") | Out-Null
    if (Wait-Http "http://127.0.0.1:8765/health" $ReadyTimeoutSeconds) {
        Write-Host "[ok] Coordinator started"
    } else {
        Write-Warning "Coordinator did not become healthy; see $logDir\coordinator.log.err"
    }
}

# 3. Model head + public API gateway (llama-server on 8090, gateway on 8000)
if (Test-Port 8000) {
    Write-Host "[ok] API gateway already listening on 8000"
} else {
    Start-Process -FilePath $python -ArgumentList @("-m", "racon.cluster", "serve") `
        -WorkingDirectory $root -WindowStyle Hidden `
        -RedirectStandardOutput (Join-Path $logDir "cluster-serve.log") `
        -RedirectStandardError (Join-Path $logDir "cluster-serve.log.err") | Out-Null
    if (Wait-Http "http://127.0.0.1:8000/health" $ReadyTimeoutSeconds) {
        Write-Host "[ok] Model head and gateway started"
    } else {
        Write-Warning "Gateway did not become healthy; see $logDir\cluster-serve.log.err"
    }
}

# 4. Cloudflare tunnel
if (Get-Process cloudflared -ErrorAction SilentlyContinue) {
    Write-Host "[ok] Cloudflare tunnel already running"
} else {
    $cfConfig = Join-Path $env:USERPROFILE ".cloudflared\config.yml"
    if (-not (Test-Path $cfConfig)) {
        Write-Warning "Missing $cfConfig; run the one-time tunnel setup in docs/WEBSITE_CLOUDFLARE.md"
    } else {
        Start-Process -FilePath "cloudflared" -ArgumentList @("tunnel", "--config", $cfConfig, "run", "racon") `
            -WindowStyle Hidden `
            -RedirectStandardOutput (Join-Path $logDir "cloudflared.log") `
            -RedirectStandardError (Join-Path $logDir "cloudflared.log.err") | Out-Null
        if (Wait-Http "https://racon.metademic.org/health" 90) {
            Write-Host "[ok] Cloudflare tunnel connected; website is live"
        } else {
            Write-Warning "Tunnel not reachable yet; see $logDir\cloudflared.log.err"
        }
    }
}

Write-Host ""
Write-Host "Website:     https://racon.metademic.org"
Write-Host "Coordinator: https://desktop-b7l73cl.tail6cb521.ts.net"
Write-Host "API:         https://racon.metademic.org/v1"
Write-Host "Logs:        $logDir"
