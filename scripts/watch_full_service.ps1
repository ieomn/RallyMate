param(
    [int]$Lines = 6,
    [int]$IntervalSeconds = 2,
    [switch]$LogsOnly,
    [switch]$IncludeLogs,
    [switch]$Once
)

$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $PSScriptRoot
$Python = Join-Path $Root "runtime\rtmpose\.venv\Scripts\python.exe"
if (-not (Test-Path -LiteralPath $Python -PathType Leaf)) {
    $Python = (Get-Command python -CommandType Application | Select-Object -First 1).Source
}
if (-not $Python) { throw "Python executable was not found." }
$Arguments = @(
    (Join-Path $PSScriptRoot "watch_full_service.py"),
    "--data-root", (Join-Path $Root "service_data"),
    "--log-root", (Join-Path $Root ".codex_tmp\full-service"),
    "--interval", ([Math]::Max(1, $IntervalSeconds)),
    "--lines", ([Math]::Max(1, $Lines))
)
if ($LogsOnly) { $Arguments += "--logs-only" }
if ($IncludeLogs) { $Arguments += "--logs" }
if ($Once) { $Arguments += "--once" }
if (-not $Once) {
    try {
        $Host.UI.RawUI.WindowTitle = "RallyMate - Live Upload and Analysis"
        $maximumSize = $Host.UI.RawUI.LargestWindowSize
        $windowSize = $Host.UI.RawUI.WindowSize
        $windowSize.Width = [Math]::Min(130, $maximumSize.Width)
        $windowSize.Height = [Math]::Min(48, $maximumSize.Height)
        $bufferSize = $Host.UI.RawUI.BufferSize
        $bufferSize.Width = [Math]::Max($bufferSize.Width, $windowSize.Width)
        $bufferSize.Height = [Math]::Max($bufferSize.Height, $windowSize.Height)
        $Host.UI.RawUI.BufferSize = $bufferSize
        $Host.UI.RawUI.WindowSize = $windowSize
    } catch {
        # Embedded terminals may own their size; monitoring still works.
    }
}
# This process reads status only. Closing it never stops the service processes.
& $Python @Arguments
