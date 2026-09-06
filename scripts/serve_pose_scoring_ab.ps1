param(
    [int]$Port = 8765,
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

$ReportRoot = Join-Path $Root "reports\pose-scoring-ab"
$Url = "http://127.0.0.1:$Port/index.html"
$Listener = Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue
if ($Listener) {
    throw "Port $Port is already in use; refusing to reuse an unverified listener. Choose another -Port or stop the existing service."
}

$Arguments = @(
    (Join-Path $PSScriptRoot "range_http_server.py"),
    "--port",
    "$Port",
    "--bind",
    "127.0.0.1",
    "--directory",
    ('"' + $ReportRoot + '"'),
    "--allow",
    "."
)
$Process = Start-Process -FilePath $Python -ArgumentList $Arguments -WindowStyle Hidden -PassThru
$Ready = $false
for ($Attempt = 0; $Attempt -lt 30; $Attempt++) {
    $ActiveListener = Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue
    if ($ActiveListener -and $ActiveListener.OwningProcess -ne $Process.Id) {
        Stop-Process -Id $Process.Id -Force -ErrorAction SilentlyContinue
        throw "Port $Port was claimed by an unexpected process; refusing to trust the listener."
    }
    try {
        $Response = Invoke-WebRequest -Uri $Url -UseBasicParsing -TimeoutSec 1
        if (
            $Response.StatusCode -eq 200 -and
            $Response.Headers["X-RallyMate-Range-Server"] -eq "allowlist-v1" -and
            $ActiveListener.OwningProcess -eq $Process.Id
        ) {
            $Ready = $true
            break
        }
    } catch {
        Start-Sleep -Milliseconds 100
    }
}
if (-not $Ready) {
    Stop-Process -Id $Process.Id -Force -ErrorAction SilentlyContinue
    throw "Verified local report server did not start (PID $($Process.Id))"
}

Write-Output $Url
