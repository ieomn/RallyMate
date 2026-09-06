param(
    [string]$Python = ""
)

$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $PSScriptRoot

if ([string]::IsNullOrWhiteSpace($Python)) {
    $ProjectPython = Join-Path $Root ".venv\Scripts\python.exe"
    if (Test-Path -LiteralPath $ProjectPython -PathType Leaf) {
        $Python = (Resolve-Path -LiteralPath $ProjectPython).Path
    } else {
        foreach ($CommandName in @("python", "py")) {
            $PythonCommand = Get-Command $CommandName -CommandType Application -ErrorAction SilentlyContinue |
                Select-Object -First 1
            if ($PythonCommand) {
                $Python = $PythonCommand.Path
                break
            }
        }
    }
} elseif (Test-Path -LiteralPath $Python -PathType Leaf) {
    $Python = (Resolve-Path -LiteralPath $Python).Path
} else {
    $PythonCommand = Get-Command $Python -CommandType Application -ErrorAction SilentlyContinue |
        Select-Object -First 1
    if (-not $PythonCommand) {
        throw "Requested Python executable was not found: $Python"
    }
    $Python = $PythonCommand.Path
}
if ([string]::IsNullOrWhiteSpace($Python)) {
    throw "Python was not found. Create .venv or pass -Python explicitly."
}

& $Python -m pip install --upgrade torch torchvision `
    --index-url https://download.pytorch.org/whl/cu128
if ($LASTEXITCODE -ne 0) {
    exit $LASTEXITCODE
}
& $Python -c "import torch; print('torch', torch.__version__); print('cuda', torch.cuda.is_available()); print(torch.cuda.get_device_name(0) if torch.cuda.is_available() else 'CPU fallback')"
exit $LASTEXITCODE
