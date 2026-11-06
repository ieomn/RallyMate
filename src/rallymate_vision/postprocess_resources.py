"""Cooperative limits and diagnostics for CPU postprocessing, without psutil."""
from __future__ import annotations

import ctypes
import json
import logging
import os
from pathlib import Path
import time

LOGGER = logging.getLogger("rallymate.postprocessing")


class PostprocessResourceLimit(RuntimeError):
    pass


class PostprocessOutputLimit(PostprocessResourceLimit):
    """The generated output exceeds its finite delivery size contract."""


def encode_bounded_json(payload, *, max_bytes, cancellation_callback):
    """Encode without a second full Unicode copy, checking limits as we go."""
    cancellation_callback()
    encoded = bytearray()
    encoder = json.JSONEncoder(ensure_ascii=False, separators=(",", ":"), allow_nan=False)
    for index, token in enumerate(encoder.iterencode(payload)):
        if index % 4096 == 0:
            cancellation_callback()
        chunk = token.encode("utf-8")
        if len(encoded) + len(chunk) > max_bytes:
            raise PostprocessOutputLimit("报告传输内容超过大小预算，已停止生成；分析产物保留")
        encoded.extend(chunk)
    cancellation_callback()
    return bytes(encoded)


def process_memory_bytes() -> int:
    if os.name == "nt":
        from ctypes import wintypes
        class Counters(ctypes.Structure):
            _fields_ = [("cb", wintypes.DWORD), ("faults", wintypes.DWORD)] + [
                (name, ctypes.c_size_t) for name in
                ("peak_ws", "ws", "peak_pool_paged", "pool_paged", "peak_pool_nonpaged",
                 "pool_nonpaged", "pagefile", "peak_pagefile", "private")]
        values = Counters()
        values.cb = ctypes.sizeof(values)
        kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        psapi = ctypes.WinDLL("psapi", use_last_error=True)
        kernel.GetCurrentProcess.restype = wintypes.HANDLE
        psapi.GetProcessMemoryInfo.argtypes = [wintypes.HANDLE, ctypes.c_void_p, wintypes.DWORD]
        if psapi.GetProcessMemoryInfo(kernel.GetCurrentProcess(), ctypes.byref(values), values.cb):
            return int(values.private)
    else:
        try:
            for line in Path("/proc/self/status").read_text().splitlines():
                if line.startswith("VmRSS:"):
                    return int(line.split()[1]) * 1024
        except OSError:
            pass
    return 0


class PostprocessBudget:
    """Sample current API/worker memory and stop expensive work cooperatively.

    Includes the current Python process; excludes the separate SAM GPU worker.
    This is a guard between records/chunks, not an OS hard allocation limit.
    """
    def __init__(self, *, cancellation_callback=None, heartbeat=None,
                 max_memory_bytes=None, max_seconds=None):
        self.max_bytes = (int(os.getenv("RALLYMATE_POSTPROCESS_MEMORY_MB", "16384")) * 1024**2
                          if max_memory_bytes is None else max_memory_bytes)
        self.max_seconds = (float(os.getenv("RALLYMATE_POSTPROCESS_TIMEOUT_SECONDS", "1800"))
                            if max_seconds is None else max_seconds)
        if self.max_bytes <= 0 or self.max_seconds <= 0:
            raise ValueError("postprocessing resource limits must be positive")
        self.cancellation_callback = cancellation_callback
        self.heartbeat = heartbeat
        self.started = time.monotonic()
        self.last_check = 0.0
        self.last_heartbeat = self.started
        self.peak_bytes = 0
        self.phase = "postprocessing"

    def check(self, *, force=False):
        now = time.monotonic()
        if not force and now - self.last_check < 0.25:
            return
        self.last_check = now
        if self.cancellation_callback:
            self.cancellation_callback()
        memory = process_memory_bytes()
        self.peak_bytes = max(self.peak_bytes, memory)
        elapsed = now - self.started
        if memory > self.max_bytes:
            raise PostprocessResourceLimit(
                f"报告后处理内存超过预算（{self.max_bytes // 1024**2} MiB），已停止；原始上传和已生成产物保留")
        if elapsed > self.max_seconds:
            raise PostprocessResourceLimit(
                f"报告后处理超过时间预算（{self.max_seconds:g} 秒），已停止；原始上传和已生成产物保留")
        if now - self.last_heartbeat >= 2:
            self.last_heartbeat = now
            if self.heartbeat:
                self.heartbeat(self.phase)

    def stage(self, phase):
        if phase != self.phase:
            LOGGER.info("phase=%s elapsed_seconds=%.3f sampled_peak_memory_mib=%.1f",
                        phase, time.monotonic() - self.started, self.peak_bytes / 1024**2)
        self.phase = phase
        self.check(force=True)

    def snapshot(self):
        return {"scope": "current_process_private_bytes_windows_rss_linux_sampled_not_os_hard_limit",
                "sampled_peak_memory_bytes": self.peak_bytes,
                "memory_limit_bytes": self.max_bytes, "time_limit_seconds": self.max_seconds,
                "elapsed_seconds": round(time.monotonic() - self.started, 3)}
