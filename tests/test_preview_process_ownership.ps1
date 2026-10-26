param([string]$OutputRoot = "")
$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path -Parent $PSScriptRoot
. (Join-Path $projectRoot 'scripts/preview_process_ownership.ps1')
if (-not $OutputRoot) {
    $OutputRoot = Join-Path $projectRoot ('.codex_tmp/preview-owner-tests-' + [guid]::NewGuid().ToString('N'))
}
$baseTime = [datetime]'2026-10-20T00:00:00Z'
$venv = Join-Path $projectRoot 'runtime/rtmpose/.venv/Scripts/python.exe'
$script:StopCalls = @()
$script:Inject = ''

function New-FakeProcess([int]$Id, [int]$Parent, [int]$Seconds, [string]$Exe, [string]$Command = '') {
    [pscustomobject]@{ ProcessId = $Id; ParentProcessId = $Parent; CreationDate = $baseTime.AddSeconds($Seconds); ExecutablePath = $Exe; CommandLine = $Command }
}
function Reset-FakeProcesses {
    $script:FakeProcesses = @{}
    foreach ($item in @(
        (New-FakeProcess 10 1 0 $venv 'python scripts/run_dev_service.py --host 127.0.0.1 --port 8001'),
        (New-FakeProcess 11 10 1 'C:/Python/python.exe' 'python scripts/run_dev_service.py --host 127.0.0.1 --port 8001'),
        (New-FakeProcess 12 11 2 'C:/Python/python.exe' 'python sam3d_runner.py'),
        (New-FakeProcess 13 12 3 'C:/Tools/ffmpeg.exe' 'ffmpeg'),
        (New-FakeProcess 14 11 -1 'C:/Other/python.exe' 'older orphan whose parent PID was reused'),
        (New-FakeProcess 20 1 0 'C:/Tools/node.exe' 'node node_modules/vinext/dist/cli.js start --port 8000'),
        (New-FakeProcess 21 20 1 'C:/Tools/node.exe' 'node child'),
        (New-FakeProcess 99 1 0 'C:/Other/python.exe' 'unrelated python')
    )) { $script:FakeProcesses[$item.ProcessId] = $item }
    $script:StopCalls = @()
    $script:Inject = ''
}
function Get-FakeListeners {
    @(
        [pscustomobject]@{ ProcessId = 11; Port = 8001; Kind = 'api'; CapturedIdentity = Get-PreviewProcessIdentity $script:FakeProcesses[11] },
        [pscustomobject]@{ ProcessId = 20; Port = 8000; Kind = 'frontend'; CapturedIdentity = Get-PreviewProcessIdentity $script:FakeProcesses[20] }
    )
}
function Get-CimInstance {
    param([string]$ClassName, [string]$Filter)
    if ($script:Inject -eq 'reuse_after_first_stop' -and $script:StopCalls.Count -eq 1 -and $Filter -eq 'ProcessId = 12') {
        $script:FakeProcesses[12] = New-FakeProcess 12 99 20 'C:/Other/python.exe' 'replacement'
        $script:Inject = ''
    }
    if ($script:Inject -eq 'new_child_during_cleanup' -and $Filter -eq 'ParentProcessId = 12') {
        $script:FakeProcesses[30] = New-FakeProcess 30 12 20 'C:/Tools/ffmpeg.exe' 'late child'
        $script:Inject = ''
    }
    $values = @($script:FakeProcesses.Values)
    if ($Filter -match '^ProcessId = (\d+)$') { return @($values | Where-Object ProcessId -eq ([int]$Matches[1])) }
    if ($Filter -match '^ParentProcessId = (\d+)$') { return @($values | Where-Object ParentProcessId -eq ([int]$Matches[1])) }
    return $values
}
function Stop-PreviewOwnedProcess {
    param($Expected)
    $script:StopCalls += $Expected.ProcessId
    if ($script:Inject -eq 'new_child_at_termination' -and $Expected.ProcessId -eq 20) {
        $script:FakeProcesses[30] = New-FakeProcess 30 20 20 'C:/Tools/node.exe' 'late child'
        $script:Inject = ''
    }
    $script:FakeProcesses.Remove([int]$Expected.ProcessId)
    return 'stopped_and_exit_verified'
}
function Assert-True([bool]$Condition, [string]$Message) {
    if (-not $Condition) { throw "TEST FAILED: $Message" }
}
function Make-Plan {
    @(New-PreviewProcessStopPlan -Snapshot @($script:FakeProcesses.Values) -Listeners (Get-FakeListeners) -ProjectRoot $projectRoot)
}
function Expect-StopFailure($Plan, [string]$Name) {
    $failed = $false
    try { Stop-PreviewProcessPlan -Plan $Plan -ReceiptPath (Join-Path $OutputRoot "$Name.json") }
    catch { $failed = $true }
    Assert-True $failed "$Name must fail closed"
}

