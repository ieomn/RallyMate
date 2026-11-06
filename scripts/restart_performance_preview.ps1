param(
    [ValidateRange(1024, 65535)] [int]$FrontendPort = 8000,
    [ValidateRange(1024, 65535)] [int]$ApiPort = 8001,
    [string]$DataRoot = "",
    [ValidateSet(1, 4, 8)] [int]$SamBatchFrames = 1,
    [ValidateSet(131072, 1048576, 2097152, 4194304)] [int]$UploadChunkBytes = 1048576
)

# Explicit local profile; the reusable library defaults remain conservative.
# 8-frame/8-subject graph8 has been measured on this machine's 16 GiB GPU.
# A source-frame buffer split or a short tail can still miss the graph shape.
$ErrorActionPreference = "Stop"
if ($SamBatchFrames -gt 1) {
    $smi = Get-Command nvidia-smi -CommandType Application -ErrorAction Stop
    $reported = @(& $smi.Source --id=0 --query-gpu=memory.total --format=csv,noheader,nounits)
    if ($LASTEXITCODE -ne 0 -or $reported.Count -ne 1) {
        throw "Cannot verify GPU memory for the explicit batch profile. No service was stopped."
    }
    $totalMiB = 0
    if (-not [int]::TryParse(([string]$reported[0]).Trim(), [ref]$totalMiB) -or $totalMiB -lt 15000) {
        throw "This batch profile was validated on a 16 GiB GPU. Use -SamBatchFrames 1 on an unvalidated device. No service was stopped."
    }
}
$performanceSettings = @{
    RALLYMATE_UPLOAD_CHUNK_BYTES = [string]$UploadChunkBytes
    RALLYMATE_DETECT_BATCH_SIZE = "1"
    RALLYMATE_SAM3D_BATCH_FRAMES = [string]$SamBatchFrames
    RALLYMATE_SAM3D_BATCH_PERSONS = "8"
    RALLYMATE_SAM3D_BATCH_GRAPH_SIZES = $(if ($SamBatchFrames -eq 1) { "" } else { "8" })
    RALLYMATE_SAM3D_BATCH_PREPROCESS = "0"
}
$previousPerformanceSettings = @{}
try {
    foreach ($name in $performanceSettings.Keys) {
        $previousPerformanceSettings[$name] = [Environment]::GetEnvironmentVariable($name, "Process")
        [Environment]::SetEnvironmentVariable($name, $performanceSettings[$name], "Process")
    }
    # The existing restart entry point checks ownership/readiness and refuses
    # active or queued analyses. It preserves Cloudflare and reuses the built UI.
    & (Join-Path $PSScriptRoot "restart_preview_service.ps1") -FrontendPort $FrontendPort -ApiPort $ApiPort `
        -DataRoot $DataRoot -Model sam3d-dinov3 -CpuThreads 2 -SamCpuThreads 1 -SamWaitMode passive
    Write-Host "Local profile launched: upload chunk=$UploadChunkBytes bytes, SAM frames=$SamBatchFrames, grouped crop staging=off."
} finally {
    foreach ($name in $previousPerformanceSettings.Keys) {
        [Environment]::SetEnvironmentVariable($name, $previousPerformanceSettings[$name], "Process")
    }
}
