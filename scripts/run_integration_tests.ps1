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
& $Python (Join-Path $Root "scripts\download_assets.py") --root $Root
if ($LASTEXITCODE -ne 0) {
    exit $LASTEXITCODE
}

$Requests = @(
    (Join-Path $Root "examples\request.json"),
    (Join-Path $Root "examples\request-match-auto.json"),
    (Join-Path $Root "examples\request-match-manual.json")
)
$Outputs = @(
    (Join-Path $Root "runs\serve-demo"),
    (Join-Path $Root "runs\match-auto"),
    (Join-Path $Root "runs\match-manual")
)

for ($Index = 0; $Index -lt $Requests.Count; $Index++) {
    & $Python -m rallymate_vision --request $Requests[$Index]
    if ($LASTEXITCODE -ne 0) {
        exit $LASTEXITCODE
    }
    & $Python (Join-Path $Root "scripts\validate_run.py") $Outputs[$Index]
    if ($LASTEXITCODE -ne 0) {
        exit $LASTEXITCODE
    }
}

& $Python (Join-Path $Root "scripts\integration_service.py")
if ($LASTEXITCODE -ne 0) {
    exit $LASTEXITCODE
}

Write-Host "integration tests passed"
