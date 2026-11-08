# Start the RACoN unified control plane on 127.0.0.1:8765.
#
# Production runs the Docker Compose stack: the ``racn-coordinator`` container publishes
# 8765 and serves /admin, /api/v1/*, /v1/* and the worker ingress. If Docker is not
# available, this script falls back to running the same FastAPI app from the repository.
#
# Never use ``racon.coordinator.server`` for this port: that older process exposes only
# the worker protocol and returns a FastAPI 404 for /admin.
#
# Usage:
#   .\scripts\start_backend.ps1              # start only if not already healthy
#   .\scripts\start_backend.ps1 -Restart     # restart a hung coordinator container
#   .\scripts\start_backend.ps1 -SourceOnly  # skip Docker, run from source

param(
    [int]$Port = 8765,
    [int]$ReadyTimeoutSeconds = 240,
    [switch]$Restart,
    [switch]$SourceOnly,
    [switch]$SkipFunnel
)

# Native tools (docker, tailscale) write progress to stderr. "Stop" would terminate the
# script on a normal "Container ... Running" line, so native failures are checked through
# $LASTEXITCODE instead.
$ErrorActionPreference = "Continue"
$root = Split-Path -Parent $PSScriptRoot
$venvPython = Join-Path $root ".venv\Scripts\python.exe"
if (Test-Path $venvPython) { $python = $venvPython } else { $python = "python" }

$logDir = Join-Path $env:LOCALAPPDATA "RACoN\logs"
New-Item -ItemType Directory -Path $logDir -Force | Out-Null
$outLog = Join-Path $logDir "backend.log"
$errLog = Join-Path $logDir "backend.log.err"

$healthUrl = "http://127.0.0.1:$Port/health"
$adminUrl = "http://127.0.0.1:$Port/admin"
$composeFile = Join-Path $root "docker-compose.yml"

function Test-Http([string]$Url) {
    try {
        $response = Invoke-WebRequest -Uri $Url -UseBasicParsing -TimeoutSec 3
        return ($response.StatusCode -eq 200)
    } catch {
        return $false
    }
}

function Test-BackendHealthy {
    return ((Test-Http $healthUrl) -and (Test-Http $adminUrl))
}

function Test-Docker {
    & docker info --format "{{.ServerVersion}}" 2>$null | Out-Null
    return ($LASTEXITCODE -eq 0)
}

function Wait-ForDocker([int]$Seconds) {
    # Docker Desktop starts from the Run key and may still be initializing when the
    # Startup-folder launcher runs. Falling back to a source server here would take 8765
    # and then collide with the container once Docker finishes booting.
    $deadline = (Get-Date).AddSeconds($Seconds)
    while ((Get-Date) -lt $deadline) {
        if (Test-Docker) { return $true }
        Start-Sleep -Seconds 3
    }
    return $false
}

function Test-Port([int]$LocalPort) {
    $client = New-Object System.Net.Sockets.TcpClient
    try {
        $async = $client.BeginConnect("127.0.0.1", $LocalPort, $null, $null)
        $ok = $async.AsyncWaitHandle.WaitOne(500)
        if ($ok) { $client.EndConnect($async) }
        return $ok
    } catch {
        return $false
    } finally {
        $client.Close()
    }
}

function Get-PortOwner([int]$LocalPort) {
    $conn = Get-NetTCPConnection -State Listen -LocalPort $LocalPort -ErrorAction SilentlyContinue |
        Select-Object -First 1
    if ($conn) { return [int]$conn.OwningProcess }

    $line = netstat -ano | Select-String -Pattern ":$LocalPort\s+.*LISTENING" | Select-Object -First 1
    if ($line) {
        $parts = ($line.ToString() -split '\s+') | Where-Object { $_ -ne "" }
        if ($parts.Count -ge 5) { return [int]$parts[-1] }
    }
    return $null
}

function Test-RaconCommandLine([int]$ProcessId) {
    $process = Get-CimInstance Win32_Process -Filter "ProcessId=$ProcessId" -ErrorAction SilentlyContinue
    if (-not $process) { return $false }
    return [bool]($process.CommandLine -match "uvicorn|backend\.racon|racon\.coordinator\.server")
}