$results = @()
Reset-FakeProcesses
$plan = Make-Plan
Assert-True (($plan.ProcessId -join ',') -eq '13,12,11,21,10,20') 'only exact owned tree in deepest-first order'
Stop-PreviewProcessPlan -Plan $plan -ReceiptPath (Join-Path $OutputRoot 'success.json')
Assert-True (($script:StopCalls -join ',') -eq '13,12,11,21,10,20') 'stop order must leave parents until children have exited'
Assert-True ($script:FakeProcesses.ContainsKey(99) -and $script:FakeProcesses.ContainsKey(14)) 'unrelated and pre-owner orphan processes survive'
$results += 'owned_tree_depth_first_and_unrelated_preserved'

foreach ($defect in @('creation', 'executable', 'parent')) {
    Reset-FakeProcesses
    $plan = Make-Plan
    if ($defect -eq 'creation') { $script:FakeProcesses[12].CreationDate = $baseTime.AddHours(1) }
    if ($defect -eq 'executable') { $script:FakeProcesses[12].ExecutablePath = 'C:/Other/python.exe' }
    if ($defect -eq 'parent') { $script:FakeProcesses[12].ParentProcessId = 99 }
    Expect-StopFailure $plan $defect
    Assert-True ($script:StopCalls.Count -eq 0) 'identity mismatch must abort before any stop'
    $results += "preflight_identity_$defect"
}

Reset-FakeProcesses
$captured = Get-FakeListeners
$script:FakeProcesses[11].CreationDate = $baseTime.AddHours(1)
$failed = $false
try { New-PreviewProcessStopPlan -Snapshot @($script:FakeProcesses.Values) -Listeners $captured -ProjectRoot $projectRoot | Out-Null }
catch { $failed = $true }
Assert-True $failed 'port owner reuse between listener read and snapshot must be rejected'
$results += 'listener_pid_reuse_rejected'

Reset-FakeProcesses
$plan = Make-Plan
$script:FakeProcesses[30] = New-FakeProcess 30 11 20 'C:/Tools/ffmpeg.exe' 'late child'
Expect-StopFailure $plan 'new-child-before-stop'
Assert-True ($script:StopCalls.Count -eq 0) 'new child at preflight must abort before stop'
$results += 'late_child_preflight_rejected'

foreach ($injection in @('reuse_after_first_stop', 'new_child_during_cleanup')) {
    Reset-FakeProcesses
    $plan = Make-Plan
    $script:Inject = $injection
    Expect-StopFailure $plan $injection
    Assert-True (($script:StopCalls -join ',') -eq '13') 'cleanup must stop before touching replaced PID or live parent'
    Assert-True ($script:FakeProcesses.ContainsKey(11)) 'service parent remains alive after a detected cleanup race'
    $results += $injection
}

Reset-FakeProcesses
$plan = Make-Plan
$script:Inject = 'new_child_at_termination'
Expect-StopFailure $plan 'late-child-postflight'
Assert-True ($script:FakeProcesses.ContainsKey(30)) 'unexpected late child is not guessed at or silently ignored'
$results += 'late_child_postflight_blocks_replacement'

Reset-FakeProcesses
$script:FakeProcesses[10].ExecutablePath = 'C:/Other/python.exe'
$plan = Make-Plan
Assert-True ($plan.ProcessId -notcontains 10) 'never climb into a nonmatching launcher'
$results += 'unrelated_launcher_excluded'

$syntax = @()
foreach ($path in @('scripts/preview_process_ownership.ps1', 'scripts/restart_preview_service.ps1', 'scripts/start_local.ps1')) {
    $tokens = $null; $errors = $null
    [System.Management.Automation.Language.Parser]::ParseFile((Join-Path $projectRoot $path), [ref]$tokens, [ref]$errors) | Out-Null
    Assert-True (@($errors).Count -eq 0) "$path must parse"
    $syntax += $path
}
$restartSource = [IO.File]::ReadAllText((Join-Path $projectRoot 'scripts/restart_preview_service.ps1'))
Assert-True ($restartSource.IndexOf('An analysis is active or queued.') -lt $restartSource.IndexOf('Stop-PreviewProcessPlan -Plan')) 'busy health guard precedes cleanup'
Assert-True ($restartSource.Contains('-CpuThreads $CpuThreads')) 'CPU threads preserved'
Initialize-PreviewProcessNative
Assert-True ($null -ne ('RallyMate.PreviewProcessHandle' -as [type])) 'native exact-handle implementation compiles without touching processes'
[pscustomobject]@{ status = 'passed'; cases = $results; syntax_checked = $syntax; live_processes_touched = $false; receipts = $OutputRoot } | ConvertTo-Json -Depth 5
