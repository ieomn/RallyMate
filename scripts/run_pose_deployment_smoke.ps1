param(
    [ValidateSet("Online", "Analysis", "WholeBody")]
    [string]$Profile = "Online"
)

$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $PSScriptRoot
$Python = Join-Path $Root "runtime\rtmpose\.venv\Scripts\python.exe"
if (-not (Test-Path -LiteralPath $Python)) {
    throw "RTMPose runtime is missing; run scripts\prepare_rtmpose_runtime.ps1"
}

switch ($Profile) {
    "Online" {
        $Preset = "rtmpose-m-halpe26-online"
        $Request = Join-Path $Root "examples\request-rtmpose-online-smoke.json"
    }
    "Analysis" {
        $Preset = "rtmpose-m-halpe26-analysis"
        $Request = Join-Path $Root "examples\request-rtmpose-analysis-smoke.json"
    }
    "WholeBody" {
        $Preset = "rtmpose-m-wholebody133-analysis"
        $Request = Join-Path $Root "examples\request-rtmpose-wholebody133-analysis-smoke.json"
    }
}

$env:PYTHONPATH = Join-Path $Root "src"
$env:RALLYMATE_POSE_PRESET = $Preset
$env:RALLYMATE_MODEL_LICENSE_ACK = "alternative-backend"
$Registry = Join-Path $Root "metric-feasibility-pose-wave-v2.json"
$env:RALLYMATE_SCORING_FEASIBILITY_REGISTRY = $Registry
& $Python (Join-Path $Root "scripts\smoke_pose_deployment_profile.py") `
    --request $Request `
    --preset $Preset `
    --registry $Registry
exit $LASTEXITCODE
