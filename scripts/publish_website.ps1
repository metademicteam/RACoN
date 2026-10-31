# Publish the current Windows RACoN installer to the website download folder.
# The unified manifest.json keeps Linux entries written by scripts/publish_website.sh.
#
# Usage:
#   .\scripts\publish_website.ps1
#   .\scripts\publish_website.ps1 -Version 1.0.2

param(
    [string]$Version = "1.0.6"
)

$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
$source = Join-Path $root "dist\installer\RACoN_Setup_v$Version.exe"
$downloads = Join-Path $root "dist\website_downloads"
$manifestPath = Join-Path $downloads "manifest.json"

if (-not (Test-Path $source)) {
    Write-Host "Installer not found: $source. Build it with ISCC first." -ForegroundColor Red
    exit 1
}

New-Item -ItemType Directory -Path $downloads -Force | Out-Null
$target = Join-Path $downloads (Split-Path $source -Leaf)
Copy-Item $source $target -Force

# Remove older Windows installers only; Linux artifacts and the manifest stay in place.
Get-ChildItem $downloads -Filter "RACoN_Setup_*.exe" -File |
    Where-Object { $_.FullName -ne $target } |
    Remove-Item -Force

$hash = (Get-FileHash $target -Algorithm SHA256).Hash.ToLower()
$item = Get-Item $target
$builtAt = (Get-Date).ToUniversalTime().ToString("yyyy-MM-dd HH:mm") + " UTC"

$platforms = @{}
if (Test-Path $manifestPath) {
    try {
        $old = Get-Content $manifestPath -Raw | ConvertFrom-Json
        if ($old.platforms) {
            foreach ($p in $old.platforms.PSObject.Properties) {
                $platforms[$p.Name] = $p.Value
            }
        } elseif ($old.file) {
            $platforms["windows"] = [ordered]@{
                file       = $old.file
                size_bytes = $old.size_bytes
                sha256     = $old.sha256
                built_at   = $old.built_at
            }
        }
    } catch {
        Write-Host "Existing manifest could not be read; writing a fresh one." -ForegroundColor Yellow
    }
}

$platforms["windows"] = [ordered]@{
    file       = $item.Name
    size_bytes = $item.Length
    sha256     = $hash
    built_at   = $builtAt
}

$manifest = [ordered]@{
    product    = "RACoN"
    version    = $Version
    built_at   = $builtAt
    platforms  = $platforms
    # Legacy top-level fields for older cached site scripts.
    file       = $item.Name
    size_bytes = $item.Length
    sha256     = $hash
}

$json = $manifest | ConvertTo-Json -Depth 6
[System.IO.File]::WriteAllText($manifestPath, $json, (New-Object System.Text.UTF8Encoding($false)))

Write-Host "Published $($item.Name) ($([math]::Round($item.Length / 1MB, 1)) MB)" -ForegroundColor Green
Write-Host "SHA-256: $hash"
Write-Host "Manifest: $manifestPath"
