# Publish the current Windows RACoN installer to the website download folder.
# The unified manifest.json keeps Linux entries written by scripts/publish_website.sh.
#
# Usage:
#   .\scripts\publish_website.ps1
#   .\scripts\publish_website.ps1 -Version 1.1.0 -IncludeLinux

param(
    [string]$Version = "1.1.0",
    [switch]$IncludeLinux
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

if ($IncludeLinux) {
    $linuxSource = Get-ChildItem (Join-Path $root "dist\installer") -Filter "RACoN-linux-*-v$Version.tar.gz" -File |
        Select-Object -First 1
    if (-not $linuxSource) {
        Write-Host "Linux tarball not found for v$Version in dist\installer." -ForegroundColor Red
        exit 1
    }
    $debSource = Get-ChildItem (Join-Path $root "dist\installer") -Filter "racon_${Version}_*.deb" -File |
        Select-Object -First 1

    $linuxTarget = Join-Path $downloads $linuxSource.Name
    Copy-Item $linuxSource.FullName $linuxTarget -Force
    Get-ChildItem $downloads -Filter "RACoN-linux-*.tar.gz" -File |
        Where-Object { $_.FullName -ne $linuxTarget } |
        Remove-Item -Force

    $linuxItem = Get-Item $linuxTarget
    $linuxEntry = [ordered]@{
        file       = $linuxItem.Name
        size_bytes = $linuxItem.Length
        sha256     = (Get-FileHash $linuxTarget -Algorithm SHA256).Hash.ToLower()
        built_at   = $builtAt
    }

    if ($debSource) {
        $debTarget = Join-Path $downloads $debSource.Name
        Copy-Item $debSource.FullName $debTarget -Force
        Get-ChildItem $downloads -Filter "racon_*.deb" -File |
            Where-Object { $_.FullName -ne $debTarget } |
            Remove-Item -Force
        $debItem = Get-Item $debTarget
        $linuxEntry["deb"] = [ordered]@{
            file       = $debItem.Name
            size_bytes = $debItem.Length
            sha256     = (Get-FileHash $debTarget -Algorithm SHA256).Hash.ToLower()
        }
    }

    $platforms["linux"] = $linuxEntry
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
