# Build the RACoN Linux/Ubuntu release inside an Ubuntu container.
#
# PyInstaller cannot cross-compile from Windows, so this script mounts the repository
# into ubuntu:24.04, installs a current CPU llama.cpp release, and runs
# scripts/build_linux.sh. Artifacts land in dist/installer/ (tarball, .deb, checksums).
#
# The inner commands are written to a file with LF endings: passing a multi-line string
# through PowerShell to `bash -lc` truncates it and fails with "unexpected end of file".
#
# Usage:
#   .\scripts\build_linux_docker.ps1
#   .\scripts\build_linux_docker.ps1 -Version 1.1.0 -LlamaTag b12125
param(
    [string]$Version = "1.1.0",
    [string]$LlamaTag = "b12125",
    [string]$UbuntuImage = "ubuntu:24.04",
    # Optional local llama.cpp folder. Use it when GitHub release downloads are unavailable.
    [string]$LlamaDir = ""
)

$ErrorActionPreference = "Continue"
$root = Split-Path -Parent $PSScriptRoot
$cache = Join-Path $root ".pytest_cache"
$log = Join-Path $cache "linux_build.log"
$inner = Join-Path $cache "build_linux_inner.sh"
New-Item -ItemType Directory -Path $cache -Force | Out-Null

$llamaUrl = "https://github.com/ggml-org/llama.cpp/releases/download/$LlamaTag/llama-$LlamaTag-bin-ubuntu-x64.tar.gz"

$body = @'
set -euo pipefail
export DEBIAN_FRONTEND=noninteractive
apt-get update -qq
apt-get install -y -qq python3 python3-venv python3-pip curl ca-certificates libgomp1 binutils file
mkdir -p /opt/racon/llama
if [ -n "__LLAMA_DIR__" ]; then
    echo "Using cached llama.cpp from __LLAMA_DIR__"
    cp -a /llama-src/. /opt/racon/llama/
else
    for attempt in 1 2 3; do
        if curl -fsSL -o /tmp/llama.tar.gz "__LLAMA_URL__"; then break; fi
        echo "llama.cpp download failed (attempt $attempt); retrying..." >&2
        sleep 3
    done
    test -s /tmp/llama.tar.gz || { echo "could not download llama.cpp" >&2; exit 3; }
    tar -xzf /tmp/llama.tar.gz -C /opt/racon/llama --strip-components=1
fi
test -x /opt/racon/llama/llama-server || { echo "llama-server missing from release" >&2; exit 2; }
cd /work
VENV=/tmp/racon-venv RACON_LLAMA_DIR=/opt/racon/llama VERSION="__VERSION__" ./scripts/build_linux.sh
'@

$body = $body.Replace("__LLAMA_URL__", $llamaUrl).Replace("__VERSION__", $Version)
$mountArgs = @()
if ($LlamaDir) {
    $resolvedLlama = (Resolve-Path $LlamaDir).Path
    $body = $body.Replace("__LLAMA_DIR__", "/llama-src")
    $mountArgs = @("-v", "${resolvedLlama}:/llama-src:ro")
} else {
    $body = $body.Replace("__LLAMA_DIR__", "")
}
[System.IO.File]::WriteAllText($inner, ($body -replace "`r`n", "`n"), (New-Object System.Text.UTF8Encoding($false)))

Write-Host "Building RACoN $Version for Linux in $UbuntuImage (llama.cpp $LlamaTag)…"
docker run --rm @mountArgs `
    -e "RACON_VERSION=$Version" `
    -v "${root}:/work" `
    -w /work `
    $UbuntuImage `
    bash -lc "bash /work/.pytest_cache/build_linux_inner.sh" *> $log
$code = $LASTEXITCODE

Get-Content $log -Tail 30
if ($code -ne 0) {
    Write-Host "Linux build failed (exit $code). See $log" -ForegroundColor Red
    exit $code
}

Get-ChildItem (Join-Path $root "dist\installer") -Filter "*$Version*" | ForEach-Object {
    Write-Host ("  {0}  {1:N1} MB" -f $_.Name, ($_.Length / 1MB))
}
