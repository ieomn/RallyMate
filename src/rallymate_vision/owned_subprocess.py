"""A renderer process tree owned for its entire lifetime, including Windows venv launchers."""
from __future__ import annotations

import ctypes
from ctypes import wintypes
import os
import signal
import subprocess
import time


class ProcessTreeCleanupError(RuntimeError):
    """Do not admit new GPU work if the preceding tree cannot be stopped."""


class _WindowsJob:
    def __init__(self):
        class BasicLimits(ctypes.Structure):
            _fields_ = [("PerProcessUserTimeLimit", ctypes.c_longlong), ("PerJobUserTimeLimit", ctypes.c_longlong),
                        ("LimitFlags", wintypes.DWORD), ("MinimumWorkingSetSize", ctypes.c_size_t),
                        ("MaximumWorkingSetSize", ctypes.c_size_t), ("ActiveProcessLimit", wintypes.DWORD),
                        ("Affinity", ctypes.c_size_t), ("PriorityClass", wintypes.DWORD),
                        ("SchedulingClass", wintypes.DWORD)]
        class IoCounters(ctypes.Structure):
            _fields_ = [(name, ctypes.c_ulonglong) for name in ("ReadOperationCount", "WriteOperationCount",
                        "OtherOperationCount", "ReadTransferCount", "WriteTransferCount", "OtherTransferCount")]
        class ExtendedLimits(ctypes.Structure):
            _fields_ = [("BasicLimitInformation", BasicLimits), ("IoInfo", IoCounters),
                        ("ProcessMemoryLimit", ctypes.c_size_t), ("JobMemoryLimit", ctypes.c_size_t),
                        ("PeakProcessMemoryUsed", ctypes.c_size_t), ("PeakJobMemoryUsed", ctypes.c_size_t)]
        class Accounting(ctypes.Structure):
            _fields_ = [(name, ctypes.c_longlong) for name in ("TotalUserTime", "TotalKernelTime",
                        "ThisPeriodTotalUserTime", "ThisPeriodTotalKernelTime")] + [
                        (name, wintypes.DWORD) for name in ("TotalPageFaultCount", "TotalProcesses",
                        "ActiveProcesses", "TotalTerminatedProcesses")]
        self.Accounting = Accounting
        self.kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        self.kernel.CreateJobObjectW.argtypes = [ctypes.c_void_p, wintypes.LPCWSTR]
        self.kernel.CreateJobObjectW.restype = wintypes.HANDLE
        self.kernel.SetInformationJobObject.argtypes = [wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD]
        self.kernel.SetInformationJobObject.restype = wintypes.BOOL
        self.kernel.AssignProcessToJobObject.argtypes = [wintypes.HANDLE, wintypes.HANDLE]
        self.kernel.AssignProcessToJobObject.restype = wintypes.BOOL
        self.kernel.TerminateJobObject.argtypes = [wintypes.HANDLE, wintypes.UINT]
        self.kernel.TerminateJobObject.restype = wintypes.BOOL
        self.kernel.QueryInformationJobObject.argtypes = [wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p,
                                                        wintypes.DWORD, ctypes.c_void_p]
        self.kernel.QueryInformationJobObject.restype = wintypes.BOOL
        self.kernel.CloseHandle.argtypes = [wintypes.HANDLE]
        self.kernel.CloseHandle.restype = wintypes.BOOL
        self.kernel.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
        self.kernel.OpenProcess.restype = wintypes.HANDLE
        self.kernel.IsProcessInJob.argtypes = [wintypes.HANDLE, wintypes.HANDLE, ctypes.POINTER(wintypes.BOOL)]
        self.kernel.IsProcessInJob.restype = wintypes.BOOL
        self.kernel.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
        self.kernel.WaitForSingleObject.restype = wintypes.DWORD
        self.handle = self.kernel.CreateJobObjectW(None, None)
        if not self.handle:
            raise ctypes.WinError(ctypes.get_last_error())
        limits = ExtendedLimits()
        limits.BasicLimitInformation.LimitFlags = 0x2000  # JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
        if not self.kernel.SetInformationJobObject(self.handle, 9, ctypes.byref(limits), ctypes.sizeof(limits)):
            error = ctypes.WinError(ctypes.get_last_error())
            self.close()
            raise error

    def assign_and_resume(self, process):
        if not self.kernel.AssignProcessToJobObject(self.handle, int(process._handle)):
            raise ctypes.WinError(ctypes.get_last_error())
        ntdll = ctypes.WinDLL("ntdll")
        ntdll.NtResumeProcess.argtypes = [wintypes.HANDLE]
        ntdll.NtResumeProcess.restype = ctypes.c_long
        status = ntdll.NtResumeProcess(int(process._handle))
        if status < 0:
            raise OSError(f"NtResumeProcess failed with status {status}")

    def active(self) -> int:
        accounting = self.Accounting()
        if not self.kernel.QueryInformationJobObject(self.handle, 1, ctypes.byref(accounting),
                                                     ctypes.sizeof(accounting), None):
            raise ctypes.WinError(ctypes.get_last_error())
        return int(accounting.ActiveProcesses)

    def terminate(self):
        if not self.kernel.TerminateJobObject(self.handle, 1):
            raise ctypes.WinError(ctypes.get_last_error())

    def member_handles(self):
        # Retain OS objects, not just PIDs: ActiveProcesses can reach zero just
        # before the actual process object becomes signaled on Windows.
        capacity = 32
        while capacity <= 65536:
            buffer = ctypes.create_string_buffer(8 + ctypes.sizeof(ctypes.c_size_t) * capacity)
            if self.kernel.QueryInformationJobObject(self.handle, 3, buffer, len(buffer), None):
                count = wintypes.DWORD.from_buffer(buffer, 4).value
                identifiers = list((ctypes.c_size_t * count).from_buffer(buffer, 8))
                break
            if ctypes.get_last_error() != 234:  # ERROR_MORE_DATA
                raise ctypes.WinError(ctypes.get_last_error())
            capacity *= 2
        else:
            raise ProcessTreeCleanupError("renderer process tree exceeded ownership bound")
        handles = []
        try:
            for identifier in identifiers:
                handle = self.kernel.OpenProcess(0x00101000, False, identifier)  # synchronize + query limited
                if not handle:
                    if ctypes.get_last_error() == 87:  # exited before OpenProcess
                        continue
                    raise ctypes.WinError(ctypes.get_last_error())
                member = wintypes.BOOL()
                if not self.kernel.IsProcessInJob(handle, self.handle, ctypes.byref(member)):
                    self.kernel.CloseHandle(handle)
                    raise ctypes.WinError(ctypes.get_last_error())
                if member.value:
                    handles.append(handle)
                else:  # PID was reused; never act on the replacement process.
                    self.kernel.CloseHandle(handle)
            return handles
        except BaseException:
            for handle in handles:
                self.kernel.CloseHandle(handle)
            raise

    def close(self):
        if self.handle:
            handle, self.handle = self.handle, None
            self.kernel.CloseHandle(handle)


