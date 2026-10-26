# Functions only: loading this file never enumerates or stops a process.
function Initialize-PreviewProcessNative {
    if ('RallyMate.PreviewProcessHandle' -as [type]) { return }
    Add-Type -TypeDefinition @'
using System;
using System.ComponentModel;
using System.IO;
using System.Runtime.InteropServices;
using System.Text;
namespace RallyMate {
    public static class PreviewProcessHandle {
        [DllImport("kernel32.dll", SetLastError=true)] static extern IntPtr OpenProcess(uint access, bool inherit, int pid);
        [DllImport("kernel32.dll", SetLastError=true)] static extern bool GetProcessTimes(IntPtr process, out long creation, out long exit, out long kernel, out long user);
        [DllImport("kernel32.dll", CharSet=CharSet.Unicode, SetLastError=true)] static extern bool QueryFullProcessImageName(IntPtr process, uint flags, StringBuilder path, ref int size);
        [DllImport("kernel32.dll", SetLastError=true)] static extern bool TerminateProcess(IntPtr process, uint exitCode);
        [DllImport("kernel32.dll", SetLastError=true)] static extern uint WaitForSingleObject(IntPtr handle, uint milliseconds);
        [DllImport("kernel32.dll")] static extern bool CloseHandle(IntPtr handle);
        // One retained OS handle closes the PID-reuse gap between verification
        // and termination. Never reopen a bare PID after checking its identity.
        public static string TerminateOwned(int pid, long expectedTicks, string expectedExe) {
            IntPtr handle = OpenProcess(0x00100000 | 0x1000 | 0x0001, false, pid);
            if (handle == IntPtr.Zero) {
                int error = Marshal.GetLastWin32Error();
                if (error == 87) return "already_exited";
                throw new Win32Exception(error, "Cannot open the exact owned process.");
            }
            try {
                long created, exited, kernel, user;
                if (!GetProcessTimes(handle, out created, out exited, out kernel, out user))
                    throw new Win32Exception(Marshal.GetLastWin32Error(), "Cannot verify owned process creation time.");
                // Win32_Process CreationDate has microsecond precision.
                if (DateTime.FromFileTimeUtc(created).Ticks / 10 != expectedTicks / 10)
                    throw new InvalidOperationException("PID identity changed before handle acquisition; replacement was left untouched.");
                var path = new StringBuilder(32768);
                int size = path.Capacity;
                if (!QueryFullProcessImageName(handle, 0, path, ref size))
                    throw new Win32Exception(Marshal.GetLastWin32Error(), "Cannot verify owned process executable.");
                if (!String.Equals(Path.GetFullPath(path.ToString()), Path.GetFullPath(expectedExe), StringComparison.OrdinalIgnoreCase))
                    throw new InvalidOperationException("Executable identity changed; process was left untouched.");
                if (WaitForSingleObject(handle, 0) == 0) return "already_exited";
                if (!TerminateProcess(handle, 1)) {
                    int error = Marshal.GetLastWin32Error();
                    if (WaitForSingleObject(handle, 0) == 0) return "already_exited";
                    throw new Win32Exception(error, "Cannot terminate the exact owned process.");
                }
                if (WaitForSingleObject(handle, 10000) != 0)
                    throw new InvalidOperationException("Owned process did not exit within ten seconds.");
                return "stopped_and_exit_verified";
            } finally { CloseHandle(handle); }
        }
    }
}
'@
}

function Stop-PreviewOwnedProcess {
    param([Parameter(Mandatory = $true)]$Expected)
    Initialize-PreviewProcessNative
    return [RallyMate.PreviewProcessHandle]::TerminateOwned(
        $Expected.ProcessId, $Expected.CreationUtcTicks, $Expected.ExecutablePath)
}

function Get-PreviewProcessIdentity {
    param([Parameter(Mandatory = $true)]$Process)
    if ([int]$Process.ProcessId -le 0 -or $null -eq $Process.CreationDate -or
        [string]::IsNullOrWhiteSpace([string]$Process.ExecutablePath) -or
        -not [IO.Path]::IsPathRooted([string]$Process.ExecutablePath)) {
        throw "A service process identity could not be verified. No unverified process will be stopped."
    }
    [pscustomobject]@{
        ProcessId = [int]$Process.ProcessId
        ParentProcessId = [int]$Process.ParentProcessId
        CreationUtcTicks = ([datetime]$Process.CreationDate).ToUniversalTime().Ticks
        ExecutablePath = [IO.Path]::GetFullPath([string]$Process.ExecutablePath)
    }
}

