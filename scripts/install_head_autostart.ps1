# Start the RACoN head stack automatically at Windows logon (no admin rights needed).
#
# Creates a shortcut in the current user's Startup folder that runs start_head.ps1 hidden.
# Remove it with: Remove-Item "$([Environment]::GetFolderPath('Startup'))\RACoN Head Stack.lnk"

$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
$startup = [Environment]::GetFolderPath("Startup")
$shortcutPath = Join-Path $startup "RACoN Head Stack.lnk"

# Disable legacy entries that started the old coordinator or moved Tailscale Funnel to
# 8443. They conflict with the unified app on 8765 and make /admin return "Not Found".
foreach ($legacyName in @("racn-server-autostart.bat")) {
    $legacyPath = Join-Path $startup $legacyName
    if (Test-Path $legacyPath) {
        $disabled = "$legacyPath.disabled-$(Get-Date -Format yyyyMMdd-HHmmss)"
        Rename-Item -Path $legacyPath -NewName (Split-Path -Leaf $disabled)
        Write-Host "Disabled stale autostart: $disabled" -ForegroundColor Yellow
    }
}

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
