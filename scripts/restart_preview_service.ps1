param(
    [ValidateRange(1024, 65535)]
    [int]$FrontendPort = 8003,
    [ValidateRange(1024, 65535)]
    [int]$ApiPort = 8001
)

# Manual maintenance entry point: verify the expected idle preview processes before
# replacing them. Cloudflare keeps its existing process and public hostname.
$ErrorActionPreference = "Stop"
$projectRoot = Split-Path -Parent $PSScriptRoot
if ($FrontendPort -eq $ApiPort) { throw "Frontend and API ports must differ." }
foreach ($relativePath in @("scripts/start_local.ps1", "runtime/rtmpose/.venv/Scripts/python.exe", "models/yolo26n.pt", "scoring-demo-web/.dev.vars", "scoring-demo-web/dist/server/index.js", "scoring-demo-web/node_modules/vinext/dist/cli.js")) {
    if (-not (Test-Path -LiteralPath (Join-Path $projectRoot $relativePath) -PathType Leaf)) {
        throw "Required file is missing: $relativePath. No service was stopped."
    }
}
Get-Command node, npm.cmd -CommandType Application -ErrorAction Stop | Out-Null
$internalKey = ""
foreach ($line in [IO.File]::ReadAllLines((Join-Path $projectRoot "scoring-demo-web/.dev.vars"))) {
    if ($line -match '^\s*RALLYMATE_API_KEY\s*=\s*(.*?)\s*$') { $internalKey = $Matches[1].Trim().Trim('"').Trim("'") }
}
if ($internalKey.Length -lt 32 -or $internalKey -notmatch '^[A-Za-z0-9_-]+$') {
    throw "The internal API key is missing or invalid. No service was stopped."
}

$processesToStop = @()
foreach ($port in @($FrontendPort, $ApiPort)) {
    $listeners = @(Get-NetTCPConnection -LocalPort $port -State Listen -ErrorAction SilentlyContinue)
    foreach ($listener in $listeners) {
        if ($listener.LocalAddress -ne "127.0.0.1") { throw "Port $port is not loopback-only. No service was stopped." }
        $serviceProcess = Get-CimInstance Win32_Process -Filter "ProcessId = $($listener.OwningProcess)"
        $expected = if ($port -eq $FrontendPort) { 'vinext[\\/]dist[\\/]cli\.js\s+start' } else { 'scripts[\\/]run_dev_service\.py' }
        if (-not $serviceProcess -or $serviceProcess.CommandLine -notmatch $expected -or $serviceProcess.CommandLine -notmatch "--port\s+$port(?:\s|$)") {
            throw "Port $port belongs to an unexpected process. No service was stopped."
        }
        $processesToStop += $serviceProcess.ProcessId
    }
}

# Refuse a restart if the latest health check shows an active analysis. Pause
# new submissions during maintenance; this check is not a queue drain lock.
# A failed/unreadable health response also prevents a restart.
if (Get-NetTCPConnection -LocalPort $ApiPort -State Listen -ErrorAction SilentlyContinue) {
    $response = $null
    $reader = $null
    try {
        $request = [Net.HttpWebRequest]::Create("http://127.0.0.1:$ApiPort/health/ready")
        $request.Proxy = $null
        $request.AllowAutoRedirect = $false
        $request.Timeout = 5000
        $request.ReadWriteTimeout = 5000
        $request.Headers["Authorization"] = "Bearer $internalKey"
        $response = $request.GetResponse()
        $reader = New-Object IO.StreamReader($response.GetResponseStream())
        $health = $reader.ReadToEnd() | ConvertFrom-Json -ErrorAction Stop
        if ($health.status -ne "ready" -or $null -eq $health.queue) { throw "API is not ready." }
        if ([int]$health.queue.running -gt 0 -or [int]$health.queue.queued -gt 0) { throw "An analysis is active or queued." }
    } catch {
        throw "Cannot safely restart the API: it is busy or its health could not be verified. No service was stopped."
    } finally {
        if ($null -ne $reader) { $reader.Dispose() }
        if ($null -ne $response) { $response.Close() }
        $internalKey = ""
    }
}

foreach ($processIdToStop in ($processesToStop | Select-Object -Unique)) {
    Stop-Process -Id $processIdToStop -ErrorAction Stop
    Wait-Process -Id $processIdToStop -Timeout 10 -ErrorAction SilentlyContinue
}
& (Join-Path $PSScriptRoot "start_local.ps1") -FrontendPort $FrontendPort -ApiPort $ApiPort
Write-Host "Preview processes launched; allow the API a few seconds to become ready. Keep the existing Cloudflare terminal open."
Write-Host "Local preview: http://127.0.0.1:$FrontendPort"
