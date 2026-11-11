# Local launcher configuration only. Library/model defaults remain unchanged.
function Assert-LocalPerformanceSettings {
    param([hashtable]$Settings)
    $choices = @{
        RALLYMATE_UPLOAD_CHUNK_BYTES = @('131072','1048576','2097152','4194304')
        RALLYMATE_DETECT_BATCH_SIZE = @('1')
        RALLYMATE_DETECT_FORWARD_GRAPH = @('0','1')
        RALLYMATE_DETECT_GRAPH_MAX_MIB = @('256')
        RALLYMATE_SCORING_CPU_WORKERS = @('1','4')
        RALLYMATE_SAM3D_BATCH_FRAMES = @('1','4','8')
        RALLYMATE_SAM3D_BATCH_PERSONS = @('8')
        RALLYMATE_SAM3D_BATCH_GRAPH_SIZES = @('','8')
        RALLYMATE_SAM3D_BATCH_PREPROCESS = @('0')
        RALLYMATE_SAM3D_ARCHIVE_PENDING_FRAMES = @('2','4','8')
        RALLYMATE_SAM3D_PIPELINE_OVERLAP = @('0','1')
        RALLYMATE_SAM3D_DECODER_BATCH_GRAPH_SIZES = @('','8')
        RALLYMATE_SAM3D_FORWARD_GRAPH_BUDGET_MIB = @('512','2560')
        RALLYMATE_SAM3D_HEAD_GRAPH_BUDGET_MIB = @('512')
        RALLYMATE_SAM3D_MESH_PREPARE_WORKERS = @('0','2')
        RALLYMATE_SAM3D_CUDA_WAIT_MODE = @('runtime_default')
    }
    if ($Settings.Count -ne $choices.Count) { throw 'Incomplete or unknown local performance settings.' }
    foreach ($name in $Settings.Keys) {
        if (-not $choices.ContainsKey($name) -or $Settings[$name] -isnot [string] -or $Settings[$name] -cnotin $choices[$name]) {
            throw "Unsupported local performance setting: $name. No service was started."
        }
    }
    if ($Settings.RALLYMATE_SAM3D_BATCH_FRAMES -eq '1' -and
        ($Settings.RALLYMATE_SAM3D_BATCH_GRAPH_SIZES -or $Settings.RALLYMATE_SAM3D_DECODER_BATCH_GRAPH_SIZES)) {
        throw 'Single-frame baseline cannot use additional batch graph shapes.'
    }
    if (($Settings.RALLYMATE_SAM3D_BATCH_GRAPH_SIZES -or $Settings.RALLYMATE_SAM3D_DECODER_BATCH_GRAPH_SIZES) -and
        $Settings.RALLYMATE_SAM3D_FORWARD_GRAPH_BUDGET_MIB -ne '2560') {
        throw 'Additional batch graphs require the validated 2560 MiB forward graph budget.'
    }
}

function Get-LocalPerformanceGpu {
    param([string]$Device)
    if ($Device -match '^(?:cuda:)?(\d+)$') { $index = $Matches[1] }
    elseif ($Device -eq 'cuda') { $index = '0' }
    else { throw 'The local batch profile requires an explicit CUDA device.' }
    $smi = Get-Command nvidia-smi -CommandType Application -ErrorAction Stop
    $rows = @(& $smi.Source "--id=$index" --query-gpu=name,memory.total --format=csv,noheader,nounits)
    if ($LASTEXITCODE -ne 0 -or $rows.Count -ne 1 -or $rows[0] -notmatch '^\s*(.+),\s*(\d+)\s*$') {
        throw 'Cannot verify the GPU for the persisted local performance profile.'
    }
    return @{ name = $Matches[1].Trim(); memory_mib = [int]$Matches[2] }
}

