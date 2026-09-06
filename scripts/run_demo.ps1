param(
    [string]$Request = "",
    [int]$MaxFrames = 0,
    [string]$Device = "",
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

if (-not $Request) {
    $Request = Join-Path $Root "examples\request.json"
}
$Request = (Resolve-Path -LiteralPath $Request).Path
$RequestPayload = Get-Content -LiteralPath $Request -Raw | ConvertFrom-Json
$PoseBackend = [string]$RequestPayload.models.pose_backend
if ($PoseBackend -eq "rtmpose") {
    $Python = Join-Path $Root "runtime\rtmpose\.venv\Scripts\python.exe"
    if (-not (Test-Path -LiteralPath $Python)) {
        $PrepareScript = Join-Path $Root "scripts\prepare_rtmpose_runtime.ps1"
        if ([string]::IsNullOrWhiteSpace($BasePython)) {
            & $PrepareScript
        } else {
            & $PrepareScript -BasePython $BasePython
        }
        if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
    }
} else {
    $Python = Resolve-PythonExecutable -ProjectRoot $Root -RequestedPython $BasePython
}
if (-not (Test-Path -LiteralPath $Python)) {
    throw "Pose runtime Python is missing: $Python"
}

$env:PYTHONPATH = Join-Path $Root "src"
& $Python (Join-Path $Root "scripts\download_assets.py") --root $Root
if ($LASTEXITCODE -ne 0) {
    exit $LASTEXITCODE
}

$Arguments = @("-m", "rallymate_vision", "--request", $Request)
if ($MaxFrames -gt 0) {
    $Arguments += @("--max-frames", $MaxFrames)
}
if ($Device) {
    $Arguments += @("--device", $Device)
}
& $Python @Arguments
exit $LASTEXITCODE
