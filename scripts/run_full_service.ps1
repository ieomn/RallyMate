param(
    [int]$FrontendPort = 8000,
    [int]$ApiPort = 8001,
    [string]$FrontendHost = "0.0.0.0",
    [switch]$StartCloudflare,
    [string]$CloudflareUrl = "http://127.0.0.1:8000",
    [switch]$ApiOnly
)

$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $PSScriptRoot
$WebRoot = Join-Path $Root "scoring-demo-web"
$Python = Join-Path $Root "runtime\rtmpose\.venv\Scripts\python.exe"
$LogRoot = Join-Path $Root ".codex_tmp\full-service"
. (Join-Path $PSScriptRoot 'local_performance_profile.ps1')
if ($FrontendPort -eq $ApiPort -or $FrontendPort -lt 1024 -or $ApiPort -lt 1024 -or $FrontendPort -gt 65535 -or $ApiPort -gt 65535) {
    throw 'Choose different frontend/API ports between 1024 and 65535.'
}
$occupied = @(Get-NetTCPConnection -State Listen -ErrorAction SilentlyContinue |
    Where-Object { $_.LocalPort -in @($FrontendPort, $ApiPort) })
if ($occupied.Count -gt 0) { throw 'A selected service port is occupied. No process was started or log overwritten.' }
$performanceModel = if ($env:RALLYMATE_POSE_BACKEND -eq 'sam3d' -and -not $env:RALLYMATE_POSE_PRESET -and
    $env:RALLYMATE_POSE_MODEL -match 'sam-3d-body-dinov3[\\/]') { 'sam3d-dinov3' } else { '' }
$performanceDevice = if ($env:RALLYMATE_DEVICE) { $env:RALLYMATE_DEVICE } else { '0' }
$launchProfile = Read-LocalPerformanceProfile -ProjectRoot $Root -Model $performanceModel -Device $performanceDevice -ValidateDevice:(-not $ApiOnly)
Set-LocalPerformanceEnvironment $launchProfile
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
    # Maintenance mode keeps existing reports readable while isolated GPU
    # experiments run; no new queue entries may prevent safe worker restoration.
    $env:RALLYMATE_ACCEPT_NEW_JOBS = if ($ApiOnly) { "0" } else { "1" }

    $apiLog = Join-Path $LogRoot "api.log"
    $apiErr = Join-Path $LogRoot "api.err.log"
    $logBackup = Backup-LocalServiceLogs -LogRoot $LogRoot -IncludeTunnel:$StartCloudflare
    $apiArguments = @("scripts/run_dev_service.py", "--host", "127.0.0.1", "--port", "$ApiPort")
    if ($ApiOnly) { $apiArguments += "--api-only" }
    $api = Start-Process -FilePath $Python -WorkingDirectory $Root -WindowStyle Hidden -PassThru `
        -RedirectStandardOutput $apiLog -RedirectStandardError $apiErr `
        -ArgumentList $apiArguments
    # A launcher receipt is evidence of the inherited environment, not proof
    # that model loading/inference succeeded. Per-job records verify execution.
    [ordered]@{ schema_version = 1; launched_at = [DateTime]::UtcNow.ToString('o'); api_launcher_pid = $api.Id;
        inference_worker = -not $ApiOnly; profile_path = $(if ($launchProfile) { $launchProfile.path } else { $null });
        profile_sha256 = $(if ($launchProfile) { $launchProfile.sha256 } else { $null });
        settings = $(if ($launchProfile) { $launchProfile.settings } else { @{} });
        sources = $(if ($launchProfile) { $launchProfile.sources } else { @{} });
        log_backup = $logBackup; scope = 'launcher environment; verify readiness and per-job execution separately' } |
        ConvertTo-Json -Depth 5 | Set-Content -LiteralPath (Join-Path $LogRoot 'launch-profile.json') -Encoding UTF8

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
        InferenceWorker = -not $ApiOnly
        FrontendPid = $web.Id
        FrontendUrl = "http://127.0.0.1:$FrontendPort"
        CloudflarePid = if ($tunnel) { $tunnel.Id } else { $null }
        LogDirectory = $LogRoot
    } | Format-List
    Write-Host "状态检查：powershell -ExecutionPolicy Bypass -File .\scripts\full_service_status.ps1" -ForegroundColor Cyan
    Write-Host "实时监控：powershell -ExecutionPolicy Bypass -File .\scripts\watch_full_service.ps1" -ForegroundColor Cyan
} finally {
    Pop-Location
}
