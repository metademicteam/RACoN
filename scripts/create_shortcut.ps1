$WshShell = New-Object -ComObject WScript.Shell
$Desktop = [System.Environment]::GetFolderPath([System.Environment+SpecialFolder]::Desktop)
$ShortcutPath = Join-Path $Desktop "RACoN Desktop.lnk"
$Shortcut = $WshShell.CreateShortcut($ShortcutPath)
$Shortcut.TargetPath = "F:\RACoN\dist\RACoN_Desktop\RACoN_Desktop.exe"
$Shortcut.WorkingDirectory = "F:\RACoN\dist\RACoN_Desktop"
$Shortcut.IconLocation = "F:\RACoN\dist\RACoN_Desktop\apps\desktop\icon.ico,0"
$Shortcut.Description = "RACoN Desktop - Local Distributed AI Compute and Model Hub"
$Shortcut.Save()
Write-Host "Created shortcut: $ShortcutPath"
