param(
    [int]$FrontendPort = 8000,
    [int]$ApiPort = 8001,
    [string]$Device = "0",
    [ValidateSet("sam3d-dinov3", "sam3d-vith", "rtmpose")]
    [string]$Model = "sam3d-dinov3",
    [string]$DataRoot = "",
    [ValidateRange(1, 64)]
    [int]$CpuThreads = 4,
    [ValidateRange(1, 64)]
    [int]$SamCpuThreads = 1,
    [ValidateSet("runtime_default", "passive")]
    [string]$SamWaitMode = "passive",
    [switch]$Build
)

$ErrorActionPreference = "Stop"
if ($Model -eq "sam3d-dinov3" -and -not $PSBoundParameters.ContainsKey("CpuThreads")) {
    $CpuThreads = 2
}
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
$env:RALLYMATE_DATA_ROOT = if ($DataRoot) { [System.IO.Path]::GetFullPath($DataRoot) } else { Join-Path $projectRoot "service_data" }
$env:RALLYMATE_DATABASE_PATH = Join-Path $env:RALLYMATE_DATA_ROOT "jobs.sqlite3"
$env:RALLYMATE_DETECT_MODEL = Join-Path $projectRoot "models\yolo26n.pt"
$env:RALLYMATE_ENVIRONMENT = "development"
$env:RALLYMATE_MODEL_LICENSE_ACK = "development"
$env:RALLYMATE_DEVICE = $Device
if ($Model -eq "rtmpose") {
    $env:RALLYMATE_POSE_PRESET = "rtmpose-m-halpe26-online"
} else {
    # A preset takes priority over explicit model settings. SAM has no registry
    # preset, so clear any inherited RTMPose preset before selecting it.
    Remove-Item Env:RALLYMATE_POSE_PRESET -ErrorAction SilentlyContinue
    $samRuntime = Join-Path $projectRoot "runtime\sam3d"
    $checkpointName = if ($Model -eq "sam3d-vith") { "sam-3d-body-vith" } else { "sam-3d-body-dinov3" }
    $checkpointRoot = Join-Path $samRuntime "checkpoints\$checkpointName"
    $env:RALLYMATE_SAM3D_PYTHON = Join-Path $samRuntime ".venv311\Scripts\python.exe"
    $env:RALLYMATE_POSE_MODEL = Join-Path $checkpointRoot "model.ckpt"
    $env:RALLYMATE_POSE_CONFIG = Join-Path $checkpointRoot "model_config.yaml"
    foreach ($requiredPath in @($env:RALLYMATE_SAM3D_PYTHON, $env:RALLYMATE_POSE_MODEL, $env:RALLYMATE_POSE_CONFIG, (Join-Path $checkpointRoot "assets\mhr_model.pt"))) {
        if (-not (Test-Path -LiteralPath $requiredPath -PathType Leaf)) {
            throw "The selected SAM model is incomplete: $requiredPath. No service was started."
        }
    }
    $env:RALLYMATE_POSE_BACKEND = "sam3d"
    $env:RALLYMATE_POSE_RUNTIME = "pytorch"
    $env:RALLYMATE_POSE_PROFILE = "analysis"
    $env:RALLYMATE_POSE_NATIVE_KEYPOINT_FORMAT = "mhr70"
    $env:RALLYMATE_SAM3D_MODE = "body"
    $env:RALLYMATE_SAM3D_RUNTIME = $samRuntime
    if ($Model -eq "sam3d-dinov3") {
        # This Windows DINOv3 profile is checked with the original pixels,
        # full official mesh and unchanged scores. Keep explicit overrides
        # available for reproducible baseline comparisons.
        $samDecoderGraphDefault = "1"
        if ($env:RALLYMATE_SAM3D_CUDA_GRAPH -eq "0" -or $env:RALLYMATE_SAM3D_VECTORIZE_ROTATIONS -eq "0") {
            $samDecoderGraphDefault = "0"
        }
        $samOutputTransferDefault = "1"
        if ($env:RALLYMATE_SAM3D_PROFILE_STAGES -eq "1" -or $env:RALLYMATE_SAM3D_REUSE_CUDA_CACHE -eq "1") {
            $samOutputTransferDefault = "0"
        }
        $samDefaults = @{
            RALLYMATE_SAM3D_IPC_TRANSPORT = "shared_memory"
            RALLYMATE_SAM3D_GPU_PREPROCESS = "1"
            RALLYMATE_SAM3D_FORWARD_GRAPH = "backbone_decoder_layers"
            RALLYMATE_SAM3D_PREFETCH_FRAMES = "2"
            RALLYMATE_SAM3D_COMPLETE_DECODER_GRAPH = $samDecoderGraphDefault
            RALLYMATE_SAM3D_BATCH_OUTPUT_TRANSFER = $samOutputTransferDefault
            RALLYMATE_SAM3D_ASYNC_MESH_ARCHIVE = "1"
        }
        foreach ($name in $samDefaults.Keys) {
            if ([string]::IsNullOrWhiteSpace([Environment]::GetEnvironmentVariable($name, "Process"))) {
                [Environment]::SetEnvironmentVariable($name, $samDefaults[$name], "Process")
            }
        }
        if ($Device -in @("0", "cuda", "cuda:0") -and
            [string]::IsNullOrWhiteSpace($env:RALLYMATE_SAM3D_CUDA_WAIT_MODE)) {
            # The isolated worker verifies that device 0 is the sole visible
            # device and refuses to modify an already active CUDA context.
            $env:RALLYMATE_SAM3D_CUDA_WAIT_MODE = "blocking_sync"
        }
    }
}
$env:RALLYMATE_PUBLIC_BASE_URL = ""
$env:RALLYMATE_CORS_ORIGINS = "http://127.0.0.1:$FrontendPort,http://localhost:$FrontendPort"
$env:RALLYMATE_CPU_THREADS = [string]$CpuThreads
$env:RALLYMATE_SAM3D_CPU_THREADS = [string]$SamCpuThreads
$env:RALLYMATE_SAM3D_WAIT_MODE = $SamWaitMode
foreach ($name in @("OMP_NUM_THREADS", "OMP_THREAD_LIMIT", "MKL_NUM_THREADS", "OPENBLAS_NUM_THREADS", "NUMEXPR_NUM_THREADS", "NUMEXPR_MAX_THREADS")) {
    [Environment]::SetEnvironmentVariable($name, [string]$CpuThreads, "Process")
}
$env:OPENCV_FOR_THREADS_NUM = "1"
# Small CPU transforms alternate with much longer CUDA work. Release native
# workers between transforms instead of spending that interval spinning.
# Explicit settings remain available for controlled performance comparisons.
if ([string]::IsNullOrWhiteSpace($env:OMP_WAIT_POLICY)) { $env:OMP_WAIT_POLICY = "PASSIVE" }
if ([string]::IsNullOrWhiteSpace($env:KMP_BLOCKTIME)) { $env:KMP_BLOCKTIME = "0" }
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
