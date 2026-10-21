# RACoN Platform - Quick Start Helper
# Usage:
#   .\run.ps1                 # Launch RACoN Lab v0.1.0 (PySide6 Desktop Application)
#   .\run.ps1 -Mode lab       # Launch PySide6 Desktop Application
#   .\run.ps1 -Mode desktop   # Launch standalone RACoN Desktop executable (.exe)
#   .\run.ps1 -Mode test      # Run full automated test suite
#   .\run.ps1 -Mode backend   # Run FastAPI coordinator backend
#   .\run.ps1 -Mode web       # Launch Vite preview in web browser
#   .\run.ps1 -Mode cli       # Run RACoN Companion CLI diagnostics

param(
    [string]$Mode = "lab"
)

$ErrorActionPreference = "Stop"

Write-Host "========================================" -ForegroundColor Cyan
Write-Host "  RACoN Platform Quick Start ($Mode)" -ForegroundColor Cyan
Write-Host "========================================" -ForegroundColor Cyan
Write-Host ""

$projectDir = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $projectDir

$venvPython = ".\.venv\Scripts\python.exe"

# 1. PySide6 Desktop Application (RACoN Lab v0.1.0 per Architecture Document)
if ($Mode -eq "lab" -or $Mode -eq "gui") {
    Write-Host "Starting RACoN Lab v0.1.0 [PySide6 Desktop Application]..." -ForegroundColor Green
    if (Test-Path $venvPython) {
        & $venvPython -m racon
    } else {
        python -m racon
    }
    exit $LASTEXITCODE
}

# 2. Automated Test Suite
if ($Mode -eq "test") {
    Write-Host "Running RACoN automated test suite..." -ForegroundColor Cyan
    & $venvPython -m pytest tests/ -v
    exit $LASTEXITCODE
}

# 3. CLI Diagnostics
if ($Mode -eq "cli") {
    Write-Host "Running RACoN CLI diagnostics..." -ForegroundColor Cyan
    & $venvPython cli/racon_cli.py doctor
    exit $LASTEXITCODE
}

# 4. Backend Coordinator API
if ($Mode -eq "backend") {
    Write-Host "Starting RACoN Coordinator Backend on http://0.0.0.0:8765..." -ForegroundColor Green
    & $venvPython -m uvicorn backend.racon.api.app:app --host 0.0.0.0 --port 8765
    exit $LASTEXITCODE
}

# 5. Web Preview
if ($Mode -eq "web") {
    Write-Host "Starting RACoN Coordinator Backend..." -ForegroundColor Green
    $backendProc = Start-Process -FilePath $venvPython -ArgumentList "-m uvicorn backend.racon.api.app:app --host 0.0.0.0 --port 8765" -PassThru -NoNewWindow
    Start-Sleep -Seconds 2
    try {
        Write-Host "Launching Web Preview..." -ForegroundColor Green
        Set-Location apps\desktop
        npm run preview -- --port 1420 --host 0.0.0.0
    } finally {
        Set-Location $projectDir
        if ($backendProc -and -not $backendProc.HasExited) {
            Stop-Process -Id $backendProc.Id -Force -ErrorAction SilentlyContinue
        }
    }
    exit 0
}

# 6. Standalone Desktop Window Executable
if ($Mode -eq "desktop" -or $Mode -eq "dist") {
    $exePath = Join-Path $projectDir "dist\RACoN_Lab\RACoN_Lab.exe"
    if (Test-Path $exePath) {
        Write-Host "Launching native RACoN Lab executable ($exePath)..." -ForegroundColor Green
        Start-Process -FilePath $exePath
        exit 0
    }
    $fallbackExe = Join-Path $projectDir "dist\RACoN_Desktop\RACoN_Desktop.exe"
    if (Test-Path $fallbackExe) {
        Write-Host "Launching RACoN Desktop executable..." -ForegroundColor Green
        Start-Process -FilePath $fallbackExe
        exit 0
    }
    if (Test-Path $venvPython) {
        Write-Host "Starting RACoN Lab..." -ForegroundColor Green
        & $venvPython -m racon
        exit 0
    }
}

Write-Host "Starting RACoN Lab v0.1.0..." -ForegroundColor Green
& $venvPython -m racon
exit 0
