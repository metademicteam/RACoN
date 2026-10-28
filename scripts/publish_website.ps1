# Publish the current RACoN installer to the website download folder and write its manifest.
#
# Only the current installer is copied. Older builds in dist\installer are not published.
#
# Usage:
#   .\scripts\publish_website.ps1
#   .\scripts\publish_website.ps1 -Version 1.0.1

param(
    [string]$Version = "1.0.1"
)

$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
$source = Join-Path $root "dist\installer\RACoN_Setup_v$Version.exe"
$downloads = Join-Path $root "dist\website_downloads"

if (-not (Test-Path $source)) {
    Write-Host "Installer not found: $source. Build it with ISCC first." -ForegroundColor Red
    exit 1
}

New-Item -ItemType Directory -Path $downloads -Force | Out-Null
Get-ChildItem $downloads -File | Remove-Item -Force

$target = Join-Path $downloads (Split-Path $source -Leaf)
Copy-Item $source $target -Force

$hash = (Get-FileHash $target -Algorithm SHA256).Hash.ToLower()
$item = Get-Item $target
$manifest = [ordered]@{
    product    = "RACoN"
    version    = $Version
    file       = $item.Name
    size_bytes = $item.Length
    sha256     = $hash
    built_at   = (Get-Date).ToUniversalTime().ToString("yyyy-MM-dd HH:mm") + " UTC"
}
$json = $manifest | ConvertTo-Json
[System.IO.File]::WriteAllText((Join-Path $downloads "manifest.json"), $json, (New-Object System.Text.UTF8Encoding($false)))

Write-Host "Published $($item.Name) ($([math]::Round($item.Length / 1MB, 1)) MB)" -ForegroundColor Green
Write-Host "SHA-256: $hash"
