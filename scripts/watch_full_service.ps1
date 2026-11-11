param(
    [ValidateRange(1, 100)]
    [int]$Lines = 6,
    [ValidateRange(1, 3600)]
    [int]$IntervalSeconds = 2,
    [switch]$LogsOnly,
    [switch]$IncludeLogs,
    [switch]$Once,
    [string]$Interface = "",
    [string]$RecordPath = "",
    [string]$DataRoot = ""
)

$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $PSScriptRoot
$Python = Join-Path $Root "runtime\rtmpose\.venv\Scripts\python.exe"
if (-not (Test-Path -LiteralPath $Python -PathType Leaf)) {
    $PythonCommand = Get-Command python -CommandType Application -ErrorAction SilentlyContinue | Select-Object -First 1
    $Python = if ($PythonCommand) { $PythonCommand.Source } else { $null }
}
if (-not $Python) { throw "Python executable was not found." }
$Arguments = @(
    "-u", "-X", "utf8",
    (Join-Path $PSScriptRoot "watch_full_service.py"),
    "--data-root", $(if ($DataRoot) { $DataRoot } else { Join-Path $Root "service_data" }),
    "--log-root", (Join-Path $Root ".codex_tmp\full-service"),
    "--interval", $IntervalSeconds,
    "--lines", $Lines
)
if ($LogsOnly) { $Arguments += "--logs-only" }
if ($IncludeLogs) { $Arguments += "--logs" }
if ($Once) { $Arguments += "--once" }
if ($Interface) { $Arguments += @("--interface", $Interface) }
if ($RecordPath) { $Arguments += @("--record", $RecordPath) }
$PreviousTitle = $null
if (-not $Once -and -not [Console]::IsOutputRedirected) {
    try {
        $PreviousTitle = $Host.UI.RawUI.WindowTitle
        $Host.UI.RawUI.WindowTitle = "RallyMate - Live Upload and Analysis"
    } catch {
        # Embedded terminals may own their title; Python handles rendering.
    }
}
# Keep the user's window and scrollback sizes intact. Python chooses fixed-screen
# rendering, or a single plain snapshot if stdout is redirected / noninteractive.
# This process reads status only. Closing it never stops the service processes.
try {
    & $Python @Arguments
} finally {
    if ($null -ne $PreviousTitle) {
        try { $Host.UI.RawUI.WindowTitle = $PreviousTitle } catch { }
    }
}
