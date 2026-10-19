# RACoN Lab — Quick Start
# This script creates a virtual environment (if needed), installs dependencies, and launches the app.

$ErrorActionPreference = "Stop"

Write-Host "========================================" -ForegroundColor Cyan
Write-Host "  RACoN Lab v0.1.0 — Quick Start" -ForegroundColor Cyan
Write-Host "========================================" -ForegroundColor Cyan
Write-Host ""

$projectDir = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $projectDir

# Find Python
$pythonCmd = $null
foreach ($candidate in @("py -3.12", "py -3.11", "py -3.10", "python")) {
    try {
        $version = & ($candidate.Split(" ")[0]) ($candidate.Split(" ") | Select-Object -Skip 1) --version 2>&1
        if ($version -match "Python 3\.(1[0-4]|[0-9])") {
            $pythonCmd = $candidate
            Write-Host "Found: $version" -ForegroundColor Green
            break
        }
    } catch { }
}

if (-not $pythonCmd) {
    Write-Host "ERROR: Python 3.10+ not found. Install from https://python.org" -ForegroundColor Red
    exit 1
}

# Create venv if needed
if (-not (Test-Path ".venv")) {
    Write-Host "Creating virtual environment..." -ForegroundColor Yellow
    & ($pythonCmd.Split(" ")[0]) ($pythonCmd.Split(" ") | Select-Object -Skip 1) -m venv .venv
}

$venvPython = ".\.venv\Scripts\python.exe"
if (-not (Test-Path $venvPython)) {
    Write-Host "ERROR: Virtual environment Python not found at $venvPython" -ForegroundColor Red
    exit 1
}

# Install dependencies
Write-Host "Installing dependencies..." -ForegroundColor Yellow
& $venvPython -m pip install --upgrade pip -q
& $venvPython -m pip install -r requirements.txt -q

Write-Host ""
Write-Host "Launching RACoN Lab..." -ForegroundColor Green
Write-Host "  (Close the window or press Ctrl+C to exit)"
Write-Host ""

& $venvPython -m racon
