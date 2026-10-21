@echo off
REM RACoN Tailscale Funnel Batch Helper
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0tailscale_funnel.ps1" %*
