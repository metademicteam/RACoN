# Verify that a remote RPC worker actually holds model layers.
#
# Starts its own llama-server on a temporary port (never touches the live head on 8090), splits
# N layers onto the worker with -dev RPC0, checks the load log for the RPC device lines, and
# measures generation speed. Stops the test server on exit.
#
# Usage:
#   .\scripts\verify_rpc_worker.ps1 -WorkerHost 100.125.77.125 -Layers 2
#   .\scripts\verify_rpc_worker.ps1 -WorkerHost 100.125.77.125 -Layers 4 -Port 8092
param(
    [Parameter(Mandatory = $true)][string]$WorkerHost,
    [int]$WorkerPort = 50052,
    [int]$Layers = 2,
    [int]$Port = 8091,
    [int]$ContextSize = 512,
    [int]$Tokens = 8,
    [int]$ReadyTimeoutSeconds = 300,
    [int]$StallSeconds = 45,
    [string]$ModelPath = "",
    [string]$LogPath = ""
)

$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
$python = Join-Path $root ".venv\Scripts\python.exe"

if (-not $ModelPath) {
    $config = Join-Path $env:LOCALAPPDATA "RACoN\racon\cluster\cluster.json"
    if (-not (Test-Path $config)) { throw "Pass -ModelPath or create $config" }
    $ModelPath = (Get-Content $config -Raw | ConvertFrom-Json).model_path
}
if (-not (Test-Path $ModelPath)) { throw "Model not found: $ModelPath" }
if (-not $LogPath) {
    $LogPath = Join-Path $env:TEMP "racon-rpc-verify-$Port.log"
}

$server = & $python -c "from racon.runtime.distributed_gguf import find_llama_tool; print(find_llama_tool('llama-server') or '')"
if (-not $server) { throw "llama-server not found. Install llama.cpp or run from the RACoN repo." }

$key = "racon-verify"
$arguments = @(
    "-m", $ModelPath, "--host", "127.0.0.1", "--port", "$Port",
    "-ngl", "$Layers", "-c", "$ContextSize", "-t", "2", "--no-webui", "--jinja",
    "--api-key", $key,
    "--rpc", "$($WorkerHost):$($WorkerPort)", "-sm", "layer", "-dev", "RPC0", "-ts", "1.0"
)

$proc = Start-Process -FilePath $server -ArgumentList $arguments -PassThru -WindowStyle Hidden `
    -RedirectStandardOutput "$LogPath.out" -RedirectStandardError $LogPath
Write-Host "Started llama-server (PID $($proc.Id)): $Layers layer(s) to $WorkerHost`:$WorkerPort on port $Port"
Write-Host "Log: $LogPath"

$sw = [Diagnostics.Stopwatch]::StartNew()
$healthy = $false
$lastSize = 0
$lastChange = $sw.Elapsed.TotalSeconds
try {
    while ($sw.Elapsed.TotalSeconds -lt $ReadyTimeoutSeconds) {
        if ($proc.HasExited) { throw "llama-server exited with code $($proc.ExitCode)" }
        try {
            if ((Invoke-WebRequest -UseBasicParsing "http://127.0.0.1:$Port/health" -TimeoutSec 2).StatusCode -eq 200) {
                $healthy = $true
                break
            }
        } catch { }
        $size = if (Test-Path $LogPath) { (Get-Item $LogPath).Length } else { 0 }
        if ($size -ne $lastSize) {
            $lastSize = $size
            $lastChange = $sw.Elapsed.TotalSeconds
        } elseif (($sw.Elapsed.TotalSeconds - $lastChange) -gt $StallSeconds) {
            throw "No log progress for $StallSeconds s; the RPC transfer is stalled."
        }
        Start-Sleep -Seconds 1
    }
    if (-not $healthy) { throw "Not healthy within $ReadyTimeoutSeconds s." }

    Write-Host "Healthy after $([math]::Round($sw.Elapsed.TotalSeconds,1)) s"
    $evidence = Select-String -Path $LogPath -Pattern "using device RPC0|offloading .* repeating|offloaded .* layers|RPC0\[.*\] model buffer size"
    if (-not $evidence) { throw "No RPC device lines in $LogPath; the split did not happen." }
    $evidence | ForEach-Object { Write-Host "  $($_.Line.Trim())" }

    $body = @{ prompt = "Say hi in one sentence."; n_predict = $Tokens; temperature = 0.2 } | ConvertTo-Json
    $gen = [Diagnostics.Stopwatch]::StartNew()
    $response = Invoke-RestMethod -Uri "http://127.0.0.1:$Port/completion" -Method Post -Body $body `
        -ContentType "application/json" -Headers @{ Authorization = "Bearer $key" } -TimeoutSec 300
    $gen.Stop()
    Write-Host "Generation: $([math]::Round($response.timings.predicted_per_second,2)) tok/s over $($gen.Elapsed.TotalSeconds) s"
    Write-Host "Text: $(($response.content -replace '\s+', ' ').Trim())"
} finally {
    if (-not $proc.HasExited) { Stop-Process -Id $proc.Id -Force }
    Write-Host "Stopped the test server. Live head on 8090 was not touched."
}
