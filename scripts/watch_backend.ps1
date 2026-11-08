# Keep the unified RACoN control plane alive on 127.0.0.1:8765 after logon.
#
# The scheduled/startup launcher only runs once. If uvicorn exits or stops answering
# (as the old coordinator did), this loop restarts it and re-asserts the Tailscale
# Funnel target. One instance per logon session; a second copy exits immediately.
#
# Start manually: .\scripts\watch_backend.ps1
# Logs:           %LOCALAPPDATA%\RACoN\logs\watchdog.log

param(
    [int]$Port = 8765,
    [int]$IntervalSeconds = 30,
    [int]$FunnelEverySeconds = 600
)

$ErrorActionPreference = "Continue"
$root = Split-Path -Parent $PSScriptRoot
$startScript = Join-Path $root "scripts\start_backend.ps1"
$healthUrl = "http://127.0.0.1:$Port/health"
$adminUrl = "http://127.0.0.1:$Port/admin"
$logDir = Join-Path $env:LOCALAPPDATA "RACoN\logs"
New-Item -ItemType Directory -Path $logDir -Force | Out-Null
$logFile = Join-Path $logDir "watchdog.log"

function Write-WatchLog([string]$Message) {
    $line = "$(Get-Date -Format o) $Message"
    Add-Content -Path $logFile -Value $line -Encoding UTF8
}

function Test-Http([string]$Url) {
    try {
        $response = Invoke-WebRequest -Uri $Url -UseBasicParsing -TimeoutSec 3
        return ($response.StatusCode -eq 200)
    } catch {
        return $false
    }
}

$mutex = New-Object System.Threading.Mutex($false, "Local\RACoN-Backend-Watchdog")
if (-not $mutex.WaitOne(0)) {
    Write-WatchLog "another watchdog is already running; exiting"
    exit 0
}

Write-WatchLog "watchdog started (port $Port, interval ${IntervalSeconds}s)"
$lastFunnel = [DateTime]::MinValue

while ($true) {
    try {
        $healthy = (Test-Http $healthUrl) -and (Test-Http $adminUrl)
        if (-not $healthy) {
            Write-WatchLog "backend unhealthy on port $Port; starting it"
            & $startScript -Port $Port -Restart *> $null
            if ($LASTEXITCODE -eq 0) {
                Write-WatchLog "backend recovered"
            } else {
                Write-WatchLog "backend restart failed (exit $LASTEXITCODE)"
            }
        }
    } catch {
        Write-WatchLog "restart attempt failed: $($_.Exception.Message)"
    }

    if ((((Get-Date) - $lastFunnel).TotalSeconds) -ge $FunnelEverySeconds) {
        try {
            $tailscale = "C:\Program Files\Tailscale\tailscale.exe"
            if (-not (Test-Path $tailscale)) {
                $command = Get-Command tailscale -ErrorAction SilentlyContinue
                if ($command) { $tailscale = $command.Source }
            }
            if (Test-Path $tailscale) {
                & $tailscale funnel --bg $Port 2>&1 | Out-Null
                Write-WatchLog "re-asserted Funnel -> 127.0.0.1:$Port"
            }
        } catch {
            Write-WatchLog "funnel check failed: $($_.Exception.Message)"
        }
        $lastFunnel = Get-Date
    }

    Start-Sleep -Seconds $IntervalSeconds
}
