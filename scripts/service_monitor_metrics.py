"""Read-only host metrics for the terminal monitor; psutil is optional.

HostMetrics(interface=None).sample(now=None) returns plain JSON-compatible dicts:
  cpu: {status, percent}; memory: {status, used_bytes, total_bytes, percent}
  network: {status, scope, selected_interface, interfaces, note}
    interfaces: [{name, is_up, status, reason, interval_seconds,
                  rx_bytes_per_second, tx_bytes_per_second}]
  gpu: {status, devices, sample_age_seconds, reason?}
    devices: [{index, name, status, utilization_percent,
               memory_used_bytes, memory_total_bytes}]
Status is ok, partial, warming_up, down, or unavailable. Unknown numbers are
None, never fabricated zeroes. CPU's first nonblocking sample is warming_up.
RAM usage is total minus available. Network rates are separate host-interface
counter deltas: no automatic sum of physical, VPN, or virtual interfaces.
GPU observations (including errors) are cached for 10 seconds and query only
the local nvidia-smi executable, with no shell/window and a 0.8-second timeout.
"""
from __future__ import annotations

from collections.abc import Mapping
import copy
import csv
import io
import math
import os
import shutil
import subprocess
import time

try:
    import psutil as _psutil
except (ImportError, OSError):
    _psutil = None


NETWORK_NOTE = "主机网卡流量，包含其他软件；各网卡不可相加，不代表对端上行速度。"
GPU_REFRESH_SECONDS = 10.0
GPU_TIMEOUT_SECONDS = 0.8


def _number(value):
    try:
        return (isinstance(value, (int, float)) and not isinstance(value, bool)
                and math.isfinite(value))
    except OverflowError:
        return False


def _count(value):
    return isinstance(value, int) and not isinstance(value, bool) and 0 <= value < 2 ** 64


def _percent(value):
    return float(value) if _number(value) and 0 <= value <= 100 else None


def _gpu_number(value):
    try:
        parsed = float(value)
    except (ValueError, TypeError, OverflowError):
        return None
    return parsed if math.isfinite(parsed) and parsed >= 0 else None


