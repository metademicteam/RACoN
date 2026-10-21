# RACoN Desktop App Installer
# Creates a Windows Desktop shortcut pointing directly to the compiled native RACoN_Desktop.exe.

$ErrorActionPreference = "Stop"

Write-Host "==========================================" -ForegroundColor Cyan
Write-Host "   RACoN Desktop Application Installer" -ForegroundColor Cyan
Write-Host "==========================================" -ForegroundColor Cyan
Write-Host ""

$projectDir = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $projectDir

$desktopDir = [System.Environment]::GetFolderPath([System.Environment+SpecialFolder]::Desktop)
$shortcutPath = Join-Path $desktopDir "RACoN Desktop.lnk"
$iconPath = Join-Path $projectDir "apps\desktop\icon.ico"
$targetExe = Join-Path $projectDir "dist\RACoN_Desktop\RACoN_Desktop.exe"

# If compiled exe not found, fallback to run.bat
if (-not (Test-Path $targetExe)) {
    $targetExe = Join-Path $projectDir "run.bat"
}

Write-Host "Installing RACoN Desktop (.exe) shortcut..." -ForegroundColor Yellow

$WshShell = New-Object -ComObject WScript.Shell
$Shortcut = $WshShell.CreateShortcut($shortcutPath)
$Shortcut.TargetPath = $targetExe
$Shortcut.WorkingDirectory = Split-Path -Parent $targetExe
$Shortcut.Description = 'RACoN Desktop - Native Windows Application'
if (Test-Path $iconPath) {
    $Shortcut.IconLocation = "$iconPath,0"
}
$Shortcut.Save()

Write-Host ""
Write-Host "================================================================" -ForegroundColor Green
Write-Host " SUCCESS! RACoN Desktop.exe shortcut created on your Desktop:" -ForegroundColor Green
Write-Host "   $shortcutPath" -ForegroundColor Cyan
Write-Host " Target Executable:" -ForegroundColor Green
Write-Host "   $targetExe" -ForegroundColor Cyan
Write-Host "================================================================" -ForegroundColor Green
Write-Host ""
Write-Host "You can now launch RACoN anytime by double-clicking the" -ForegroundColor White
Write-Host "RACoN Desktop icon on your desktop or running RACoN_Desktop.exe directly." -ForegroundColor White
Write-Host ""
Write-Host "Features available:" -ForegroundColor Cyan
Write-Host "  - Native Windows desktop app (.exe) with custom window & icon"
Write-Host "  - Handy-style Model Hub (1-click download of Llama 3.2, Qwen 2.5, SmolLM2)"
Write-Host "  - Laya-assisted intelligent compute scheduling"
Write-Host "  - Global internet access via Tailscale Funnel"
Write-Host ""
