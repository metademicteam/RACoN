@echo off
setlocal
cd /d "%~dp0\.."
powershell -ExecutionPolicy Bypass -File scripts\tailscale_info.ps1
endlocal