function Read-LocalPerformanceProfile {
    param([string]$ProjectRoot, [string]$Model, [string]$Device = '0',
          [hashtable]$Overrides = @{}, [switch]$ValidateDevice, [switch]$IgnoreEnvironment)
    if ($Model -ne 'sam3d-dinov3') { return $null }
    $path = Join-Path $ProjectRoot 'config/local-performance.json'
    if (-not (Test-Path -LiteralPath $path -PathType Leaf)) {
        throw "Local performance profile is missing: $path. Refusing a silent single-frame fallback."
    }
    $profile = Get-Content -LiteralPath $path -Raw | ConvertFrom-Json -ErrorAction Stop
    if ($profile.schema_version -ne 1 -or $profile.model -ne $Model -or
        $profile.gpu_name -ne 'NVIDIA GeForce RTX 5070 Ti' -or $profile.minimum_gpu_memory_mib -ne 15000 -or
        @($profile.PSObject.Properties.Name).Count -ne 5) { throw 'Unsupported local performance profile schema or hardware scope.' }
    $settings = @{}
    foreach ($property in $profile.settings.PSObject.Properties) { $settings[$property.Name] = $property.Value }
    Assert-LocalPerformanceSettings $settings
    $sources = @{}
    foreach ($name in @($settings.Keys)) {
        $sources[$name] = 'persisted_profile'
        $explicit = [Environment]::GetEnvironmentVariable($name, 'Process')
        if (-not $IgnoreEnvironment -and $null -ne $explicit) { $settings[$name] = $explicit; $sources[$name] = 'environment' }
    }
    # Explicit launcher options must also carry empty graph lists through
    # Windows, where an empty environment variable is otherwise removed.
    if (-not $IgnoreEnvironment -and $env:RALLYMATE_LOCAL_PERFORMANCE_OVERRIDES) {
        $launcherOverrides = $env:RALLYMATE_LOCAL_PERFORMANCE_OVERRIDES | ConvertFrom-Json -ErrorAction Stop
        foreach ($property in $launcherOverrides.PSObject.Properties) {
            $settings[$property.Name] = $property.Value; $sources[$property.Name] = 'explicit_launcher'
        }
    }
    foreach ($name in $Overrides.Keys) { $settings[$name] = $Overrides[$name]; $sources[$name] = 'explicit_parameter' }
    # Windows removes empty environment values. A deliberate batch1 override
    # also disables graph shapes inherited from the persisted batch8 profile.
    if ($settings.RALLYMATE_SAM3D_BATCH_FRAMES -eq '1') {
        foreach ($name in @('RALLYMATE_SAM3D_BATCH_GRAPH_SIZES','RALLYMATE_SAM3D_DECODER_BATCH_GRAPH_SIZES')) {
            if ($sources[$name] -eq 'persisted_profile') { $settings[$name] = ''; $sources[$name] = 'batch1_override' }
        }
    }
    Assert-LocalPerformanceSettings $settings
    if ($ValidateDevice -and [int]$settings.RALLYMATE_SAM3D_BATCH_FRAMES -gt 1) {
        $gpu = Get-LocalPerformanceGpu $Device
        if ($gpu.name -ne $profile.gpu_name -or $gpu.memory_mib -lt $profile.minimum_gpu_memory_mib) {
            throw 'Persisted batch profile does not match the validated RTX 5070 Ti 16 GiB GPU. No service was stopped or started.'
        }
    }
    return [pscustomobject]@{ path = $path; sha256 = (Get-FileHash -LiteralPath $path -Algorithm SHA256).Hash.ToLowerInvariant();
        schema_version = 1; model = $Model; settings = $settings; sources = $sources }
}

function Set-LocalPerformanceEnvironment {
    param($Profile)
    if ($null -eq $Profile) { return }
    foreach ($name in $Profile.settings.Keys) { [Environment]::SetEnvironmentVariable($name, $Profile.settings[$name], 'Process') }
}

function Save-LocalPerformanceProfile {
    param($Profile)
    Assert-LocalPerformanceSettings $Profile.settings
    $value = [ordered]@{ schema_version = 1; model = 'sam3d-dinov3'; gpu_name = 'NVIDIA GeForce RTX 5070 Ti';
        minimum_gpu_memory_mib = 15000; settings = $Profile.settings }
    $temp = $Profile.path + '.' + [guid]::NewGuid().ToString('N') + '.tmp'
    try {
        $value | ConvertTo-Json -Depth 4 | Set-Content -LiteralPath $temp -Encoding UTF8
        # Replace an existing file atomically; never leave a partially written profile.
        [IO.File]::Replace($temp, $Profile.path, [System.Management.Automation.Language.NullString]::Value)
    } finally { if (Test-Path -LiteralPath $temp) { Remove-Item -LiteralPath $temp } }
}

function Backup-LocalServiceLogs {
    param([string]$LogRoot, [switch]$IncludeTunnel)
    $names = @('api.log','api.err.log','web.log','web.err.log','performance-profile.json','launch-profile.json')
    if ($IncludeTunnel) { $names += @('cloudflared.log','cloudflared.err.log') }
    $files = @($names | ForEach-Object { Join-Path $LogRoot $_ } | Where-Object { Test-Path -LiteralPath $_ -PathType Leaf })
    if ($files.Count -eq 0) { return }
    $destination = Join-Path $LogRoot ('startup-backup-' + [DateTime]::UtcNow.ToString('yyyyMMddTHHmmssfffffffZ'))
    New-Item -ItemType Directory -Path $destination -ErrorAction Stop | Out-Null
    foreach ($file in $files) { Copy-Item -LiteralPath $file -Destination $destination -ErrorAction Stop }
    return $destination
}

