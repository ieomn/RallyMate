param(
    [int]$FrontendPort = 8000,
    [int]$ApiPort = 8001,
    [string]$FrontendHost = "0.0.0.0",
    [switch]$StartCloudflare,
    [string]$CloudflareUrl = "http://127.0.0.1:8000"
)

$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $PSScriptRoot
$WebRoot = Join-Path $Root "scoring-demo-web"
$Python = Join-Path $Root "runtime\rtmpose\.venv\Scripts\python.exe"
$LogRoot = Join-Path $Root ".codex_tmp\full-service"
New-Item -ItemType Directory -Force -Path $LogRoot | Out-Null

if (-not (Test-Path -LiteralPath $Python -PathType Leaf)) {
    $Python = Join-Path $env:USERPROFILE ".conda\envs\yolo\python.exe"
}
if (-not (Test-Path -LiteralPath $Python -PathType Leaf)) {
    $Python = (Get-Command python -CommandType Application | Select-Object -First 1).Source
}
if (-not $Python) { throw "Python executable was not found." }

Push-Location $Root
try {
    $env:PYTHONPATH = Join-Path $Root "src"
    $env:RALLYMATE_API_ORIGIN = "http://127.0.0.1:$ApiPort"

    $apiLog = Join-Path $LogRoot "api.log"
    $apiErr = Join-Path $LogRoot "api.err.log"
    $api = Start-Process -FilePath $Python -WorkingDirectory $Root -WindowStyle Hidden -PassThru `
        -RedirectStandardOutput $apiLog -RedirectStandardError $apiErr `
        -ArgumentList @("scripts/run_dev_service.py", "--host", "127.0.0.1", "--port", "$ApiPort")

    Push-Location $WebRoot
    try {
        $env:RALLYMATE_API_ORIGIN = "http://127.0.0.1:$ApiPort"
        $env:RALLYMATE_LOCAL_TUNNEL = "1"
        $webLog = Join-Path $LogRoot "web.log"
        $webErr = Join-Path $LogRoot "web.err.log"
        $web = Start-Process -FilePath "node" -WorkingDirectory $WebRoot -WindowStyle Hidden -PassThru `
            -RedirectStandardOutput $webLog -RedirectStandardError $webErr `
            -ArgumentList @("--env-file=.dev.vars", "node_modules/vinext/dist/cli.js", "start", "--hostname", $FrontendHost, "--port", "$FrontendPort")
    } finally {
        Pop-Location
    }

    if ($StartCloudflare) {
        $tunnelLog = Join-Path $LogRoot "cloudflared.log"
        $tunnelErr = Join-Path $LogRoot "cloudflared.err.log"
        $tunnel = Start-Process -FilePath "cloudflared" -WindowStyle Hidden -PassThru `
            -RedirectStandardOutput $tunnelLog -RedirectStandardError $tunnelErr `
            -ArgumentList @("tunnel", "--url", $CloudflareUrl)
    }

    [pscustomobject]@{
        ApiPid = $api.Id
        ApiUrl = "http://127.0.0.1:$ApiPort"
        FrontendPid = $web.Id
        FrontendUrl = "http://127.0.0.1:$FrontendPort"
        CloudflarePid = if ($tunnel) { $tunnel.Id } else { $null }
        LogDirectory = $LogRoot
    } | Format-List
    Write-Host "状态检查：powershell -ExecutionPolicy Bypass -File .\scripts\full_service_status.ps1" -ForegroundColor Cyan
    Write-Host "实时日志：powershell -ExecutionPolicy Bypass -File .\scripts\watch_full_service.ps1" -ForegroundColor Cyan
} finally {
    Pop-Location
}
