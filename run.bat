@echo off
setlocal
cd /d "%~dp0"

set MODE=%1
if "%MODE%"=="" set MODE=lab

REM 1. PySide6 Desktop Application (RACoN Lab v0.1.0 per Architecture Document)
if "%MODE%"=="dist" (
    echo Launching RACoN Lab Standalone Executable...
    start "" "dist\RACoN_Lab\RACoN_Lab.exe"
    goto :eof
)

if "%MODE%"=="lab" (
    echo Starting RACoN Lab v0.1.0 [PySide6 Desktop Application]...
    if exist ".\.venv\Scripts\python.exe" (
        .\.venv\Scripts\python.exe -m racon
    ) else (
        python -m racon
    )
    goto :eof
)

if "%MODE%"=="gui" (
    echo Starting RACoN Lab v0.1.0 [PySide6 Desktop Application]...
    .\.venv\Scripts\python.exe -m racon
    goto :eof
)

REM 2. Automated Test Suite
if "%MODE%"=="test" (
    echo Running automated test suite...
    .\.venv\Scripts\python.exe -m pytest tests/ -v
    goto :eof
)

REM 3. CLI Diagnostics
if "%MODE%"=="cli" (
    echo Running CLI diagnostics...
    .\.venv\Scripts\python.exe cli/racon_cli.py doctor
    goto :eof
)

REM 4. Backend Coordinator API
if "%MODE%"=="backend" (
    echo Starting RACoN Backend Coordinator on 0.0.0.0:8765...
    .\.venv\Scripts\python.exe -m uvicorn backend.racon.api.app:app --host 0.0.0.0 --port 8765
    goto :eof
)

REM 5. Web Preview
if "%MODE%"=="web" (
    echo Starting RACoN Web Preview...
    start "RACoN Backend" .\.venv\Scripts\python.exe -m uvicorn backend.racon.api.app:app --host 0.0.0.0 --port 8765
    timeout /t 2 /nobreak > nul
    cd apps\desktop
    call npm run preview -- --port 1420 --host 0.0.0.0
    goto :eof
)

REM 6. Native Window Executable (.exe)
if "%MODE%"=="desktop" (
    if exist "dist\RACoN_Desktop\RACoN_Desktop.exe" (
        echo Launching native RACoN Desktop executable (.exe)...
        start "" "dist\RACoN_Desktop\RACoN_Desktop.exe"
        goto :eof
    )
    if exist ".venv\Scripts\python.exe" (
        echo Starting RACoN Desktop Window...
        .\.venv\Scripts\python.exe desktop_app.py
        goto :eof
    )
)

echo Starting RACoN Lab v0.1.0...
.\.venv\Scripts\python.exe -m racon
endlocal
