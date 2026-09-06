param(
    [int]$Port = 8000,
    [ValidateSet("rtmpose-m-halpe26-online", "rtmpose-m-halpe26-analysis", "rtmpose-l-halpe26-analysis-shadow", "rtmpose-m-wholebody133-analysis", "yolo-baseline")]
    [string]$PosePreset = "rtmpose-m-halpe26-online",
    [string]$BasePython = ""
)

$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $PSScriptRoot

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
if (-not (Test-Path -LiteralPath $pythonExe)) {
    throw "Pose runtime Python is missing: $pythonExe"
}

# The API snapshots this preset into every generated request. A separately
# launched worker must receive the same -PosePreset value.
$env:PYTHONPATH = Join-Path $Root "src"
$env:RALLYMATE_POSE_PRESET = $PosePreset
Push-Location -LiteralPath $Root
try {
    & $pythonExe -m pip install -e ".[service]" | Out-Host
    if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
    & $pythonExe -c "from rallymate_service.cli import api_main; import sys; sys.argv=['rallymate-api','--port','$Port']; api_main()"
    exit $LASTEXITCODE
} finally {
    Pop-Location
}
