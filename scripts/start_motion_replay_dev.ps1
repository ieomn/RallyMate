param(
    [ValidateRange(1024, 65535)][int]$ApiPort = 8005,
    [ValidateRange(1024, 65535)][int]$FrontendPort = 8006,
    [switch]$NoAutoGeneration
)

$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path -Parent $PSScriptRoot
$webRoot = Join-Path $projectRoot 'scoring-demo-web'
$logRoot = Join-Path $projectRoot '.codex_tmp/motion-replay-dev'
if ($ApiPort -eq $FrontendPort) { throw 'The API and frontend need different ports.' }
foreach ($listenPort in @($ApiPort, $FrontendPort)) {
    if (Get-NetTCPConnection -LocalPort $listenPort -State Listen -ErrorAction SilentlyContinue) {
        throw "Port $listenPort is already in use. Select a free port; no existing service was stopped."
    }
}
$pythonExecutable = (Get-Command python -CommandType Application | Select-Object -First 1).Source
$nodeExecutable = (Get-Command node -CommandType Application | Select-Object -First 1).Source
$webCli = Join-Path $webRoot 'node_modules/vinext/dist/cli.js'
if (-not (Test-Path -LiteralPath $webCli)) { throw 'Install frontend dependencies before starting the preview.' }
New-Item -ItemType Directory -Path $logRoot -Force | Out-Null

$env:PYTHONPATH = Join-Path $projectRoot 'src'
$env:RALLYMATE_DATA_ROOT = Join-Path $projectRoot 'service_data'
$env:RALLYMATE_MOTION_REPLAY_ENABLED = '1'
$env:RALLYMATE_MOTION_REPLAY_AUTO_GENERATE = if ($NoAutoGeneration) { '0' } else { '1' }
# This local machine routes public CDN traffic through a fake-IP DNS proxy.
# The provider only permits benchmark addresses for its exact official CDN.
$env:RALLYMATE_MINIMAX_ALLOW_FAKEIP = '1'
$env:RALLYMATE_API_ORIGIN = "http://127.0.0.1:$ApiPort"
$env:RALLYMATE_API_PROXY = $env:RALLYMATE_API_ORIGIN
$env:RALLYMATE_LOCAL_TUNNEL = '1'
$env:MIMO_ADVICE_ENABLED = '0'
$devVariables = Join-Path $webRoot '.dev.vars'
if (Test-Path -LiteralPath $devVariables) {
    foreach ($line in [IO.File]::ReadAllLines($devVariables)) {
        if ($line -match '^\s*RALLYMATE_API_KEY\s*=\s*(.*?)\s*$') {
            $env:RALLYMATE_API_KEY = $Matches[1].Trim().Trim('"').Trim("'")
        }
    }
}

$backend = Start-Process -FilePath $pythonExecutable -ArgumentList @('-m', 'uvicorn', 'rallymate_service.api:app', '--host', '127.0.0.1', '--port', "$ApiPort") -WorkingDirectory $projectRoot -WindowStyle Hidden -PassThru -RedirectStandardOutput (Join-Path $logRoot 'api.stdout.log') -RedirectStandardError (Join-Path $logRoot 'api.stderr.log')
$ready = $false
for ($attempt = 0; $attempt -lt 30; $attempt++) {
    if ($backend.HasExited) { throw 'The API stopped during startup. See .codex_tmp/motion-replay-dev/api.stderr.log.' }
    try {
        $response = Invoke-RestMethod -Uri "http://127.0.0.1:$ApiPort/health/live" -TimeoutSec 2
        if ($response.status -eq 'ok') { $ready = $true; break }
    } catch { }
    Start-Sleep -Milliseconds 500
}
if (-not $ready) { throw 'The API has not become ready. Check the API log before retrying.' }
$frontend = Start-Process -FilePath $nodeExecutable -ArgumentList @('node_modules/vinext/dist/cli.js', 'dev', '--hostname', '127.0.0.1', '--port', "$FrontendPort") -WorkingDirectory $webRoot -WindowStyle Hidden -PassThru -RedirectStandardOutput (Join-Path $logRoot 'web.stdout.log') -RedirectStandardError (Join-Path $logRoot 'web.stderr.log')
@{ apiPid = $backend.Id; frontendPid = $frontend.Id; apiPort = $ApiPort; frontendPort = $FrontendPort } | ConvertTo-Json | Set-Content -LiteralPath (Join-Path $logRoot 'processes.json') -Encoding utf8
Write-Output "Motion replay API: http://127.0.0.1:$ApiPort"
Write-Output "Development preview: http://127.0.0.1:$FrontendPort"
Write-Output 'Existing analysis workers continue to handle uploaded videos; this preview runs the persistent motion-replay worker.'
