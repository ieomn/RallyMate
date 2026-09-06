param(
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

$BasePython = Resolve-PythonExecutable -ProjectRoot $projectRoot -RequestedPython $BasePython
$runtimeRoot = Join-Path $projectRoot "runtime\rtmpose"
$venvRoot = Join-Path $runtimeRoot ".venv"
$venvPython = Join-Path $venvRoot "Scripts\python.exe"

if (-not (Test-Path -LiteralPath $venvPython)) {
    & $BasePython -m venv $venvRoot --system-site-packages
    if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
}

# MMPose 1.3.x native extensions require the NumPy 1.x ABI. This override is
# local to the RTMPose venv; the selected base environment remains unchanged.
& $venvPython -m pip install --disable-pip-version-check --ignore-installed "numpy==1.26.4"
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
& $venvPython -m pip install --disable-pip-version-check `
    "mmengine==0.10.7" "mmcv-lite==2.1.0" "mmpose==1.3.2" "mmdet==3.3.0"
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }

& $venvPython -c "import torch,numpy,mmengine,mmcv,mmpose,mmdet; print({'torch':torch.__version__,'cuda':torch.cuda.is_available(),'numpy':numpy.__version__,'mmengine':mmengine.__version__,'mmcv':mmcv.__version__,'mmpose':mmpose.__version__,'mmdet':mmdet.__version__})"
exit $LASTEXITCODE
