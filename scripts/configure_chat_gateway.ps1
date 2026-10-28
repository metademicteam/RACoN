# Point the RACoN desktop chat at a remote RACoN gateway (no local model needed).
#
# Usage:
#   .\scripts\configure_chat_gateway.ps1 -ApiKey "rk_..."
#   .\scripts\configure_chat_gateway.ps1 -ApiKey "rk_..." -GatewayUrl "https://racon.metademic.org"

param(
    [Parameter(Mandatory = $true)][string]$ApiKey,
    [string]$GatewayUrl = "https://racon.metademic.org"
)

$ErrorActionPreference = "Stop"
$dir = Join-Path $env:LOCALAPPDATA "RACoN\racon"
New-Item -ItemType Directory -Path $dir -Force | Out-Null
$path = Join-Path $dir "gateway.json"
[ordered]@{ url = $GatewayUrl.TrimEnd("/"); api_key = $ApiKey } |
    ConvertTo-Json |
    Set-Content -Path $path -Encoding UTF8 -NoNewline

Write-Host "Wrote $path" -ForegroundColor Green
Write-Host "Chat will use $GatewayUrl when no local GGUF model is present."
Write-Host "Test it:" -ForegroundColor Yellow
Write-Host "  curl $($GatewayUrl.TrimEnd('/'))/v1/models -H `"Authorization: Bearer $ApiKey`""
