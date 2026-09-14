param(
    [int]$Port = 8000,
    [string]$Device = "0",
    [ValidateSet("rtmpose-m-halpe26-online", "rtmpose-m-halpe26-analysis", "rtmpose-l-halpe26-analysis-shadow", "rtmpose-m-wholebody133-analysis", "yolo-baseline")]
    [string]$PosePreset = "rtmpose-m-halpe26-online",
    [string]$BasePython = ""
)

$ErrorActionPreference = "Stop"
$projectRoot = Split-Path -Parent $PSScriptRoot

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

$rtmposePython = Join-Path $projectRoot "runtime\rtmpose\.venv\Scripts\python.exe"
if ($PosePreset -eq "yolo-baseline") {
    $pythonExe = Resolve-PythonExecutable -ProjectRoot $projectRoot -RequestedPython $BasePython
} else {
    if (-not (Test-Path -LiteralPath $rtmposePython)) {
        $PrepareScript = Join-Path $projectRoot "scripts\prepare_rtmpose_runtime.ps1"
        if ([string]::IsNullOrWhiteSpace($BasePython)) {
            & $PrepareScript
        } else {
            & $PrepareScript -BasePython $BasePython
        }
        if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
    }
    $pythonExe = $rtmposePython
}
if (-not (Test-Path -LiteralPath $pythonExe)) {
    throw "Pose runtime Python is missing: $pythonExe"
}

$env:RALLYMATE_DEVICE = $Device
$env:RALLYMATE_POSE_PRESET = $PosePreset
# This script is a local-development launcher.  Keep the production default
# strict, but make browser preflight work for the local Vite/vinext ports when
# the operator has not supplied an explicit CORS policy.  An explicit value is
# always preserved so callers can use a narrower origin list.
if ([string]::IsNullOrWhiteSpace($env:RALLYMATE_CORS_ORIGINS)) {
    $env:RALLYMATE_CORS_ORIGINS = "http://localhost:3000,http://127.0.0.1:3000,http://localhost:5173,http://127.0.0.1:5173"
}
if ($PosePreset -ne "yolo-baseline") {
    $env:RALLYMATE_MODEL_LICENSE_ACK = "alternative-backend"
}
Set-Location -LiteralPath $projectRoot
& $pythonExe -m pip install -e ".[service]"
if ($LASTEXITCODE -ne 0) {
    exit $LASTEXITCODE
}
& $pythonExe -c "from rallymate_service.cli import dev_main; import sys; sys.argv=['rallymate-dev','--port','$Port']; dev_main()"