function Assert-LocalMeshIdle {
    param([array]$Snapshot, [array]$ApiProcessIds, [switch]$RequireUnsupervised)
    $ownedIds = @($ApiProcessIds)
    $previousCount = -1
    while ($previousCount -ne $ownedIds.Count) {
        $previousCount = $ownedIds.Count
        $ownedIds = @($ownedIds + @($Snapshot | Where-Object { $_.ParentProcessId -in $ownedIds } | ForEach-Object { [int]$_.ProcessId }) | Select-Object -Unique)
    }
    $activeRenderers = @($Snapshot | Where-Object {
        $_.ProcessId -in $ownedIds -and $_.CommandLine -match '(?:sam3d_mesh_renderer|render_sam3d_mesh|sam3d_mesh\.py|rallymate_vision\.sam3d_mesh(?:\s|$)|--render-mesh)'
    })
    if ($activeRenderers.Count -gt 0) { throw 'An owned three-dimensional renderer is active. No service was stopped.' }
    if ($RequireUnsupervised -and @($Snapshot | Where-Object {
        $_.ProcessId -in $ownedIds -and $_.CommandLine -match 'rallymate_service\.local_worker'
    }).Count -gt 0) { throw 'Supervised worker shutdown was not confirmed. No service was stopped.' }
}

function Invoke-LocalServiceJson {
    param([int]$ApiPort, [string]$ApiKey, [string]$Path, [string]$Method = 'GET')
    $response = $reader = $null
    try {
        $request = [Net.HttpWebRequest]::Create("http://127.0.0.1:$ApiPort$Path")
        $request.Proxy = $null
        $request.AllowAutoRedirect = $false
        $request.Timeout = 5000
        $request.ReadWriteTimeout = 5000
        $request.Method = $Method
        $request.Headers['Authorization'] = "Bearer $ApiKey"
        if ($Method -eq 'POST') { $request.ContentLength = 0 }
        try { $response = $request.GetResponse() }
        catch [Net.WebException] {
            if ($null -eq $_.Exception.Response) { throw }
            $response = $_.Exception.Response
        }
        $reader = New-Object IO.StreamReader($response.GetResponseStream())
        $buffer = New-Object char[] 65537
        $count = $reader.ReadBlock($buffer, 0, $buffer.Length)
        if ($count -gt 65536) { throw 'Unexpectedly large local service control response.' }
        $body = (-join $buffer[0..($count - 1)]) | ConvertFrom-Json -ErrorAction Stop
        return [pscustomobject]@{ StatusCode = [int]$response.StatusCode; Body = $body }
    } finally {
        if ($null -ne $reader) { $reader.Dispose() }
        if ($null -ne $response) { $response.Close() }
    }
}

function Stop-LocalExecutorForRestart {
    param([int]$ApiPort, [string]$ApiKey, [int]$TimeoutSeconds = 50)
    # The API freezes admission before requesting idle shutdown. A dedicated
    # drain file exits only at worker loop boundaries; active work is not killed.
    $control = Invoke-LocalServiceJson -ApiPort $ApiPort -ApiKey $ApiKey -Path '/internal/maintenance/stop-executor' -Method POST
    if ($control.StatusCode -eq 404) {
        # First migration from the old daemon-thread API: it has no supervisor
        # to respawn a child. Never use this fallback for a newer supervised API.
        $health = Invoke-LocalServiceJson -ApiPort $ApiPort -ApiKey $ApiKey -Path '/health/ready'
        if ($health.StatusCode -ne 200 -or $health.Body.status -ne 'ready' -or
            $null -eq $health.Body.queue -or $null -ne $health.Body.executor -or
            [int]$health.Body.queue.running -gt 0 -or [int]$health.Body.queue.queued -gt 0) {
            throw 'Legacy API is not verifiably idle, or supervised API lacks maintenance control. No service was stopped.'
        }
        return 'legacy_idle'
    }
    if ($control.StatusCode -notin @(200,202) -or $control.Body.status -notin @('draining','stopped')) {
        throw 'Executor maintenance was refused. Current work was preserved; no process will be killed.'
    }
    $deadline = [DateTime]::UtcNow.AddSeconds($TimeoutSeconds)
    do {
        # not_ready/503 is expected during maintenance and is not a reason to
        # kill or restart a worker; require its explicit stopped confirmation.
        $health = Invoke-LocalServiceJson -ApiPort $ApiPort -ApiKey $ApiKey -Path '/health/ready'
        if ($health.StatusCode -notin @(200,503) -or $null -eq $health.Body.queue -or $health.Body.maintenance -ne $true) {
            throw 'Executor shutdown could not be verified. No process will be killed.'
        }
        if ([int]$health.Body.queue.running -gt 0 -or [int]$health.Body.queue.queued -gt 0) {
            throw 'Work appeared during maintenance. No process will be killed.'
        }
        if ($health.Body.executor.status -eq 'stopped') { return 'supervisor_stopped' }
        if ([DateTime]::UtcNow -ge $deadline) { break }
        Start-Sleep -Milliseconds 250
    } while ($true)
    throw 'Executor has not confirmed safe shutdown. Admission remains paused; inspect status and retry maintenance. No process will be killed.'
}
