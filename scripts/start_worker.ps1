# Join this machine to a RACoN server as a provider worker.
# Usage:
#   .\scripts\start_worker.ps1 -Server http://100.76.208.54:8765
#   .\scripts\start_worker.ps1 -Server http://100.76.208.54:8765 -JoinCode RCN-PUBLIC-JOIN -Headless
param(
    [Parameter(Mandatory = $true)]
    [string]$Server,

    [string]$JoinCode = "RCN-PUBLIC-JOIN",

    [switch]$Headless
)

$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
Push-Location $root
try {
    $python = Join-Path $root ".venv\Scripts\python.exe"
    if (-not (Test-Path $python)) {
        throw "Python virtual environment not found at $python. Run the installer or create .venv first."
    }

    $env:RACON_COORDINATOR_URL = $Server.TrimEnd("/")
    $env:RACON_JOIN_CODE = $JoinCode
    if (-not $env:RACON_DATA_DIR) {
        $env:RACON_DATA_DIR = Join-Path $env:LOCALAPPDATA "RACoN"
    }

    Write-Host "Coordinator : $env:RACON_COORDINATOR_URL" -ForegroundColor Cyan
    Write-Host "Join code   : $env:RACON_JOIN_CODE" -ForegroundColor Cyan
    Write-Host "Data dir    : $env:RACON_DATA_DIR" -ForegroundColor Cyan

    if ($Headless) {
        & $python -m racon --headless
    } else {
        & $python -m racon.desktop
    }
}
finally {
    Pop-Location
}
