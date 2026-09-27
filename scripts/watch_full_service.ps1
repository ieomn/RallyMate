param(
    [int]$Lines = 12,
    [int]$IntervalSeconds = 2
)

$ErrorActionPreference = "SilentlyContinue"
$Root = Split-Path -Parent $PSScriptRoot
$LogRoot = Join-Path $Root ".codex_tmp\full-service"
$logs = @(
    @{ Name = "API / Worker（标准输出）"; Path = (Join-Path $LogRoot "api.log") },
    @{ Name = "API / Worker"; Path = (Join-Path $LogRoot "api.err.log") },
    @{ Name = "网页服务"; Path = (Join-Path $LogRoot "web.err.log") },
    @{ Name = "Cloudflare 隧道"; Path = (Join-Path $LogRoot "cloudflared.err.log") }
)

while ($true) {
    Clear-Host
    Write-Host "RallyMate 实时日志  $(Get-Date -Format 'yyyy-MM-dd HH:mm:ss')" -ForegroundColor Cyan
    Write-Host "按 Ctrl+C 停止查看；不会停止服务。" -ForegroundColor DarkGray
    foreach ($log in $logs) {
        Write-Host "`n--- $($log.Name) ---" -ForegroundColor Yellow
        if (Test-Path -LiteralPath $log.Path) {
            Get-Content -LiteralPath $log.Path -Tail $Lines
        } else {
            Write-Host "日志文件尚未生成。" -ForegroundColor DarkGray
        }
    }
    Start-Sleep -Seconds ([Math]::Max(1, $IntervalSeconds))
}
