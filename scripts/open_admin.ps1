# Open the RACoN admin dashboard for this machine's tailnet address.
# Usage: .\scripts\open_admin.ps1 [-Server http://100.76.208.54:8765]
param(
    [string]$Server = ""
)

$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
Push-Location $root
try {
    function Get-EnvValue([string]$Name, [string]$Default) {
        if (-not (Test-Path ".env")) { return $Default }
        $line = Get-Content ".env" | Where-Object { $_ -match "^\s*$([regex]::Escape($Name))\s*=" } | Select-Object -First 1
        if (-not $line) { return $Default }
        $value = ($line -split "=", 2)[1].Trim().Trim('"').Trim("'")
        if ([string]::IsNullOrWhiteSpace($value)) { return $Default }
        return $value
    }

    $token = Get-EnvValue "AUTH_TOKEN" ""
    if ([string]::IsNullOrWhiteSpace($token)) {
        throw "AUTH_TOKEN is not set in .env. Add a long random administrator token first."
    }

    if (-not $Server) {
        $ip = "127.0.0.1"
        try {
            $candidate = (& tailscale ip -4 2>$null | Select-Object -First 1)
            if ($candidate) { $ip = $candidate.Trim() }
        } catch { }
        $Server = "http://${ip}:8765"
    }

    $url = "$($Server.TrimEnd('/'))/admin?token=$([uri]::EscapeDataString($token))"
    Write-Host "Opening $url" -ForegroundColor Cyan
    Start-Process $url
}
finally {
    Pop-Location
}
