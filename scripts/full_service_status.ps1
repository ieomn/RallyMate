param(
    [int]$FrontendPort = 8000,
    [int]$ApiPort = 8001
)

$ErrorActionPreference = "SilentlyContinue"
$Root = Split-Path -Parent $PSScriptRoot
$LogRoot = Join-Path $Root ".codex_tmp\full-service"

function Get-Listener([int]$Port) {
    Get-NetTCPConnection -LocalPort $Port -State Listen | Select-Object -First 1
}

function Get-Health([string]$Url) {
    try {
        $response = Invoke-WebRequest -Uri $Url -UseBasicParsing -TimeoutSec 5
        $body = $response.Content.Trim()
        if ($body.Length -gt 240) { $body = $body.Substring(0, 240) + "..." }
        return "HTTP $($response.StatusCode) $body"
    } catch {
        return "DOWN $($_.Exception.Message)"
    }
}

$front = Get-Listener $FrontendPort
$api = Get-Listener $ApiPort
$cloudflared = Get-CimInstance Win32_Process | Where-Object {
    $_.Name -eq "cloudflared.exe" -and $_.CommandLine -match "tunnel"
} | Select-Object -First 1

[pscustomobject]@{
    CheckedAt = Get-Date -Format "yyyy-MM-dd HH:mm:ss"
    Frontend = if ($front) { "LISTEN pid=$($front.OwningProcess) $(Get-Health "http://127.0.0.1:$FrontendPort/")" } else { "DOWN" }
    Api = if ($api) { "LISTEN pid=$($api.OwningProcess) $(Get-Health "http://127.0.0.1:$ApiPort/health/ready")" } else { "DOWN" }
    Cloudflared = if ($cloudflared) { "RUNNING pid=$($cloudflared.ProcessId)" } else { "DOWN" }
    ApiLog = Join-Path $LogRoot "api.err.log"
    WebLog = Join-Path $LogRoot "web.err.log"
    TunnelLog = Join-Path $LogRoot "cloudflared.err.log"
} | Format-List
