param(
    [switch]$Once,
    [ValidateSet("rtmpose-m-halpe26-online", "rtmpose-m-halpe26-analysis", "rtmpose-l-halpe26-analysis-shadow", "rtmpose-m-wholebody133-analysis", "yolo-baseline")]
    [string]$PosePreset = "rtmpose-m-halpe26-online",
    [int]$CpuThreads,
    [string]$BasePython = ""
)

$ErrorActionPreference = "Stop"

function Resolve-PythonExecutable {
    param(
        [string]$ProjectRoot,
        [string]$RequestedPython = ""
    )

    if (-not [string]::IsNullOrWhiteSpace($RequestedPython)) {
        if (Test-Path -LiteralPath $RequestedPython -PathType Leaf) {
            return (Resolve-Path -LiteralPath $RequestedPython).Path
        }
        $RequestedCommand = Get-Command $RequestedPython -CommandType Application -ErrorAction SilentlyContinue |
            Select-Object -First 1
        if ($RequestedCommand) {
            return $RequestedCommand.Path
        }
        throw "Requested Python executable was not found: $RequestedPython"
    }

    $ProjectPython = Join-Path $ProjectRoot ".venv\Scripts\python.exe"
    if (Test-Path -LiteralPath $ProjectPython -PathType Leaf) {
        return (Resolve-Path -LiteralPath $ProjectPython).Path
    }
    foreach ($CommandName in @("python", "py")) {
        $PythonCommand = Get-Command $CommandName -CommandType Application -ErrorAction SilentlyContinue |
            Select-Object -First 1
        if ($PythonCommand) {
            return $PythonCommand.Path
        }
    }
    throw "Python was not found. Create .venv or pass -BasePython explicitly."
}

$resolvedCpuThreads = 4
if ($PSBoundParameters.ContainsKey("CpuThreads")) {
    if ($CpuThreads -lt 1) {
        throw "-CpuThreads must be an integer >= 1; got '$CpuThreads'"
    }
    $resolvedCpuThreads = $CpuThreads
} elseif (-not [string]::IsNullOrWhiteSpace($env:RALLYMATE_CPU_THREADS)) {
    $parsedCpuThreads = 0
    if (
        -not [int]::TryParse(
            $env:RALLYMATE_CPU_THREADS,
            [ref]$parsedCpuThreads
        ) -or $parsedCpuThreads -lt 1
    ) {
        throw "RALLYMATE_CPU_THREADS must be an integer >= 1; got '$($env:RALLYMATE_CPU_THREADS)'"
    }
    $resolvedCpuThreads = $parsedCpuThreads
}
$cpuThreadValue = [string]$resolvedCpuThreads
$env:RALLYMATE_CPU_THREADS = $cpuThreadValue
$env:OMP_NUM_THREADS = $cpuThreadValue
$env:OMP_THREAD_LIMIT = $cpuThreadValue
$env:MKL_NUM_THREADS = $cpuThreadValue
$env:OPENBLAS_NUM_THREADS = $cpuThreadValue
$env:NUMEXPR_NUM_THREADS = $cpuThreadValue
$env:OPENCV_FOR_THREADS_NUM = $cpuThreadValue

$Root = Split-Path -Parent $PSScriptRoot
if ($PosePreset -eq "yolo-baseline") {
    $pythonExe = Resolve-PythonExecutable -ProjectRoot $Root -RequestedPython $BasePython
} else {
    $pythonExe = Join-Path $Root "runtime\rtmpose\.venv\Scripts\python.exe"
    if (-not (Test-Path -LiteralPath $pythonExe)) {
        $PrepareScript = Join-Path $Root "scripts\prepare_rtmpose_runtime.ps1"
        if ([string]::IsNullOrWhiteSpace($BasePython)) {
            & $PrepareScript
        } else {
            & $PrepareScript -BasePython $BasePython
        }
        if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
    }
    $env:RALLYMATE_MODEL_LICENSE_ACK = "alternative-backend"
}
$env:RALLYMATE_POSE_PRESET = $PosePreset
$env:PYTHONPATH = Join-Path $Root "src"
Push-Location -LiteralPath $Root
try {
    & $pythonExe -m pip install -e ".[service]" | Out-Host
    if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
    if ($Once) {
        & $pythonExe -c "from rallymate_service.cli import worker_main; import sys; sys.argv=['rallymate-worker','--once']; worker_main()"
    } else {
        & $pythonExe -c "from rallymate_service.cli import worker_main; worker_main()"
    }
    exit $LASTEXITCODE
} finally {
    Pop-Location
}