function Test-PreviewProcessIdentity {
    param([Parameter(Mandatory = $true)]$Expected, [Parameter(Mandatory = $true)]$Actual)
    $currentIdentity = Get-PreviewProcessIdentity -Process $Actual
    return ($currentIdentity.ProcessId -eq $Expected.ProcessId -and
        $currentIdentity.ParentProcessId -eq $Expected.ParentProcessId -and
        $currentIdentity.CreationUtcTicks -eq $Expected.CreationUtcTicks -and
        [string]::Equals($currentIdentity.ExecutablePath, $Expected.ExecutablePath,
            [StringComparison]::OrdinalIgnoreCase))
}

function New-PreviewProcessStopPlan {
    param(
        [Parameter(Mandatory = $true)][AllowEmptyCollection()][object[]]$Snapshot,
        [Parameter(Mandatory = $true)][AllowEmptyCollection()][object[]]$Listeners,
        [Parameter(Mandatory = $true)][string]$ProjectRoot
    )
    $processById = @{}
    foreach ($snapshotProcess in $Snapshot) {
        $processById[[int]$snapshotProcess.ProcessId] = $snapshotProcess
    }
    $roots = @{}
    $expectedVenv = [IO.Path]::GetFullPath((Join-Path $ProjectRoot 'runtime/rtmpose/.venv/Scripts/python.exe'))
    foreach ($listener in $Listeners) {
        $listenerId = [int]$listener.ProcessId
        if (-not $processById.ContainsKey($listenerId)) {
            throw "A selected port owner exited before its process tree was captured. Retry the restart."
        }
        $owner = $processById[$listenerId]
        $ownerIdentity = Get-PreviewProcessIdentity -Process $owner
        if ($null -eq $listener.CapturedIdentity -or
            -not (Test-PreviewProcessIdentity -Expected $listener.CapturedIdentity -Actual $owner)) {
            throw "A selected port owner changed identity before the snapshot. No replacement process will be stopped."
        }
        $roots[$listenerId] = $true
        # Windows venv python.exe can be a launcher whose child owns the socket.
        # Include only that exact project interpreter with the same API command;
        # never climb into the calling PowerShell/Codex process.
        if ($listener.Kind -eq 'api' -and $processById.ContainsKey($ownerIdentity.ParentProcessId)) {
            $parent = $processById[$ownerIdentity.ParentProcessId]
            if (-not [string]::IsNullOrWhiteSpace([string]$parent.ExecutablePath) -and
                [string]::Equals([IO.Path]::GetFullPath([string]$parent.ExecutablePath), $expectedVenv,
                    [StringComparison]::OrdinalIgnoreCase) -and
                [string]$parent.CommandLine -match 'scripts[\\/]run_dev_service\.py(?:"|\s)' -and
                [string]$parent.CommandLine -match "--port\s+$([int]$listener.Port)(?:\s|$)") {
                $parentIdentity = Get-PreviewProcessIdentity -Process $parent
                if ($parentIdentity.CreationUtcTicks -gt $ownerIdentity.CreationUtcTicks) {
                    throw "API launcher ownership changed. No process will be stopped."
                }
                $roots[$parentIdentity.ProcessId] = $true
                $roots.Remove($listenerId)
            }
        }
    }
    $owned = @{}
    $pending = New-Object 'System.Collections.Generic.Queue[object]'
    foreach ($rootId in $roots.Keys) {
        $pending.Enqueue([pscustomobject]@{ ProcessId = [int]$rootId; Depth = 0 })
    }
    while ($pending.Count -gt 0) {
        $entry = $pending.Dequeue()
        if ($owned.ContainsKey($entry.ProcessId)) { continue }
        if ($entry.Depth -gt 128 -or $entry.ProcessId -eq $PID) {
            throw "Invalid service process tree. No process will be stopped."
        }
        $identity = Get-PreviewProcessIdentity -Process $processById[$entry.ProcessId]
        $identity | Add-Member -NotePropertyName Depth -NotePropertyValue $entry.Depth
        $owned[$entry.ProcessId] = $identity
        foreach ($child in $Snapshot) {
            if ([int]$child.ParentProcessId -ne $entry.ProcessId) { continue }
            $childIdentity = Get-PreviewProcessIdentity -Process $child
            # A reused PID is not proof that an older orphan belongs to this run.
            if ($childIdentity.CreationUtcTicks -lt $identity.CreationUtcTicks) { continue }
            $pending.Enqueue([pscustomobject]@{ ProcessId = $childIdentity.ProcessId; Depth = $entry.Depth + 1 })
        }
    }
    return @($owned.Values | Sort-Object @{ Expression = 'Depth'; Descending = $true }, ProcessId)
}

