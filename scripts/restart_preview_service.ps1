param(
    [ValidateRange(1024, 65535)]
    [int]$FrontendPort = 8000,
    [ValidateRange(1024, 65535)]
    [int]$ApiPort = 8001,
    [ValidateSet("sam3d-dinov3", "sam3d-vith", "rtmpose")]
    [string]$Model = "sam3d-dinov3",
    [string]$DataRoot = "",
    [ValidateRange(1, 64)]
    [int]$CpuThreads = 4,
    [ValidateRange(1, 64)]
    [int]$SamCpuThreads = 1,
    [ValidateSet("runtime_default", "passive")]
    [string]$SamWaitMode = "passive",
    [switch]$ApiOnly
)

# Manual maintenance entry point: verify the expected idle preview processes before
# replacing them. Cloudflare keeps its existing process and public hostname.
$ErrorActionPreference = "Stop"
if ($Model -eq "sam3d-dinov3" -and -not $PSBoundParameters.ContainsKey("CpuThreads")) {
    $CpuThreads = 2
}
$projectRoot = Split-Path -Parent $PSScriptRoot
. (Join-Path $PSScriptRoot "preview_process_ownership.ps1")
. (Join-Path $PSScriptRoot 'local_performance_profile.ps1')
# Reject missing/invalid performance configuration before stopping the old API.
$null = Read-LocalPerformanceProfile -ProjectRoot $projectRoot -Model $Model -Device '0' -ValidateDevice:(-not $ApiOnly)
if ($FrontendPort -eq $ApiPort) { throw "Frontend and API ports must differ." }
foreach ($relativePath in @("scripts/start_local.ps1", "runtime/rtmpose/.venv/Scripts/python.exe", "models/yolo26n.pt", "scoring-demo-web/.dev.vars", "scoring-demo-web/dist/server/index.js", "scoring-demo-web/node_modules/vinext/dist/cli.js")) {
    if (-not (Test-Path -LiteralPath (Join-Path $projectRoot $relativePath) -PathType Leaf)) {
        throw "Required file is missing: $relativePath. No service was stopped."
    }
}
Get-Command node, npm.cmd -CommandType Application -ErrorAction Stop | Out-Null
if ($Model -ne "rtmpose") {
    $checkpointName = if ($Model -eq "sam3d-vith") { "sam-3d-body-vith" } else { "sam-3d-body-dinov3" }
    foreach ($relativePath in @("runtime/sam3d/.venv311/Scripts/python.exe", "runtime/sam3d/checkpoints/$checkpointName/model.ckpt", "runtime/sam3d/checkpoints/$checkpointName/model_config.yaml", "runtime/sam3d/checkpoints/$checkpointName/assets/mhr_model.pt")) {
        if (-not (Test-Path -LiteralPath (Join-Path $projectRoot $relativePath) -PathType Leaf)) {
            throw "The selected SAM model is incomplete: $relativePath. No service was stopped."
        }
    }
}
$internalKey = ""
foreach ($line in [IO.File]::ReadAllLines((Join-Path $projectRoot "scoring-demo-web/.dev.vars"))) {
    if ($line -match '^\s*RALLYMATE_API_KEY\s*=\s*(.*?)\s*$') { $internalKey = $Matches[1].Trim().Trim('"').Trim("'") }
}
if ($internalKey.Length -lt 32 -or $internalKey -notmatch '^[A-Za-z0-9_-]+$') {
    throw "The internal API key is missing or invalid. No service was stopped."
}

$listenersToStop = @()
foreach ($port in @($FrontendPort, $ApiPort)) {
    $listeners = @(Get-NetTCPConnection -LocalPort $port -State Listen -ErrorAction SilentlyContinue)
    foreach ($listener in $listeners) {
        if ($listener.LocalAddress -ne "127.0.0.1") { throw "Port $port is not loopback-only. No service was stopped." }
        $serviceProcess = Get-CimInstance Win32_Process -Filter "ProcessId = $($listener.OwningProcess)"
        $expected = if ($port -eq $FrontendPort) { 'vinext[\\/]dist[\\/]cli\.js\s+start' } else { 'scripts[\\/]run_dev_service\.py' }
        if (-not $serviceProcess -or $serviceProcess.CommandLine -notmatch $expected -or $serviceProcess.CommandLine -notmatch "--port\s+$port(?:\s|$)") {
            throw "Port $port belongs to an unexpected process. No service was stopped."
        }
        $listenersToStop += [pscustomobject]@{
            ProcessId = [int]$serviceProcess.ProcessId
            Port = $port
            Kind = if ($port -eq $ApiPort) { 'api' } else { 'frontend' }
            CapturedIdentity = Get-PreviewProcessIdentity -Process $serviceProcess
        }
    }
}

# New services freeze admission and gracefully drain the supervised worker
# before process cleanup. Never force-stop a child that its parent may respawn.
$maintenanceState = $null
if (Get-NetTCPConnection -LocalPort $ApiPort -State Listen -ErrorAction SilentlyContinue) {
    try {
        $maintenanceState = Stop-LocalExecutorForRestart -ApiPort $ApiPort -ApiKey $internalKey
    } finally {
        $internalKey = ""
    }
}

$processSnapshot = @(Get-CimInstance Win32_Process)
# Older services have no executor snapshot. An owned mesh subprocess is still
# active work even after its analysis job has status=succeeded.
$ownedApiIds = @($listenersToStop | Where-Object Kind -eq 'api' | ForEach-Object { $_.ProcessId })
Assert-LocalMeshIdle -Snapshot $processSnapshot -ApiProcessIds $ownedApiIds -RequireUnsupervised:($maintenanceState -eq 'legacy_idle')
$stopPlan = @(New-PreviewProcessStopPlan -Snapshot $processSnapshot -Listeners $listenersToStop -ProjectRoot $projectRoot)
$cleanupReceipt = Join-Path $projectRoot (".codex_tmp/full-service/restart-cleanup-" + [datetime]::UtcNow.ToString('yyyyMMddTHHmmssfffffffZ') + ".json")
Stop-PreviewProcessPlan -Plan $stopPlan -ReceiptPath $cleanupReceipt
Write-Host "Verified service process cleanup receipt: $cleanupReceipt"
# Windows may retain the listener briefly after the owning process exits.
# Check both ports again before the launcher refuses an apparently busy port.
$portReleaseDeadline = (Get-Date).AddSeconds(30)
do {
    $remainingListeners = @(Get-NetTCPConnection -State Listen -ErrorAction SilentlyContinue |
        Where-Object { $_.LocalPort -in @($FrontendPort, $ApiPort) })
    if ($remainingListeners.Count -eq 0) { break }
    if ((Get-Date) -ge $portReleaseDeadline) {
        throw "The preview ports were not released. No replacement service was started."
    }
    Start-Sleep -Milliseconds 300
} while ($true)
& (Join-Path $PSScriptRoot "start_local.ps1") -FrontendPort $FrontendPort -ApiPort $ApiPort -Model $Model -DataRoot $DataRoot -CpuThreads $CpuThreads -SamCpuThreads $SamCpuThreads -SamWaitMode $SamWaitMode -ApiOnly:$ApiOnly
Write-Host "Preview processes launched; allow the API a few seconds to become ready. Keep the existing Cloudflare terminal open."
Write-Host "Local preview: http://127.0.0.1:$FrontendPort"
