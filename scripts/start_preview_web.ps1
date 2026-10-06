param(
    [ValidateRange(1024, 65535)]
    [int]$FrontendPort = 8003,
    [ValidateRange(1024, 65535)]
    [int]$ApiPort = 8001
)

# Run this launcher manually in PowerShell. It starts only the built frontend;
# the existing API and GPU worker continue in their own process.
$ErrorActionPreference = "Stop"
$projectRoot = Split-Path -Parent $PSScriptRoot
$webRoot = Join-Path $projectRoot "scoring-demo-web"
if ($FrontendPort -eq $ApiPort) { throw "Frontend and API ports must differ." }
foreach ($relativePath in @(".dev.vars", "dist/server/index.js", "node_modules/vinext/dist/cli.js")) {
    if (-not (Test-Path -LiteralPath (Join-Path $webRoot $relativePath) -PathType Leaf)) {
        throw "Required frontend file is missing: $relativePath"
    }
}
if (Get-NetTCPConnection -LocalPort $FrontendPort -State Listen -ErrorAction SilentlyContinue) {
    Write-Host "Port $FrontendPort is already in use. Do not start another frontend."
    return
}
if (-not (Get-NetTCPConnection -LocalPort $ApiPort -State Listen -ErrorAction SilentlyContinue)) {
    throw "The API is not listening on port $ApiPort. Start the complete local service first."
}
$nodeExecutable = (Get-Command node -CommandType Application -ErrorAction Stop | Select-Object -First 1).Source
$internalKey = ""
foreach ($line in [IO.File]::ReadAllLines((Join-Path $webRoot ".dev.vars"))) {
    if ($line -match '^\s*RALLYMATE_API_KEY\s*=\s*(.*?)\s*$') { $internalKey = $Matches[1].Trim().Trim('"').Trim("'") }
}
if ($internalKey.Length -lt 32 -or $internalKey -notmatch '^[A-Za-z0-9_-]+$') {
    throw "Set the shared internal API key in scoring-demo-web/.dev.vars first."
}
$env:RALLYMATE_API_KEY = $internalKey
$env:RALLYMATE_API_ORIGIN = "http://127.0.0.1:$ApiPort"
$env:RALLYMATE_LOCAL_TUNNEL = "1"
$env:MIMO_ADVICE_ENABLED = "0"

Push-Location $webRoot
try {
    Write-Host "Starting http://127.0.0.1:$FrontendPort - keep this terminal open."
    & $nodeExecutable --env-file=.dev.vars node_modules/vinext/dist/cli.js start --hostname 127.0.0.1 --port $FrontendPort
    if ($LASTEXITCODE -ne 0) { throw "Frontend exited with code $LASTEXITCODE." }
} finally {
    Pop-Location
}
