param(
    [int]$FrontendPort = 8000,
    [int]$ApiPort = 8001,
    [string]$Device = "0",
    [switch]$Build
)

$ErrorActionPreference = "Stop"
$projectRoot = Split-Path -Parent $PSScriptRoot
$webRoot = Join-Path $projectRoot "scoring-demo-web"
$pythonExe = Join-Path $projectRoot "runtime\rtmpose\.venv\Scripts\python.exe"
$varsFile = Join-Path $webRoot ".dev.vars"
if (-not (Test-Path -LiteralPath $pythonExe -PathType Leaf)) {
    throw "Prepare runtime/rtmpose first. This launcher does not install or replace model dependencies."
}
if ($FrontendPort -eq $ApiPort -or $FrontendPort -lt 1024 -or $ApiPort -lt 1024 -or $FrontendPort -gt 65535 -or $ApiPort -gt 65535) {
    throw "Choose different frontend/API ports between 1024 and 65535."
}
$occupied = @(Get-NetTCPConnection -State Listen -ErrorAction SilentlyContinue |
    Where-Object { $_.LocalPort -in @($FrontendPort, $ApiPort) })
if ($occupied.Count -gt 0) {
    throw "A selected local port is already in use. Inspect the existing service before starting another GPU worker."
}
if (-not (Test-Path -LiteralPath $varsFile -PathType Leaf)) {
    throw "Create scoring-demo-web/.dev.vars from the documented local configuration first."
}

# Read only the shared internal key. All other local routing settings below
# override inherited cloud configuration and Node's optional env file values.
$apiKey = ""
foreach ($line in [System.IO.File]::ReadAllLines($varsFile)) {
    if ($line -match '^\s*RALLYMATE_API_KEY\s*=\s*(.*?)\s*$') {
        $apiKey = $Matches[1].Trim().Trim('"').Trim("'")
    }
}
if ($apiKey.Length -lt 32 -or $apiKey -notmatch '^[A-Za-z0-9_-]+$') {
    throw "Set a random URL-safe RALLYMATE_API_KEY of at least 32 characters in .dev.vars. The API and web process share it internally."
}
$env:PYTHONPATH = Join-Path $projectRoot "src"
$env:RALLYMATE_API_KEY = $apiKey
$env:RALLYMATE_API_ORIGIN = "http://127.0.0.1:$ApiPort"
$env:RALLYMATE_API_PROXY = $env:RALLYMATE_API_ORIGIN
$env:RALLYMATE_DATA_ROOT = Join-Path $projectRoot "service_data"
$env:RALLYMATE_DATABASE_PATH = Join-Path $env:RALLYMATE_DATA_ROOT "jobs.sqlite3"
$env:RALLYMATE_DETECT_MODEL = Join-Path $projectRoot "models\yolo26n.pt"
$env:RALLYMATE_ENVIRONMENT = "development"
$env:RALLYMATE_MODEL_LICENSE_ACK = "development"
$env:RALLYMATE_DEVICE = $Device
$env:RALLYMATE_POSE_PRESET = "rtmpose-m-halpe26-online"
$env:RALLYMATE_PUBLIC_BASE_URL = ""
$env:RALLYMATE_CORS_ORIGINS = "http://127.0.0.1:$FrontendPort,http://localhost:$FrontendPort"
$env:RALLYMATE_CPU_THREADS = "4"
foreach ($name in @("OMP_NUM_THREADS", "OMP_THREAD_LIMIT", "MKL_NUM_THREADS", "OPENBLAS_NUM_THREADS", "NUMEXPR_NUM_THREADS", "OPENCV_FOR_THREADS_NUM")) {
    [Environment]::SetEnvironmentVariable($name, "4", "Process")
}
$env:MIMO_ADVICE_ENABLED = "0"
$env:MIMO_ALLOWED_ORIGIN = ""
foreach ($name in @("NEXT_PUBLIC_RALLYMATE_API_URL", "NEXT_PUBLIC_API_BASE_URL", "NEXT_PUBLIC_API_URL", "VITE_RALLYMATE_API_URL", "VITE_API_BASE_URL", "VITE_API_URL")) {
    [Environment]::SetEnvironmentVariable($name, "", "Process")
}
$ffmpegDirectory = Join-Path $projectRoot ".codex_tmp\ffmpeg-env\Library\bin"
if (Test-Path -LiteralPath (Join-Path $ffmpegDirectory "ffmpeg.exe") -PathType Leaf) {
    $env:PATH = "$ffmpegDirectory;$env:PATH"
}
if ($Build) {
    Push-Location $webRoot
    try {
        & npm.cmd run build
        if ($LASTEXITCODE -ne 0) { throw "Frontend build failed; no service was started." }
    } finally { Pop-Location }
}
if (-not (Test-Path -LiteralPath (Join-Path $webRoot "dist") -PathType Container)) {
    throw "Frontend build is missing. Run this launcher with -Build."
}

# The existing development API includes exactly one GPU worker. Cloudflare
# remains opt-in in run_full_service.ps1 and is deliberately omitted here.
$occupied = @(Get-NetTCPConnection -State Listen -ErrorAction SilentlyContinue |
    Where-Object { $_.LocalPort -in @($FrontendPort, $ApiPort) })
if ($occupied.Count -gt 0) {
    throw "A selected local port became occupied during preparation. Inspect it before starting the service."
}
& (Join-Path $PSScriptRoot "run_full_service.ps1") -FrontendHost "127.0.0.1" -FrontendPort $FrontendPort -ApiPort $ApiPort