function Ensure-Funnel {
    if ($SkipFunnel) { return }
    $tailscale = "C:\Program Files\Tailscale\tailscale.exe"
    if (-not (Test-Path $tailscale)) {
        $command = Get-Command tailscale -ErrorAction SilentlyContinue
        if (-not $command) {
            Write-Host "[warn] tailscale not found; Funnel target not verified" -ForegroundColor Yellow
            return
        }
        $tailscale = $command.Source
    }
    & $tailscale funnel --bg $Port 2>$null | Out-Null
    if ($LASTEXITCODE -eq 0) {
        Write-Host "[ok] Tailscale Funnel -> 127.0.0.1:$Port" -ForegroundColor Green
    } else {
        Write-Host "[warn] 'tailscale funnel --bg $Port' failed; check Funnel in the admin console" -ForegroundColor Yellow
    }
}

function Wait-ForBackend([int]$Seconds) {
    $deadline = (Get-Date).AddSeconds($Seconds)
    while ((Get-Date) -lt $deadline) {
        if (Test-BackendHealthy) { return $true }
        Start-Sleep -Seconds 3
    }
    return $false
}

Write-Host "=== RACoN backend on 127.0.0.1:$Port ===" -ForegroundColor Cyan

if (Test-BackendHealthy) {
    Write-Host "[ok] Unified API already healthy (admin dashboard present)" -ForegroundColor Green
    Ensure-Funnel
    exit 0
}

# The old coordinator app never serves /admin. Stop every copy so it cannot take the
# port or answer the Funnel URL with a FastAPI 404.
Get-CimInstance Win32_Process -ErrorAction SilentlyContinue |
    Where-Object { $_.CommandLine -match "racon\.coordinator\.server" } |
    ForEach-Object {
        Write-Host "[..] Stopping old coordinator PID $($_.ProcessId)"
        Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue
    }

# Preferred path: the production Docker Compose stack. Wait for Docker Desktop at logon
# instead of racing it with a source process on the same port.
if ((-not $SourceOnly) -and (Test-Path $composeFile)) {
    if (Wait-ForDocker ([Math]::Max(30, $ReadyTimeoutSeconds))) {
        Write-Host "[..] Starting the Docker Compose stack"
        Push-Location $root
        try {
            & docker compose up -d 2>$null | Out-Null
            if ($Restart) {
                # ``up -d`` leaves a running-but-hung container alone; restart the API service.
                & docker compose restart racn-coordinator 2>$null | Out-Null
            }
        } finally {
            Pop-Location
        }
        if (Wait-ForBackend $ReadyTimeoutSeconds) {
            Write-Host "[ok] Coordinator container ready: http://127.0.0.1:$Port/admin" -ForegroundColor Green
            Ensure-Funnel
            exit 0
        }
        Write-Host "[warn] Compose stack did not become healthy; falling back to the source app" -ForegroundColor Yellow
    } else {
        Write-Host "[warn] Docker is not available; starting the API from source" -ForegroundColor Yellow
    }
}

# Source fallback (also used on developer machines without Docker).
if ($Restart -and (Test-Port $Port)) {
    $owner = Get-PortOwner $Port
    if ($owner -and (Test-RaconCommandLine $owner)) {
        Write-Host "[..] Stopping stale RACoN listener PID $owner"
        Stop-Process -Id $owner -Force -ErrorAction SilentlyContinue
        Start-Sleep -Seconds 1
    }
}

if (Test-Port $Port) {
    $owner = Get-PortOwner $Port
    Write-Host "[error] Port $Port is held by PID $owner, which is not a healthy RACoN API." -ForegroundColor Red
    Write-Host "        Close it, or run this script with -Restart." -ForegroundColor Red
    exit 1
}

$arguments = @(
    "-m", "uvicorn", "backend.racon.api.app:app",
    "--host", "127.0.0.1", "--port", "$Port"
)
Write-Host "[..] Starting unified API from source (admin dashboard, /v1, worker ingress)"
$process = Start-Process -FilePath $python -ArgumentList $arguments `
    -WorkingDirectory $root -WindowStyle Hidden -PassThru `
    -RedirectStandardOutput $outLog -RedirectStandardError $errLog

if (Wait-ForBackend $ReadyTimeoutSeconds) {
    Write-Host "[ok] Backend ready: http://127.0.0.1:$Port/admin" -ForegroundColor Green
    Ensure-Funnel
    exit 0
}

Write-Host "[error] Backend did not become healthy within $ReadyTimeoutSeconds seconds." -ForegroundColor Red
if ($process.HasExited) {
    Write-Host "        The source process exited with code $($process.ExitCode)." -ForegroundColor Red
}
Write-Host "        Output: $outLog" -ForegroundColor Red
Write-Host "        Errors: $errLog" -ForegroundColor Red
if (Test-Path $errLog) { Get-Content $errLog -Tail 25 }
exit 1
