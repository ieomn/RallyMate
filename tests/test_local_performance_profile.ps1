$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path -Parent $PSScriptRoot
. (Join-Path $projectRoot 'scripts/local_performance_profile.ps1')
$testRoot = Join-Path $projectRoot ('.codex_tmp/performance-profile-tests-' + [guid]::NewGuid().ToString('N'))
foreach ($directory in @('config','scripts','scoring-demo-web','runtime/rtmpose/.venv/Scripts','.codex_tmp/full-service')) {
    New-Item -ItemType Directory -Force -Path (Join-Path $testRoot $directory) | Out-Null
}
foreach ($relative in @('config/local-performance.json','scripts/local_performance_profile.ps1','scripts/run_full_service.ps1')) {
    Copy-Item -LiteralPath (Join-Path $projectRoot $relative) -Destination (Join-Path $testRoot $relative)
}
Set-Content -LiteralPath (Join-Path $testRoot 'runtime/rtmpose/.venv/Scripts/python.exe') -Value 'never executed'
$script:assertions = 0
function Assert([bool]$Condition, [string]$Message) {
    if (-not $Condition) { throw $Message }; $script:assertions++
}
function Reject([scriptblock]$Operation, [string]$Message) {
    $caught = $false; try { & $Operation | Out-Null } catch { $caught = $true }
    Assert $caught $Message
}
$original = Read-LocalPerformanceProfile -ProjectRoot $testRoot -Model sam3d-dinov3 -IgnoreEnvironment
$names = @($original.settings.Keys) + @('RALLYMATE_LOCAL_PERFORMANCE_OVERRIDES','RALLYMATE_POSE_BACKEND','RALLYMATE_POSE_PRESET','RALLYMATE_POSE_MODEL','RALLYMATE_DEVICE')
$saved = @{}
foreach ($name in $names) {
    $saved[$name] = [Environment]::GetEnvironmentVariable($name, 'Process')
    # PowerShell 7.6 preserves an empty string passed to the .NET API. Remove
    # the entry explicitly so this fixture also exercises an unset variable.
    Remove-Item -LiteralPath "Env:$name" -ErrorAction SilentlyContinue
}
try {
    $profile = Read-LocalPerformanceProfile -ProjectRoot $testRoot -Model sam3d-dinov3
    Assert ($profile.settings.RALLYMATE_SAM3D_BATCH_FRAMES -eq '8') 'Fresh process must restore batch8.'
    Assert ($profile.settings.RALLYMATE_SAM3D_PIPELINE_OVERLAP -eq '1') 'Overlap must survive restart.'
    Assert ($profile.settings.RALLYMATE_SAM3D_MESH_PREPARE_WORKERS -eq '2') 'Mesh preparation must survive restart.'
    $env:RALLYMATE_SCORING_CPU_WORKERS = '4'
    $parallel = Read-LocalPerformanceProfile -ProjectRoot $testRoot -Model sam3d-dinov3
    Assert ($parallel.settings.RALLYMATE_SCORING_CPU_WORKERS -eq '4') 'Explicit bounded scoring workers must be accepted.'
    Remove-Item Env:RALLYMATE_SCORING_CPU_WORKERS
    $profile.settings.RALLYMATE_SCORING_CPU_WORKERS = '4'
    $profile.settings.RALLYMATE_DETECT_FORWARD_GRAPH = '1'
    Save-LocalPerformanceProfile $profile
    $restored = Read-LocalPerformanceProfile -ProjectRoot $testRoot -Model sam3d-dinov3
    Assert ($restored.settings.RALLYMATE_SCORING_CPU_WORKERS -eq '4' -and $restored.settings.RALLYMATE_DETECT_FORWARD_GRAPH -eq '1') 'CPU workers and detector graph must survive a fresh profile read.'
    Assert ($null -eq (Read-LocalPerformanceProfile -ProjectRoot $testRoot -Model rtmpose)) 'Do not apply SAM profile to RTMPose.'
    function Get-LocalPerformanceGpu { @{ name = 'NVIDIA GeForce RTX 5070 Ti'; memory_mib = 16303 } }
    $null = Read-LocalPerformanceProfile -ProjectRoot $testRoot -Model sam3d-dinov3 -ValidateDevice
    function Get-LocalPerformanceGpu { @{ name = 'NVIDIA GeForce RTX 5070 Ti'; memory_mib = 8192 } }
    Reject { Read-LocalPerformanceProfile -ProjectRoot $testRoot -Model sam3d-dinov3 -ValidateDevice } 'Insufficient memory must fail, never downgrade silently.'
    $env:RALLYMATE_SAM3D_BATCH_FRAMES = '1'
    $baseline = Read-LocalPerformanceProfile -ProjectRoot $testRoot -Model sam3d-dinov3
    Assert ($baseline.settings.RALLYMATE_SAM3D_BATCH_GRAPH_SIZES -eq '') 'Baseline override must disable inherited batch graphs.'
    Assert ($baseline.settings.RALLYMATE_SAM3D_BATCH_FRAMES -eq '1') 'Explicit environment must win.'
    Remove-Item Env:RALLYMATE_SAM3D_BATCH_FRAMES
    $env:RALLYMATE_LOCAL_PERFORMANCE_OVERRIDES = '{"RALLYMATE_SAM3D_DECODER_BATCH_GRAPH_SIZES":""}'
    $noDecoder = Read-LocalPerformanceProfile -ProjectRoot $testRoot -Model sam3d-dinov3
    Assert ($noDecoder.settings.RALLYMATE_SAM3D_DECODER_BATCH_GRAPH_SIZES -eq '') 'Explicit empty graph override must survive Windows environment semantics.'
    Remove-Item Env:RALLYMATE_LOCAL_PERFORMANCE_OVERRIDES
    $profile.settings.RALLYMATE_SAM3D_PIPELINE_OVERLAP = '0'
    Save-LocalPerformanceProfile $profile
    $reload = Read-LocalPerformanceProfile -ProjectRoot $testRoot -Model sam3d-dinov3
    Assert ($reload.settings.RALLYMATE_SAM3D_PIPELINE_OVERLAP -eq '0') 'Saved choice must persist in a fresh read.'
    $profile.settings.RALLYMATE_SAM3D_PIPELINE_OVERLAP = '1'
    Save-LocalPerformanceProfile $profile
    Reject { Read-LocalPerformanceProfile -ProjectRoot $testRoot -Model sam3d-dinov3 -Overrides @{ RALLYMATE_SAM3D_BATCH_FRAMES = '16' } } 'Reject unsupported batch size.'
    Reject { Read-LocalPerformanceProfile -ProjectRoot $testRoot -Model sam3d-dinov3 -Overrides @{ RALLYMATE_SCORING_CPU_WORKERS = '64' } } 'Reject unqualified CPU worker counts.'
    Reject { Read-LocalPerformanceProfile -ProjectRoot $testRoot -Model sam3d-dinov3 -Overrides @{ RALLYMATE_DETECT_FORWARD_GRAPH = '2' } } 'Reject invalid detector graph settings.'
    Reject { Read-LocalPerformanceProfile -ProjectRoot $testRoot -Model sam3d-dinov3 -Overrides @{ SECRET = 'forbidden' } } 'Reject unknown keys; receipts must be allowlisted.'
    $processes = @(
        [pscustomobject]@{ ProcessId = 1; ParentProcessId = 0; CommandLine = 'python scripts/run_dev_service.py' },
        [pscustomobject]@{ ProcessId = 2; ParentProcessId = 1; CommandLine = 'python -m rallymate_service.local_worker' },
        [pscustomobject]@{ ProcessId = 3; ParentProcessId = 2; CommandLine = 'python -u -m rallymate_vision.sam3d_mesh --source video.mp4' }
    )
    Reject { Assert-LocalMeshIdle -Snapshot $processes -ApiProcessIds @(1) } 'Must protect mesh grandchildren, including new managed local worker.'
    Reject { Assert-LocalMeshIdle -Snapshot @($processes[0],$processes[1]) -ApiProcessIds @(1) -RequireUnsupervised } 'Cannot force-stop a supervised worker through legacy fallback.'
    Assert-LocalMeshIdle -Snapshot $processes -ApiProcessIds @(77)
    $script:assertions++

    # Execute the real ordinary launcher in a disposable directory. All process
    # creation and listening sockets are fakes; no model/server/GPU is started.
    $global:PerformanceTestCalls = @(); $global:PerformanceTestOccupied = $false
    function Get-NetTCPConnection { if ($global:PerformanceTestOccupied) { [pscustomobject]@{ LocalPort = 8001 } } }
    function Start-Process {
        param($FilePath, $WorkingDirectory, $WindowStyle, [switch]$PassThru, $RedirectStandardOutput, $RedirectStandardError, $ArgumentList)
        $global:PerformanceTestCalls += [pscustomobject]@{ file = $FilePath; frames = $env:RALLYMATE_SAM3D_BATCH_FRAMES; overlap = $env:RALLYMATE_SAM3D_PIPELINE_OVERLAP }
        Set-Content -LiteralPath $RedirectStandardOutput -Value 'new launch'
        Set-Content -LiteralPath $RedirectStandardError -Value 'new errors'
        [pscustomobject]@{ Id = 1000 + $global:PerformanceTestCalls.Count }
    }
    $env:RALLYMATE_POSE_BACKEND = 'sam3d'
    $env:RALLYMATE_POSE_MODEL = 'X:/models/sam-3d-body-dinov3/model.ckpt'
    $env:RALLYMATE_DEVICE = '0'
    $logRoot = Join-Path $testRoot '.codex_tmp/full-service'
    Set-Content -LiteralPath (Join-Path $logRoot 'api.err.log') -Value 'historical worker exception'
    & (Join-Path $testRoot 'scripts/run_full_service.ps1') -ApiOnly | Out-Null
    Assert ($global:PerformanceTestCalls.Count -eq 2 -and $global:PerformanceTestCalls[0].frames -eq '8') 'Ordinary launch must inherit persisted batch8.'
    $receipt = Get-Content -LiteralPath (Join-Path $logRoot 'launch-profile.json') -Raw | ConvertFrom-Json
    Assert ($receipt.api_launcher_pid -eq 1001 -and $receipt.settings.RALLYMATE_SAM3D_BATCH_FRAMES -eq '8') 'Receipt must describe this launch.'
    Assert ((Get-Content -LiteralPath (Join-Path $receipt.log_backup 'api.err.log') -Raw).Trim() -eq 'historical worker exception') 'Archive evidence before log truncation.'
    foreach ($name in $original.settings.Keys) { Remove-Item -LiteralPath "Env:$name" -ErrorAction SilentlyContinue }
    & (Join-Path $testRoot 'scripts/run_full_service.ps1') -ApiOnly | Out-Null
    Assert ($global:PerformanceTestCalls.Count -eq 4 -and $global:PerformanceTestCalls[2].frames -eq '8' -and $global:PerformanceTestCalls[2].overlap -eq '1') 'Second clean startup must restore the same settings.'
    $global:PerformanceTestOccupied = $true
    $oldHash = (Get-FileHash -LiteralPath (Join-Path $logRoot 'api.err.log')).Hash
    Reject { & (Join-Path $testRoot 'scripts/run_full_service.ps1') -ApiOnly } 'Occupied ports must reject duplicate launch.'
    Assert ($global:PerformanceTestCalls.Count -eq 4 -and (Get-FileHash -LiteralPath (Join-Path $logRoot 'api.err.log')).Hash -eq $oldHash) 'Rejected launch must not truncate evidence or start processes.'
    $global:PerformanceTestOccupied = $false
    Set-Content -LiteralPath (Join-Path $testRoot 'config/local-performance.json') -Value '{broken'
    Reject { & (Join-Path $testRoot 'scripts/run_full_service.ps1') -ApiOnly } 'Invalid profile must reject ordinary launch.'
    Assert ($global:PerformanceTestCalls.Count -eq 4) 'Invalid profile must never launch a fallback worker.'
    function Invoke-LocalServiceJson {
        param($ApiPort, $ApiKey, $Path, $Method)
        if ($global:PerformanceControlResponses.Count -eq 0) { throw 'Unexpected control request' }
        return $global:PerformanceControlResponses.Dequeue()
    }
    function Set-ControlResponses([array]$Values) {
        $global:PerformanceControlResponses = New-Object 'Collections.Generic.Queue[object]'
        foreach ($value in $Values) { $global:PerformanceControlResponses.Enqueue($value) }
    }
    $emptyQueue = @{ queued = 0; running = 0 }
    Set-ControlResponses @(
        @{ StatusCode = 202; Body = @{ status = 'draining' } },
        @{ StatusCode = 503; Body = @{ status = 'not_ready'; maintenance = $true; queue = $emptyQueue; executor = @{status = 'stopped'} } }
    )
    Assert ((Stop-LocalExecutorForRestart -ApiPort 8001 -ApiKey 'fake') -eq 'supervisor_stopped') 'Shutdown must accept explicit stopped even when readiness is 503.'
    Set-ControlResponses @(@{ StatusCode = 409; Body = @{ error = 'busy' } })
    Reject { Stop-LocalExecutorForRestart -ApiPort 8001 -ApiKey 'fake' } 'Busy control response must block forced shutdown.'
    Set-ControlResponses @(
        @{ StatusCode = 202; Body = @{ status = 'draining' } },
        @{ StatusCode = 200; Body = @{ maintenance = $true; queue = $emptyQueue; executor = @{status = 'restarting'} } }
    )
    Reject { Stop-LocalExecutorForRestart -ApiPort 8001 -ApiKey 'fake' -TimeoutSeconds 0 } 'Drain timeout must abort, not force-stop the worker.'
    Set-ControlResponses @(
        @{ StatusCode = 404; Body = @{} },
        @{ StatusCode = 200; Body = @{ status = 'ready'; queue = $emptyQueue } }
    )
    Assert ((Stop-LocalExecutorForRestart -ApiPort 8001 -ApiKey 'fake') -eq 'legacy_idle') 'Old idle API supports first safe migration.'
    Set-ControlResponses @(
        @{ StatusCode = 404; Body = @{} },
        @{ StatusCode = 200; Body = @{ status = 'ready'; queue = $emptyQueue; executor = @{status='ready'} } }
    )
    Reject { Stop-LocalExecutorForRestart -ApiPort 8001 -ApiKey 'fake' } 'New API without maintenance control must not fall back to killing a supervised child.'
    foreach ($file in @('local_performance_profile.ps1','start_local.ps1','run_full_service.ps1','restart_preview_service.ps1','restart_performance_preview.ps1')) {
        $tokens = $null; $errors = $null
        $null = [System.Management.Automation.Language.Parser]::ParseFile((Join-Path $projectRoot "scripts/$file"), [ref]$tokens, [ref]$errors)
        Assert ($errors.Count -eq 0) "PowerShell parser errors: $file"
    }
    Write-Host "$script:assertions assertions passed; fake launch only; evidence: $testRoot"
} finally {
    foreach ($name in $saved.Keys) {
        if ($null -eq $saved[$name]) { Remove-Item -LiteralPath "Env:$name" -ErrorAction SilentlyContinue }
        else { [Environment]::SetEnvironmentVariable($name, $saved[$name], 'Process') }
    }
}
