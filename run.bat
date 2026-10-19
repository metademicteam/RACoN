@echo off
setlocal
cd /d "%~dp0"

if not exist ".venv\Scripts\python.exe" (
    echo Creating virtual environment with Python 3.12...
    py -3.12 -m venv .venv
    if errorlevel 1 (
        echo Fallback to default python...
        python -m venv .venv
    )
    echo Installing dependencies...
    .\.venv\Scripts\python.exe -m pip install -r requirements.txt
)

echo Starting RACoN Lab...
.\.venv\Scripts\python.exe -m racon %*
endlocal
