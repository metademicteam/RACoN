# Start the RACoN head stack automatically at Windows logon (no admin rights needed).
#
# Creates a shortcut in the current user's Startup folder that runs start_head.ps1 hidden.
# Remove it with: Remove-Item "$([Environment]::GetFolderPath('Startup'))\RACoN Head Stack.lnk"

$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
$startup = [Environment]::GetFolderPath("Startup")
$shortcutPath = Join-Path $startup "RACoN Head Stack.lnk"

$shell = New-Object -ComObject WScript.Shell
$shortcut = $shell.CreateShortcut($shortcutPath)
$shortcut.TargetPath = "powershell.exe"
$shortcut.Arguments = "-NoProfile -WindowStyle Hidden -ExecutionPolicy Bypass -File `"$root\scripts\start_head.ps1`""
$shortcut.WorkingDirectory = $root
$shortcut.WindowStyle = 7
$shortcut.Description = "Start the RACoN head stack (website, coordinator, gateway, tunnel)"
$shortcut.Save()

Write-Host "Created $shortcutPath" -ForegroundColor Green
Write-Host "The head stack will start automatically the next time you sign in."
Write-Host "Test it now: .\scripts\start_head.ps1"