function Stop-PreviewProcessPlan {
    param(
        [Parameter(Mandatory = $true)][AllowEmptyCollection()][object[]]$Plan,
        [Parameter(Mandatory = $true)][string]$ReceiptPath
    )
    $receipt = [ordered]@{
        schema = 'preview-owned-process-cleanup-v1'
        started_at = [datetime]::UtcNow.ToString('o')
        status = 'failed'
        planned_processes = @($Plan)
        outcomes = @()
    }
    $ownedById = @{}
    $exitVerifiedAt = @{}
    foreach ($expected in $Plan) { $ownedById[[int]$expected.ProcessId] = $expected }
    try {
        # Check the complete snapshot again before the first mutation. A new
        # child or PID reuse causes a retry, not a partial guessed tree cleanup.
        $fresh = @(Get-CimInstance Win32_Process)
        foreach ($expected in $Plan) {
            $current = @($fresh | Where-Object { [int]$_.ProcessId -eq $expected.ProcessId })
            if ($current.Count -gt 0 -and -not (Test-PreviewProcessIdentity -Expected $expected -Actual $current[0])) {
                throw "A planned PID was reused or changed identity. No replacement process will be stopped."
            }
        }
        foreach ($candidate in $fresh) {
            $parentId = [int]$candidate.ParentProcessId
            if ($ownedById.ContainsKey($parentId) -and -not $ownedById.ContainsKey([int]$candidate.ProcessId) -and
                ([datetime]$candidate.CreationDate).ToUniversalTime().Ticks -ge $ownedById[$parentId].CreationUtcTicks) {
                throw "The service acquired a new child after the snapshot. Retry with a fresh idle check."
            }
        }
        foreach ($expected in @($Plan | Sort-Object @{ Expression = 'Depth'; Descending = $true }, ProcessId)) {
            $current = @(Get-CimInstance Win32_Process -Filter "ProcessId = $($expected.ProcessId)")
            if ($current.Count -eq 0) {
                $receipt.outcomes += [pscustomobject]@{ process_id = $expected.ProcessId; status = 'already_exited' }
                $exitVerifiedAt[$expected.ProcessId] = [datetime]::UtcNow.Ticks
                continue
            }
            if (-not (Test-PreviewProcessIdentity -Expected $expected -Actual $current[0])) {
                throw "A planned PID changed identity during cleanup. The replacement process was left untouched."
            }
            # A child created during cleanup is not silently orphaned by killing
            # its parent. Leave that parent alive and require a fresh idle check.
            $children = @(Get-CimInstance Win32_Process -Filter "ParentProcessId = $($expected.ProcessId)")
            foreach ($child in $children) {
                if (([datetime]$child.CreationDate).ToUniversalTime().Ticks -ge $expected.CreationUtcTicks) {
                    throw "A child is still attached to a planned parent. Cleanup stopped before terminating that parent."
                }
            }
            $stopOutcome = Stop-PreviewOwnedProcess -Expected $expected
            $remaining = @(Get-CimInstance Win32_Process -Filter "ProcessId = $($expected.ProcessId)")
            if ($remaining.Count -gt 0 -and (Test-PreviewProcessIdentity -Expected $expected -Actual $remaining[0])) {
                throw "An owned process did not exit. No replacement service will be started."
            }
            $receipt.outcomes += [pscustomobject]@{ process_id = $expected.ProcessId; status = $stopOutcome }
            $exitVerifiedAt[$expected.ProcessId] = [datetime]::UtcNow.Ticks
        }
        # A process can spawn between the final child check and termination.
        # Detect a remaining late child without guessing its ownership or
        # killing a reused PID. The caller must not launch replacement workers.
        foreach ($candidate in @(Get-CimInstance Win32_Process)) {
            $parentId = [int]$candidate.ParentProcessId
            if (-not $ownedById.ContainsKey($parentId) -or -not $exitVerifiedAt.ContainsKey($parentId)) { continue }
            $childCreated = ([datetime]$candidate.CreationDate).ToUniversalTime().Ticks
            if ($childCreated -ge $ownedById[$parentId].CreationUtcTicks -and $childCreated -le $exitVerifiedAt[$parentId]) {
                throw "A possible late service descendant remains after cleanup. No replacement service will be started."
            }
        }
        $receipt.status = 'owned_processes_exited'
    } catch {
        $receipt.failure = $_.Exception.Message
        throw
    } finally {
        $receipt.finished_at = [datetime]::UtcNow.ToString('o')
        $receiptDirectory = Split-Path -Parent $ReceiptPath
        New-Item -ItemType Directory -Force -Path $receiptDirectory | Out-Null
        $receipt | ConvertTo-Json -Depth 8 | Set-Content -LiteralPath $ReceiptPath -Encoding UTF8
    }
}
