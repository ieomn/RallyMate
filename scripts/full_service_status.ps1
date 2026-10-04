param(
    [ValidateRange(1024, 65535)]
    [int]$FrontendPort = 8000,
    [ValidateRange(1024, 65535)]
    [int]$ApiPort = 8001
)

$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $PSScriptRoot
$LogRoot = Join-Path $Root ".codex_tmp\full-service"

function Get-Listener([int]$Port) {
    Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue | Select-Object -First 1
}

function Read-InternalApiKey {
    $varsFile = Join-Path $Root "scoring-demo-web\.dev.vars"
    if (-not (Test-Path -LiteralPath $varsFile -PathType Leaf)) {
        return @{ Status = "KEY_MISSING"; Key = "" }
    }
    try {
        $key = ""
        foreach ($line in [System.IO.File]::ReadAllLines($varsFile)) {
            if ($line -match '^\s*RALLYMATE_API_KEY\s*=\s*(.*?)\s*$') {
                $key = $Matches[1].Trim().Trim('"').Trim("'")
            }
        }
        if ([string]::IsNullOrWhiteSpace($key)) {
            return @{ Status = "KEY_MISSING"; Key = "" }
        }
        if ($key.Length -lt 32 -or $key -notmatch '^[A-Za-z0-9_-]+$') {
            return @{ Status = "KEY_INVALID"; Key = "" }
        }
        return @{ Status = "OK"; Key = $key }
    } catch {
        return @{ Status = "KEY_UNREADABLE"; Key = "" }
    }
}

function Get-LocalHealth([int]$Port, [switch]$Readiness, [string]$ApiKey = "") {
    $response = $null
    $reader = $null
    $path = if ($Readiness) { "/health/ready" } else { "/" }
    $result = [ordered]@{ Status = "DOWN"; HttpStatus = $null }
    if ($Readiness) { $result["Reasons"] = @() }
    try {
        # The credential target is constructed here, never accepted as a URL.
        # HttpWebRequest works in Windows PowerShell 5 and allows us to disable
        # both redirects and proxies before attaching the loopback-only key.
        $request = [System.Net.HttpWebRequest]::Create("http://127.0.0.1:$Port$path")
        $request.Proxy = $null
        $request.AllowAutoRedirect = $false
        $request.Timeout = 5000
        $request.ReadWriteTimeout = 5000
        if ($Readiness -and $ApiKey) {
            $request.Headers["Authorization"] = "Bearer $ApiKey"
        }
        try {
            $response = $request.GetResponse()
        } catch [System.Net.WebException] {
            $response = $_.Exception.Response
            if ($null -eq $response) {
                $result.Status = if ($_.Exception.Status -eq [System.Net.WebExceptionStatus]::Timeout) { "TIMEOUT" } else { "DOWN" }
                return [pscustomobject]$result
            }
        }
        $result.HttpStatus = [int]$response.StatusCode
        if ($result.HttpStatus -eq 401 -or $result.HttpStatus -eq 403) {
            $result.Status = "AUTH_REJECTED"
        } elseif ($result.HttpStatus -ge 300 -and $result.HttpStatus -lt 400) {
            $result.Status = "REDIRECT_BLOCKED"
        } elseif ($result.HttpStatus -ne 200 -and -not ($Readiness -and $result.HttpStatus -eq 503)) {
            $result.Status = "HTTP_ERROR"
        } elseif (-not $Readiness) {
            # Website health needs only the status code, never its HTML body.
            $result.Status = "OK"
        } else {
            $result.Status = "INVALID_RESPONSE"
            $reader = New-Object System.IO.StreamReader($response.GetResponseStream())
            $buffer = New-Object char[] 16385
            $count = $reader.ReadBlock($buffer, 0, $buffer.Length)
            if ($count -gt 0 -and $count -lt $buffer.Length) {
                try {
                    $payload = (-join $buffer[0..($count - 1)]) | ConvertFrom-Json -ErrorAction Stop
                    if ($payload.status -in @("ready", "not_ready")) {
                        $result.Status = $payload.status
                        # Return only bounded diagnostic strings, not queue,
                        # model paths, an arbitrary response body or secrets.
                        $reasons = @()
                        foreach ($reason in @($payload.reasons)) {
                            if ($reason -is [string] -and $reasons.Count -lt 8) {
                                $safeReason = $reason
                                if ($ApiKey) { $safeReason = $safeReason.Replace($ApiKey, "[redacted]") }
                                $reasons += $safeReason.Substring(0, [Math]::Min(300, $safeReason.Length))
                            }
                        }
                        $result.Reasons = $reasons
                    }
                } catch {
                    $result.Status = "INVALID_RESPONSE"
                }
            }
        }
    } catch {
        # Exception text may contain request details. Report no raw exception.
        $result.Status = "CHECK_FAILED"
    } finally {
        if ($null -ne $reader) { $reader.Dispose() }
        if ($null -ne $response) { $response.Close() }
    }
    return [pscustomobject]$result
}

$front = Get-Listener $FrontendPort
$api = Get-Listener $ApiPort
$cloudflared = Get-CimInstance Win32_Process -ErrorAction SilentlyContinue | Where-Object {
    $_.Name -eq "cloudflared.exe" -and $_.CommandLine -match "tunnel"
} | Select-Object -First 1

$apiHealth = [pscustomobject]@{ Status = "NOT_LISTENING"; HttpStatus = $null; Reasons = @() }
if ($api) {
    $credential = Read-InternalApiKey
    try {
        if ($credential.Status -eq "OK") {
            $apiHealth = Get-LocalHealth -Port $ApiPort -Readiness -ApiKey $credential.Key
        } else {
            $apiHealth.Status = $credential.Status
        }
    } finally {
        $credential.Key = ""
    }
}

[pscustomobject]@{
    CheckedAt = Get-Date -Format "yyyy-MM-dd HH:mm:ss"
    Frontend = [pscustomobject]@{
        Pid = if ($front) { $front.OwningProcess } else { $null }
        Health = if ($front) { Get-LocalHealth -Port $FrontendPort } else { [pscustomobject]@{ Status = "NOT_LISTENING"; HttpStatus = $null } }
    }
    Api = [pscustomobject]@{
        Pid = if ($api) { $api.OwningProcess } else { $null }
        Health = $apiHealth
    }
    Cloudflared = if ($cloudflared) { "RUNNING pid=$($cloudflared.ProcessId)" } else { "NOT_RUNNING" }
    ApiLog = Join-Path $LogRoot "api.err.log"
    WebLog = Join-Path $LogRoot "web.err.log"
    TunnelLog = Join-Path $LogRoot "cloudflared.err.log"
} | ConvertTo-Json -Depth 5
