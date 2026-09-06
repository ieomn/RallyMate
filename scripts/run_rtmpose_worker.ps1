param(
    [ValidateSet("Online", "Analysis", "Shadow", "WholeBody")]
    [string]$Profile = "Online",
    [switch]$Once,
    [int]$CpuThreads
)

$ErrorActionPreference = "Stop"
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
$Python = Join-Path $Root "runtime\rtmpose\.venv\Scripts\python.exe"
if (-not (Test-Path -LiteralPath $Python)) {
    throw "RTMPose runtime is missing; run scripts\prepare_rtmpose_runtime.ps1"
}

$env:PYTHONPATH = Join-Path $Root "src"
$env:RALLYMATE_POSE_PRESET = switch ($Profile) {
    "Online" { "rtmpose-m-halpe26-online" }
    "Analysis" { "rtmpose-m-halpe26-analysis" }
    "Shadow" { "rtmpose-l-halpe26-analysis-shadow" }
    "WholeBody" { "rtmpose-m-wholebody133-analysis" }
}
$env:RALLYMATE_MODEL_LICENSE_ACK = "alternative-backend"
$Arguments = @("-c", "from rallymate_service.cli import worker_main; import sys; sys.argv=['rallymate-worker']; worker_main()")
if ($Once) {
    $Arguments = @("-c", "from rallymate_service.cli import worker_main; import sys; sys.argv=['rallymate-worker','--once']; worker_main()")
}
& $Python @Arguments
exit $LASTEXITCODE
