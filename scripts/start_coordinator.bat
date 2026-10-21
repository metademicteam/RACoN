@echo off
REM ===================================================================
REM RACoN Coordinator Server Launcher
REM Runs dual-listener coordinator (Worker API on :8765, Admin API on :8766)
REM ===================================================================
cd /d "%~dp0\.."
echo Starting RACoN Coordinator Server...
".venv\Scripts\python.exe" -m racon.coordinator.server %*
pause
