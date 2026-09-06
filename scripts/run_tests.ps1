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
$env:PYTHONPATH = Join-Path $Root "src"
& $Python -c "import jsonschema"
if ($LASTEXITCODE -ne 0) {
    throw "Missing required test dependency 'jsonschema'. Install the dev extras with: $Python -m pip install -e '$Root[dev]'"
}
& $Python -m compileall -q (Join-Path $Root "src") (Join-Path $Root "tests")
if ($LASTEXITCODE -ne 0) {
    exit $LASTEXITCODE
}
& $Python -m unittest discover -s (Join-Path $Root "tests") -v
exit $LASTEXITCODE
