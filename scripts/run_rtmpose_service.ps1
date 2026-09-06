param(
    [ValidateSet("Online", "Analysis", "Shadow", "WholeBody")]
    [string]$Profile = "Online",
    [int]$Port = 8000
)

$ErrorActionPreference = "Stop"
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
& $Python -c "from rallymate_service.cli import dev_main; import sys; sys.argv=['rallymate-dev','--port','$Port']; dev_main()"
exit $LASTEXITCODE