class HostMetrics:
    def __init__(self, interface: str | None = None):
        if interface is not None and (not isinstance(interface, str) or not interface.strip()):
            raise ValueError("interface must be a nonempty interface name")
        self.interface = interface
        self._psutil = _psutil
        self._cpu_primed = False
        self._net_previous = {}
        self._net_names = None
        self._net_at = None
        self._gpu_at = None
        self._gpu_value = None

    def sample(self, now: float | None = None) -> dict:
        """Sample without sleeping; now is an optional monotonic timestamp."""
        if now is None:
            now = time.monotonic()
        valid_clock = _number(now) and now >= 0
        return {
            "scope": "host",
            "sampled_at_monotonic": float(now) if valid_clock else None,
            "cpu": self._cpu(),
            "memory": self._memory(),
            "network": self._network(float(now) if valid_clock else None),
            "gpu": self._gpu(float(now) if valid_clock else None),
        }

    def _cpu(self):
        result = {"status": "unavailable", "percent": None}
        if self._psutil is None:
            return {**result, "reason": "psutil_unavailable"}
        try:
            value = _percent(self._psutil.cpu_percent(interval=None))
            if value is None:
                raise ValueError("invalid CPU counter")
            primed, self._cpu_primed = self._cpu_primed, True
            return {"status": "ok" if primed else "warming_up", "percent": value if primed else None}
        except Exception:
            self._cpu_primed = False
            return {**result, "reason": "cpu_sample_failed"}

    def _memory(self):
        result = {"status": "unavailable", "used_bytes": None, "total_bytes": None, "percent": None}
        if self._psutil is None:
            return {**result, "reason": "psutil_unavailable"}
        try:
            memory = self._psutil.virtual_memory()
            total, available = memory.total, memory.available
            if not _count(total) or total == 0 or not _count(available) or available > total:
                raise ValueError("invalid RAM counters")
            return {"status": "ok", "used_bytes": total - available, "total_bytes": total,
                    "percent": 100.0 * (total - available) / total}
        except Exception:
            return {**result, "reason": "memory_sample_failed"}

    def _reset_network(self):
        self._net_previous, self._net_names, self._net_at = {}, None, None

    def _network(self, now):
        result = {"status": "unavailable", "scope": "per_interface", "selected_interface": self.interface,
                  "interfaces": [], "note": NETWORK_NOTE}
        if now is None or self._psutil is None:
            self._reset_network()
            return {**result, "reason": "invalid_clock" if now is None else "psutil_unavailable"}
        try:
            # Disable psutil's wrap compensation so resets cannot masquerade as
            # traffic; reset both directions if either raw counter goes back.
            counters = self._psutil.net_io_counters(pernic=True, nowrap=False)
            if not isinstance(counters, Mapping):
                raise ValueError("invalid interface counters")
        except Exception:
            self._reset_network()
            return {**result, "reason": "network_sample_failed"}
        try:
            stats = self._psutil.net_if_stats()
            if not isinstance(stats, Mapping):
                stats = {}
        except Exception:
            stats = {}
        names = {name for name in counters if isinstance(name, str) and name}
        changed = self._net_names is not None and names != self._net_names
        elapsed = now - self._net_at if self._net_at is not None else None
        clock_reset = elapsed is not None and (not math.isfinite(elapsed) or elapsed <= 0)
        previous = {} if changed or clock_reset else self._net_previous
        current, rows = {}, []
        for name in sorted(names):
            item = counters[name]
            rx, tx = getattr(item, "bytes_recv", None), getattr(item, "bytes_sent", None)
            up = getattr(stats.get(name), "isup", None)
            up = up if isinstance(up, bool) else None
            row = {"name": name, "is_up": up, "status": "warming_up", "reason": "baseline",
                   "interval_seconds": None, "rx_bytes_per_second": None, "tx_bytes_per_second": None}
            if not _count(rx) or not _count(tx):
                row.update(status="unavailable", reason="invalid_counter")
            else:
                current[name] = (rx, tx, up)
                prior = previous.get(name)
                if up is False:
                    row.update(status="down", reason="interface_down")
                elif changed or prior is not None and up != prior[2]:
                    row["reason"] = "interface_changed"
                elif clock_reset:
                    row["reason"] = "clock_reset"
                elif prior is not None and (rx < prior[0] or tx < prior[1]):
                    row["reason"] = "counter_reset"
                elif prior is not None and elapsed is not None:
                    rates = ((rx - prior[0]) / elapsed, (tx - prior[1]) / elapsed)
                    if all(math.isfinite(value) for value in rates):
                        row.update(status="ok", reason=None, interval_seconds=elapsed,
                                   rx_bytes_per_second=rates[0], tx_bytes_per_second=rates[1])
                    else:
                        row.update(status="unavailable", reason="invalid_interval")
            if self.interface is None or name == self.interface:
                rows.append(row)
        self._net_previous, self._net_names, self._net_at = current, names, now
        if not rows:
            return {**result, "reason": "interface_not_found" if self.interface else "no_interfaces"}
        statuses = {row["status"] for row in rows}
        status = next(iter(statuses)) if len(statuses) == 1 else "partial"
        return {**result, "status": status, "interfaces": rows}

    def _gpu(self, now):
        if now is None:
            self._gpu_at, self._gpu_value = None, None
            return {"status": "unavailable", "devices": [], "sample_age_seconds": None, "reason": "invalid_clock"}
        age = now - self._gpu_at if self._gpu_at is not None else None
        if self._gpu_value is None or age is None or age < 0 or age >= GPU_REFRESH_SECONDS:
            self._gpu_value, self._gpu_at = self._query_gpu(), now
            age = 0.0
        # Callers may decorate rows for display without poisoning the cache.
        return {**copy.deepcopy(self._gpu_value), "sample_age_seconds": age}

    @staticmethod
    def _query_gpu():
        empty = {"status": "unavailable", "devices": []}
        try:
            executable = shutil.which("nvidia-smi")
            if not executable:
                return {**empty, "reason": "nvidia_smi_missing"}
            process = subprocess.run(
                [executable, "--query-gpu=index,name,utilization.gpu,memory.used,memory.total",
                 "--format=csv,noheader,nounits"],
                stdin=subprocess.DEVNULL, capture_output=True, text=True,
                encoding="utf-8", errors="replace", timeout=GPU_TIMEOUT_SECONDS,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0) if os.name == "nt" else 0,
                check=False,
            )
            if process.returncode != 0:
                return {**empty, "reason": "nvidia_smi_failed"}
            if not isinstance(process.stdout, str) or len(process.stdout) > 65536:
                return {**empty, "reason": "invalid_gpu_response"}
            devices, seen = [], set()
            for fields in csv.reader(io.StringIO(process.stdout), skipinitialspace=True):
                if not fields:
                    continue
                if len(fields) != 5:
                    return {**empty, "reason": "invalid_gpu_response"}
                index_text, name, utilization, used, total = (field.strip() for field in fields)
                if not index_text.isdecimal() or int(index_text) in seen or len(devices) >= 64:
                    return {**empty, "reason": "invalid_gpu_response"}
                index = int(index_text)
                seen.add(index)
                utilization = _percent(_gpu_number(utilization))
                used, total = _gpu_number(used), _gpu_number(total)
                if (used is None or total is None or total <= 0 or used > total
                        or total >= 2 ** 44):
                    used = total = None
                device = {"index": index, "name": "".join(c for c in name if c.isprintable())[:120],
                          "status": "ok" if utilization is not None and used is not None else "partial",
                          "utilization_percent": utilization,
                          "memory_used_bytes": int(used * 1048576) if used is not None else None,
                          "memory_total_bytes": int(total * 1048576) if total is not None else None}
                devices.append(device)
            if not devices:
                return {**empty, "reason": "invalid_gpu_response"}
            return {"status": "ok" if all(row["status"] == "ok" for row in devices) else "partial", "devices": devices}
        except subprocess.TimeoutExpired:
            return {**empty, "reason": "nvidia_smi_timeout"}
        except Exception:
            return {**empty, "reason": "nvidia_smi_failed"}