class OwnedProcess:
    """Launch suspended, bind the complete tree, then resume before any child runs.

    Windows kernel ownership survives launcher exit and kills all descendants on
    parent crash. The job handle is non-inheritable and never shared with children.
    """
    def __init__(self, arguments, **kwargs):
        self.job = _WindowsJob() if os.name == "nt" else None
        self.process = None
        self.closed = False
        try:
            if self.job:
                flags = kwargs.pop("creationflags", 0) | subprocess.CREATE_NO_WINDOW | 0x4  # CREATE_SUSPENDED
                self.process = subprocess.Popen(arguments, creationflags=flags, **kwargs)
                self.job.assign_and_resume(self.process)
            else:
                self.process = subprocess.Popen(arguments, start_new_session=True, **kwargs)
        except BaseException:
            # Assignment can fail while the direct process is still suspended.
            try:
                if self.process is not None and self.process.poll() is None:
                    self.process.kill()
                    self.process.wait(timeout=5)
            finally:
                if self.job:
                    self.job.close()
            raise

    @property
    def returncode(self):
        return self.process.returncode

    def poll(self):
        return self.process.poll()

    def stop(self):
        try:
            if self.job:
                handles = self.job.member_handles()
                try:
                    self.job.terminate()
                    deadline = time.monotonic() + 5
                    for handle in handles:
                        remaining = max(0, int((deadline - time.monotonic()) * 1000))
                        if self.job.kernel.WaitForSingleObject(handle, remaining) != 0:
                            raise ProcessTreeCleanupError("renderer descendant exit was not confirmed")
                    while self.job.active():
                        if time.monotonic() >= deadline:
                            raise ProcessTreeCleanupError("renderer process tree did not exit after termination")
                        time.sleep(.02)
                finally:
                    for handle in handles:
                        self.job.kernel.CloseHandle(handle)
            else:
                # The session leader may exit while its descendants still own
                # the process group. Its return code says nothing about them.
                try:
                    os.killpg(self.process.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass  # The whole process group has already disappeared.
            self.process.wait(timeout=5)
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise ProcessTreeCleanupError("renderer process tree termination failed") from exc

    def close(self):
        if self.closed:
            return
        try:
            if self.job is None or self.job.active() or self.process.poll() is None:
                self.stop()
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise ProcessTreeCleanupError("renderer process tree cleanup could not be verified") from exc
        finally:
            if self.job:
                self.job.close()
            self.closed = True
