param(
    [ValidateRange(1024, 65535)] [int]$FrontendPort = 8000,
    [ValidateRange(1024, 65535)] [int]$ApiPort = 8001,
    [string]$DataRoot = "",
    [ValidateSet(1, 4, 8)] [int]$SamBatchFrames = 8,
    [switch]$PipelineOverlap,
    [switch]$DecoderBatchGraph,
    [ValidateSet(0, 2)] [int]$MeshPrepareWorkers = 2,
    [ValidateSet(131072, 1048576, 2097152, 4194304)] [int]$UploadChunkBytes = 1048576
)

# Unspecified options retain the durable local profile. Explicit options are
# persisted only after the ordinary guarded restart successfully launches.
$ErrorActionPreference = "Stop"
. (Join-Path $PSScriptRoot 'local_performance_profile.ps1')
$projectRoot = Split-Path -Parent $PSScriptRoot
$overrides = @{}
if ($PSBoundParameters.ContainsKey('SamBatchFrames')) {
    $overrides.RALLYMATE_SAM3D_BATCH_FRAMES = [string]$SamBatchFrames
    $overrides.RALLYMATE_SAM3D_BATCH_GRAPH_SIZES = $(if ($SamBatchFrames -eq 1) { '' } else { '8' })
    $overrides.RALLYMATE_SAM3D_ARCHIVE_PENDING_FRAMES = $(if ($SamBatchFrames -eq 1) { '2' } else { [string]$SamBatchFrames })
    $overrides.RALLYMATE_SAM3D_FORWARD_GRAPH_BUDGET_MIB = $(if ($SamBatchFrames -eq 1) { '512' } else { '2560' })
    if ($SamBatchFrames -eq 1) { $overrides.RALLYMATE_SAM3D_DECODER_BATCH_GRAPH_SIZES = '' }
}
if ($PSBoundParameters.ContainsKey('PipelineOverlap')) { $overrides.RALLYMATE_SAM3D_PIPELINE_OVERLAP = $(if ($PipelineOverlap) { '1' } else { '0' }) }
if ($PSBoundParameters.ContainsKey('DecoderBatchGraph')) { $overrides.RALLYMATE_SAM3D_DECODER_BATCH_GRAPH_SIZES = $(if ($DecoderBatchGraph) { '8' } else { '' }) }
if ($PSBoundParameters.ContainsKey('MeshPrepareWorkers')) { $overrides.RALLYMATE_SAM3D_MESH_PREPARE_WORKERS = [string]$MeshPrepareWorkers }
if ($PSBoundParameters.ContainsKey('UploadChunkBytes')) { $overrides.RALLYMATE_UPLOAD_CHUNK_BYTES = [string]$UploadChunkBytes }
$profile = Read-LocalPerformanceProfile -ProjectRoot $projectRoot -Model 'sam3d-dinov3' -Overrides $overrides -ValidateDevice
$performanceSettings = $profile.settings
$previousPerformanceSettings = @{}
$previousLauncherOverrides = $env:RALLYMATE_LOCAL_PERFORMANCE_OVERRIDES
try {
    foreach ($name in $performanceSettings.Keys) {
        $previousPerformanceSettings[$name] = [Environment]::GetEnvironmentVariable($name, "Process")
        [Environment]::SetEnvironmentVariable($name, $performanceSettings[$name], "Process")
    }
    $env:RALLYMATE_LOCAL_PERFORMANCE_OVERRIDES = $performanceSettings | ConvertTo-Json -Compress
    # The existing restart entry point checks ownership/readiness and refuses
    # active or queued analyses. It preserves Cloudflare and reuses the built UI.
    & (Join-Path $PSScriptRoot "restart_preview_service.ps1") -FrontendPort $FrontendPort -ApiPort $ApiPort `
        -DataRoot $DataRoot -Model sam3d-dinov3 -CpuThreads 2 -SamCpuThreads 1 -SamWaitMode passive
    Save-LocalPerformanceProfile $profile
    Write-Host "Local profile launched and persisted: SAM frames=$($performanceSettings.RALLYMATE_SAM3D_BATCH_FRAMES). Receipt: .codex_tmp/full-service/launch-profile.json"
} finally {
    [Environment]::SetEnvironmentVariable('RALLYMATE_LOCAL_PERFORMANCE_OVERRIDES', $previousLauncherOverrides, 'Process')
    foreach ($name in $previousPerformanceSettings.Keys) {
        [Environment]::SetEnvironmentVariable($name, $previousPerformanceSettings[$name], "Process")
    }
}
