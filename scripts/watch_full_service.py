"""Read-only local upload/analysis monitor; no API credentials or service control."""
from __future__ import annotations

import argparse
from collections import deque
from contextlib import closing
from datetime import datetime, timedelta, timezone
import json
import hashlib
import stat
import math
from pathlib import Path
import re
import sqlite3
import sys
import time
import unicodedata

try:
    from service_monitor_terminal import TerminalRenderer
    from service_monitor_uploads import read_uploads as _read_uploads
    from service_monitor_metrics import HostMetrics
except ModuleNotFoundError:
    from scripts.service_monitor_terminal import TerminalRenderer
    from scripts.service_monitor_uploads import read_uploads as _read_uploads
    from scripts.service_monitor_metrics import HostMetrics

ROOT = Path(__file__).resolve().parents[1]
RECENT_SECONDS = 24 * 60 * 60
LIMIT = 8
LABELS = {
    "queued": "排队中", "running": "分析中", "succeeded": "分析完成", "failed": "失败",
    "cancelled": "已取消", "completed": "分析完成", "loading_models": "加载模型",
    "processing": "分析视频", "inference": "模型推理", "preparing_replay": "准备回放",
    "tracking_player": "整理轨迹", "recognizing_strokes": "识别挥拍", "detecting_events": "划分动作",
    "extracting_features": "计算测量", "scoring": "整理测量", "writing_results": "保存结果",
    "finalizing": "生成报告", "scoring_readiness": "校验报告",
}
LOG_NAMES = ("api.err.log", "web.err.log", "api.log")


def safe_text(value: object, length: int = 90) -> str:
    text = "".join(" " if unicodedata.category(char).startswith("C") else char for char in str(value or ""))
    text = re.sub(r"\bsk-[A-Za-z0-9_-]{12,}", "[已隐藏密钥]", text)
    text = re.sub(r"(?i)\bbearer\s+[A-Za-z0-9._~+/-]+=*", "Bearer [已隐藏]", text)
    text = re.sub(r"(?i)(authorization|api[_-]?key|token)(\s*[:=]\s*)\S+", r"\1\2[已隐藏]", text)
    return text if len(text) <= length else text[:length - 1] + "…"


def byte_text(value: float) -> str:
    for unit in ("B", "KiB", "MiB", "GiB", "TiB"):
        if value < 1024 or unit == "TiB":
            return f"{value:.1f} {unit}"
        value /= 1024
    raise AssertionError("unreachable")


class ReceiveRates:
    """Rate of newly committed chunks, interpolated over the last ten seconds."""

    def __init__(self, window: float = 10):
        self.window = window
        self.samples: dict[str, deque] = {}

    def sample(self, upload_id: str, received: int, now: float) -> float | None:
        points = self.samples.setdefault(upload_id, deque())
        if points and (received < points[-1][1] or now <= points[-1][0]):
            points.clear()
        points.append((now, received))
        cutoff = now - self.window
        while len(points) > 2 and points[1][0] <= cutoff:
            points.popleft()
        if len(points) < 2:
            return None
        start, count = points[0]
        if start < cutoff:
            next_time, next_count = points[1]
            count += (next_count - count) * (cutoff - start) / (next_time - start)
            start = cutoff
        return max(0.0, (received - count) / (now - start))

    def retain(self, ids: set[str]) -> None:
        self.samples = {key: value for key, value in self.samples.items() if key in ids}


def read_uploads(data_root: Path, now: float) -> tuple[list[dict], int]:
    return _read_uploads(data_root, now)


def read_rows(path: Path, query: str, parameters: tuple) -> tuple[list[dict], str | None]:
    if not path.is_file():
        return [], "任务状态库不存在，无法判断队列"
    try:
        with closing(sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True, timeout=0.25)) as connection:
            connection.row_factory = sqlite3.Row
            return [dict(row) for row in connection.execute(query, parameters)], None
    except sqlite3.OperationalError as error:
        if "locked" in str(error).lower():
            return [], "任务状态库忙，下一轮自动重试"
        return [], "任务状态库不可读或结构不匹配，无法判断队列"
    except (OSError, sqlite3.Error):
        return [], "任务状态库暂不可读，下一轮自动重试"


def read_jobs(data_root: Path, now: float) -> tuple[list[dict], str | None]:
    return read_rows(data_root / "jobs.sqlite3", """
        SELECT id, original_filename, status, progress_json, created_at, updated_at FROM jobs
        WHERE status IN ('queued', 'running')
        ORDER BY CASE WHEN status = 'running' THEN 0 ELSE 1 END,
                 created_at ASC LIMIT ?""", (LIMIT,))


def read_counts(data_root: Path, now: float) -> tuple[dict, str | None]:
    cutoff = datetime.fromtimestamp(now - RECENT_SECONDS, timezone.utc).isoformat()
    rows, error = read_rows(data_root / "jobs.sqlite3", """
        SELECT status, count(*) AS count FROM jobs
        WHERE status IN ('queued', 'running') OR updated_at >= ? GROUP BY status""", (cutoff,))
    return {row["status"]: row["count"] for row in rows}, error


def read_history(data_root: Path, now: float) -> tuple[list[dict], str | None]:
    cutoff = datetime.fromtimestamp(now - RECENT_SECONDS, timezone.utc).isoformat()
    return read_rows(data_root / "jobs.sqlite3", """
        SELECT id, original_filename, status, progress_json, updated_at, error FROM jobs
        WHERE status IN ('succeeded', 'failed', 'cancelled') AND updated_at >= ?
        ORDER BY CASE WHEN status = 'failed' THEN 0 ELSE 1 END, updated_at DESC LIMIT 4""", (cutoff,))


def job_progress(job: dict) -> str:
    if job["status"] in {"succeeded", "failed", "cancelled"}:
        return LABELS[job["status"]]
    try:
        progress = json.loads(job.get("progress_json") or "{}")
        phase = safe_text(progress.get("phase", job["status"]), 35)
        percent = float(progress.get("percent", 100 if job["status"] == "succeeded" else 0))
        if not math.isfinite(percent):
            raise ValueError("invalid progress")
        label = LABELS.get(phase, phase)
        return f"{label} {min(100, max(0, percent)):.1f}%"
    except (ValueError, TypeError, AttributeError):
        return "进度暂不可读"


def timestamp(value: str) -> float | None:
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00").replace(",", "."))
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone(timedelta(hours=8)))
        return parsed.timestamp()
    except (ValueError, TypeError, AttributeError):
        return None


def age_text(value: str, now: float) -> str:
    then = timestamp(value)
    if then is None:
        return "更新时间未知"
    seconds = max(0, int(now - then))
    if seconds < 60:
        return f"{seconds}秒前更新"
    if seconds < 3600:
        return f"{seconds // 60}分钟前更新"
    return f"{seconds // 3600}小时前更新"


def duration_text(seconds: float | None) -> str:
    if seconds is None or not math.isfinite(seconds):
        return "未知"
    seconds = max(0, int(seconds))
    if seconds < 60:
        return f"{seconds}秒"
    if seconds < 3600:
        return f"{seconds // 60}分{seconds % 60:02d}秒"
    return f"{seconds // 3600}小时{seconds % 3600 // 60:02d}分"


def finite_number(value, *, positive=False):
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return value if math.isfinite(value) and (value > 0 if positive else value >= 0) else None


class JobSamples:
    """Observed phase durations are lower bounds, never invented start times."""

    def __init__(self):
        self.phases = {}
        self.frames = ReceiveRates()

    def sample(self, identifier, phase, processed, monotonic):
        previous = self.phases.get(identifier)
        if previous is None or previous[0] != phase or monotonic < previous[1]:
            self.phases[identifier] = (phase, monotonic)
            self.frames.samples.pop(identifier, None)
        rate = self.frames.sample(identifier, processed, monotonic) if processed is not None else None
        return {"observed_phase_seconds": max(0, monotonic - self.phases[identifier][1]),
                "observed_fps": rate if phase == "inference" else None}

    def retain(self, identifiers):
        self.phases = {key: value for key, value in self.phases.items() if key in identifiers}
        self.frames.retain(identifiers)


def collect_snapshot(data_root, rates, now, monotonic, *, host=None, job_samples=None):
    uploads, bad = read_uploads(data_root, now)
    rates.retain({item["id"] for item in uploads})
    for item in uploads:
        item["rate_bytes_per_second"] = rates.sample(item["id"], item["received"], monotonic)
        speed = item["rate_bytes_per_second"]
        item["eta_seconds"] = ((item["total"] - item["received"]) / speed
                               if speed and now - item["updated"] <= 120 else None)
    # In-progress sessions must remain visible ahead of completed, unsubmitted
    # sessions and abandoned resumable uploads.
    uploads.sort(key=lambda item: (
        0 if item["received"] < item["total"] and now - item["updated"] <= 120 else
        1 if item["received"] == item["total"] else 2, -item["updated"]))
    counts, count_error = read_counts(data_root, now)
    jobs, job_error = read_jobs(data_root, now)
    history, history_error = read_history(data_root, now)
    for job in jobs:
        try:
            progress = json.loads(job.pop("progress_json") or "{}")
            if not isinstance(progress, dict):
                raise ValueError("invalid progress")
            phase = safe_text(progress.get("phase", job["status"]), 35)
            percent = finite_number(progress.get("percent", 0))
            if percent is None:
                raise ValueError("invalid percent")
            job["progress_json"] = json.dumps({"phase": phase, "percent": percent})
            job["phase"] = phase
            job["message"] = safe_text(progress.get("message", ""), 100)
            for key in ("processed_frames", "total_frames", "effective_fps", "completed_items", "total_items"):
                job[key] = finite_number(progress.get(key))
            fps = finite_number(job.get("effective_fps"), positive=True)
            then = timestamp(job["updated_at"])
            job["eta_seconds"] = ((job["total_frames"] - job["processed_frames"]) / fps
                if phase == "inference" and fps and then is not None and now - then < 60
                and job["total_frames"] is not None and job["processed_frames"] is not None
                and job["total_frames"] >= job["processed_frames"] else None)
            if job_samples:
                job.update(job_samples.sample(job["id"], phase, job["processed_frames"], monotonic))
        except (ValueError, TypeError, AttributeError, KeyError):
            job["progress_json"] = "invalid"
        created = timestamp(job.get("created_at"))
        job["elapsed_seconds"] = max(0, now - created) if created is not None else None
        job["original_filename"] = safe_text(job["original_filename"], 65)
    if job_samples:
        job_samples.retain({job["id"] for job in jobs})
    for item in history:
        item.pop("progress_json", None)
        item["original_filename"] = safe_text(item["original_filename"], 65)
        item["error"] = safe_text(item.get("error"), 180)
    return {"version": "rallymate-monitor-v2", "sampled_at": datetime.fromtimestamp(now, timezone.utc).isoformat(),
            "now": now, "uploads": uploads, "unreadable_uploads": bad, "counts": counts,
            "jobs": jobs, "history": history, "host": host,
            "count_error": count_error, "job_error": job_error, "history_error": history_error}


def record_projection(snapshot):
    """Performance data only: omit filenames, errors, messages and local paths."""
    def identity(value):
        return hashlib.sha256(str(value).encode("utf-8")).hexdigest()[:16]

    def numbers(item, keys):
        return {key: finite_number(item.get(key)) for key in keys}

    jobs = []
    for job in snapshot["jobs"]:
        jobs.append({"id": identity(job["id"]),
            "status": job["status"] if job["status"] in LABELS else "other",
            "phase": job.get("phase") if job.get("phase") in LABELS else "other",
            **numbers(job, ("processed_frames", "total_frames", "effective_fps", "observed_fps",
                "completed_items", "total_items", "elapsed_seconds", "eta_seconds", "observed_phase_seconds"))})
    host = snapshot.get("host") or {}
    network = host.get("network", {})
    resources = {"cpu": numbers(host.get("cpu", {}), ("percent",)),
        "memory": numbers(host.get("memory", {}), ("used_bytes", "total_bytes", "percent")),
        "network": {"scope": "per_interface", "interfaces": [{"id": identity(item.get("name")),
            **numbers(item, ("rx_bytes_per_second", "tx_bytes_per_second", "interval_seconds"))}
            for item in network.get("interfaces", [])]},
        "gpu": {"sample_age_seconds": finite_number(host.get("gpu", {}).get("sample_age_seconds")),
            "devices": [numbers(item, ("index", "utilization_percent", "memory_used_bytes", "memory_total_bytes"))
                        for item in host.get("gpu", {}).get("devices", [])]}}
    return {"version": "rallymate-monitor-v2", "sampled_at": snapshot["sampled_at"],
        "counts": {key: snapshot["counts"].get(key, 0) if not snapshot["count_error"] else None
                   for key in ("running", "queued", "succeeded", "failed", "cancelled")},
        "unreadable_uploads": snapshot["unreadable_uploads"],
        "uploads": [{"id": identity(item["id"]), **numbers(item, ("total", "received", "updated",
            "chunk_count", "received_count", "rate_bytes_per_second", "eta_seconds"))} for item in snapshot["uploads"]],
        "jobs": jobs, "host": resources}


class SnapshotRecorder:
    """Opt-in bounded JSONL, outside service data, with performance-only fields."""

    def __init__(self, path: Path, max_bytes=32 * 1024 * 1024, data_root: Path | None = None):
        self.path, self.max_bytes = path.absolute(), max_bytes
        self.data_root = data_root.resolve() if data_root is not None else None
        self._validate_path()

    def _validate_path(self):
        if self.path.suffix.lower() != ".jsonl":
            raise ValueError("采样记录必须使用 .jsonl 文件")
        if self.data_root is not None and self.path.resolve().is_relative_to(self.data_root):
            raise ValueError("采样记录不能写入服务数据目录；请使用 .codex_tmp/monitor")
        for part in (self.path, *self.path.parents):
            try:
                info = part.lstat()
            except FileNotFoundError:
                continue
            if stat.S_ISLNK(info.st_mode) or getattr(info, "st_file_attributes", 0) & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0):
                raise ValueError("采样记录路径不能经过符号链接或重解析点")
        if self.path.exists():
            if not self.path.is_file():
                raise ValueError("采样记录目标不是普通文件")
            if self.path.stat().st_size:
                with self.path.open("rb") as handle:
                    first = handle.readline(2 * 1024 * 1024)
                try:
                    if not first.endswith(b"\n") or json.loads(first).get("version") != "rallymate-monitor-v2":
                        raise ValueError("not a monitor log")
                except (ValueError, AttributeError, UnicodeError):
                    raise ValueError("已有文件不是监控采样记录；请使用新的 .jsonl 文件") from None

    def write(self, snapshot):
        try:
            self._validate_path()
            raw = (json.dumps(record_projection(snapshot), ensure_ascii=False, allow_nan=False) + "\n").encode("utf-8")
            size = self.path.stat().st_size if self.path.exists() else 0
            if size + len(raw) > self.max_bytes:
                return "采样记录已达到大小上限，停止追加；请更换 -RecordPath"
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with self.path.open("ab") as handle:
                handle.write(raw)
        except (OSError, ValueError, TypeError):
            return "采样记录写入失败；屏幕监控继续"
        return None


def rate_text(value):
    value = finite_number(value)
    return "采样中/不可用" if value is None else byte_text(value) + "/s"


def host_lines(host):
    if not host:
        return ["  网卡收/发：未采样", "  CPU / 内存：未采样", "  GPU：未采样"]
    network, cpu, memory, gpu = (host.get(key, {}) for key in ("network", "cpu", "memory", "gpu"))
    interfaces = sorted(network.get("interfaces", []), key=lambda item:
        (bool(item.get("is_up")), (finite_number(item.get("rx_bytes_per_second")) or 0)
          + (finite_number(item.get("tx_bytes_per_second")) or 0)), reverse=True)
    networks = [f"  收 {rate_text(item.get('rx_bytes_per_second'))}  发 {rate_text(item.get('tx_bytes_per_second'))} | {safe_text(item.get('name'), 26)}"
                for item in interfaces if item.get("is_up") is not False or network.get("selected_interface")]
    if not networks:
        networks = ["  网卡收/发：暂不可用（网卡未找到或缺少psutil）"]
    percent = finite_number(cpu.get("percent"))
    cpu_text = "采样中/不可用" if percent is None else f"{percent:.1f}%"
    used, total = finite_number(memory.get("used_bytes")), finite_number(memory.get("total_bytes"))
    mem_text = f"{byte_text(used)}/{byte_text(total)}" if used is not None and total else "不可用"
    resources = f"  CPU {cpu_text}  内存 {mem_text}"
    devices = gpu.get("devices", [])
    gpu_text = "  GPU：暂不可用"
    if devices:
        device = devices[0]
        util = finite_number(device.get("utilization_percent"))
        used, total = finite_number(device.get("memory_used_bytes")), finite_number(device.get("memory_total_bytes"))
        util_text = f"{util:.0f}%" if util is not None else "未知"
        memory_text = f"{byte_text(used)}/{byte_text(total)}" if used is not None and total else "未知"
        gpu_text = f"  GPU {device.get('index', 0)} {util_text}  显存 {memory_text}"
        age = finite_number(gpu.get("sample_age_seconds"))
        if age is not None:
            gpu_text += f" ({age:.0f}秒前采样)"
        if len(devices) > 1:
            gpu_text += f" 另{len(devices)-1}卡见采样记录"
    return [networks[0], resources, gpu_text, *networks[1:3],
            *([f"  另有{len(networks)-3}个网卡；-Interface 可固定网卡"] if len(networks) > 3 else [])]


def dashboard(data_root: Path, rates: ReceiveRates, now: float | None = None,
              monotonic: float | None = None, *, height: int = 32,
              log_errors: list[str] | None = None, log_issues: list[str] | None = None,
              error_limit: int = 2, snapshot=None) -> str:
    now = time.time() if now is None else now
    monotonic = time.monotonic() if monotonic is None else monotonic
    snapshot = snapshot or collect_snapshot(data_root, rates, now, monotonic)
    uploads, jobs, history, counts = (snapshot[key] for key in ("uploads", "jobs", "history", "counts"))
    count_error, job_error, history_error = (snapshot[key] for key in ("count_error", "job_error", "history_error"))
    clock = datetime.fromtimestamp(now, timezone(timedelta(hours=8))).strftime("%m-%d %H:%M:%S")
    compact = height < 30
    lines = [f"RallyMate 实时监控  |  {clock} 北京时间",
             (f"上传 {len(uploads)}  |  分析 {counts.get('running', 0)}  |  排队 {counts.get('queued', 0)}"
              if not count_error else f"上传 {len(uploads)}  |  分析 / 排队：未知"),
             (f"近24小时：完成 {counts.get('succeeded', 0)}  失败 {counts.get('failed', 0)}  已取消 {counts.get('cancelled', 0)}（历史记录）"
              if not count_error else "历史任务：暂不可读")]
    if height < 19:
        resources = host_lines(snapshot.get("host"))
        short = lines[:2] + ["网卡收/发（含其他软件）", resources[0], "上传落盘（近10秒）"]
        if uploads:
            item = uploads[0]
            short.append(f"  {rate_text(item['rate_bytes_per_second'])} · {100*item['received']/item['total']:.1f}% · {byte_text(item['received'])}/{byte_text(item['total'])}")
        else:
            short.append("  当前没有待提交的上传")
        if jobs:
            current = jobs[0]
            fps = current.get("effective_fps")
            short.append("分析 " + job_progress(current) + (f" · {fps:.2f}帧/s" if fps is not None and current.get("phase") == "inference" else ""))
        else:
            short.append("分析：状态未知" if job_error or count_error else "分析：无进行中任务")
        short.extend(resources[1:3])
        attention = [count_error, job_error, *(log_issues or []), *(log_errors or [])]
        if snapshot["unreadable_uploads"]:
            attention.insert(0, f"{snapshot['unreadable_uploads']}个上传记录暂不可读")
        short.extend("提示：" + safe_text(item, 100) for item in attention if item)
        return "\n".join(short[:max(1, height - 2)] + ["Ctrl+C退出；放大窗口查看明细"])
    lines += ["[本机网络 / 资源 · 网卡流量包含其他软件]"]
    lines += [safe_text(line, 220) for line in host_lines(snapshot.get("host"))[:3 if compact else 5]]
    # Upload and network panels come before verbose task details at every size.
    lines += ["[上传中 / 待提交 · 已落盘分块，不是对方网卡带宽]"]
    shown_uploads = uploads[:1 if compact else 2]
    for item in shown_uploads:
        status = ("待合并/提交" if item["received"] == item["total"]
                  else "等待续传" if now - item["updated"] > 120 else "接收中")
        rate = item["rate_bytes_per_second"]
        speed = "已传完" if item["received"] == item["total"] else "采样中" if rate is None else byte_text(rate) + "/s"
        lines.append(f"  [{safe_text(item['id'], 9)}] {status} {100 * item['received'] / item['total']:.1f}%  {safe_text(item['name'], 28)}")
        lines.append(f"    落盘 {speed}  {byte_text(item['received'])}/{byte_text(item['total'])}  剩余≈{duration_text(item['eta_seconds'])}")
    if not uploads:
        lines.append("  当前没有待提交的上传")
    if len(uploads) > len(shown_uploads):
        lines.append(f"  另有 {len(uploads) - len(shown_uploads)} 个上传未展开")
    notices = list(dict.fromkeys(filter(None, [count_error, job_error, history_error, *(log_issues or [])])))
    if snapshot["unreadable_uploads"]:
        notices.append(f"{snapshot['unreadable_uploads']} 个上传记录暂不可读，下一轮重试")
    notices.extend(log_errors or [])
    shown_notices = max(1, min(error_limit, 2 if compact else 4))
    lines += ["[需要关注 · 监视期间最近5分钟日志]"]
    lines += ["  " + safe_text(line, 150) for line in notices[:shown_notices]] or ["  未发现新增异常；历史失败见下方。"]
    if len(notices) > shown_notices:
        lines.append(f"  另有 {len(notices) - shown_notices} 条提示，使用 -LogsOnly 查看详情")
    lines += ["[当前任务]"]
    shown_jobs = jobs[:1 if compact else 2]
    for job in shown_jobs:
        lines.append(f"  [{safe_text(job['id'], 9)}] {job_progress(job)}  {safe_text(job['original_filename'], 28)}")
        processed, total, fps = (job.get(key) for key in ("processed_frames", "total_frames", "effective_fps"))
        frame_text = f"帧 {processed:g}/{total:g}" if processed is not None and total is not None else ""
        frame_text += f"  均速 {fps:.2f}帧/s" if fps is not None and job.get("phase") == "inference" else ""
        lines.append(f"    {frame_text}  {age_text(job['updated_at'], now)}")
        timing = f"任务用时(含排队) {duration_text(job.get('elapsed_seconds'))}"
        if job.get("eta_seconds") is not None:
            timing += f"  推理剩余≈{duration_text(job['eta_seconds'])}"
        elif job.get("completed_items") is not None and job.get("total_items") is not None:
            timing += f"  本阶段 {job['completed_items']:g}/{job['total_items']:g}"
        lines.append("    " + timing)
        if not compact:
            observed = job.get("observed_phase_seconds")
            detail = f"观察本阶段≥{duration_text(observed)}  " if observed is not None else ""
            lines.append("    " + detail + safe_text(job.get("message", ""), 65))
    active_count = counts.get("running", 0) + counts.get("queued", 0)
    if not jobs:
        lines.append("  状态未知，不能判断是否空闲" if job_error or count_error else
                     "  任务状态正在变化，下一轮刷新" if active_count else "  空闲，等待新任务")
    if active_count > len(shown_jobs):
        lines.append(f"  另有 {active_count - len(shown_jobs)} 个任务未展开（分析任务优先）")
    history_lines = ["[近24小时记录 · 失败优先，不代表当前故障]"]
    for job in history[:1 if compact else 2]:
        history_lines.append(f"  [{safe_text(job['id'], 9)}] {job_progress(job)}  {safe_text(job['original_filename'], 28)}")
        if job["status"] == "failed" and job.get("error"):
            history_lines.append("    原因：" + safe_text(job["error"], 110))
    if not history:
        history_lines.append("  近24小时无结束任务" if not history_error else "  历史暂不可读")
    max_lines = max(5, height - 1)
    available = max_lines - len(lines) - 1
    if available >= 2:
        lines.extend(history_lines[:available])
    footer = "Ctrl+C退出(服务继续) | -Interface 网卡名 | -RecordPath 采样.jsonl"
    if len(lines) > max_lines - 1:
        lines = lines[:max_lines - 2] + ["… 部分明细隐藏；放大窗口或记录JSONL"]
    return "\n".join([*lines, footer])


class RecentLogErrors:
    """Bounded incremental alerts; never replay undated historical error lines."""

    def __init__(self, log_root: Path):
        self.log_root = log_root
        self.positions = {}
        self.events = deque(maxlen=64)

    def poll(self, now: float | None = None) -> tuple[list[str], list[str]]:
        now = time.time() if now is None else now
        issues = []
        for name in LOG_NAMES:
            path = self.log_root / name
            try:
                stat = path.stat()
                previous = self.positions.get(name)
                reset = previous is None or previous[0] != stat.st_ino or previous[1] > stat.st_size
                if reset and previous is not None:
                    self.events = deque((event for event in self.events if event["file"] != name), maxlen=64)
                start = max(0, stat.st_size - 131072) if reset else max(previous[1], stat.st_size - 131072)
                with path.open("rb") as handle:
                    handle.seek(start)
                    raw = handle.read(131072)
                    end = handle.tell()
                # Leave an incomplete write for the next refresh.
                if raw and not raw.endswith(b"\n"):
                    last = raw.rfind(b"\n") + 1
                    end -= len(raw) - last
                    raw = raw[:last]
                self.positions[name] = (stat.st_ino, end)
                if start > 0 and (reset or previous is not None and start > previous[1]):
                    raw = raw.partition(b"\n")[2]
                line_time = None
                for line in raw.decode("utf-8", errors="replace").splitlines():
                    match = re.match(r"^(\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}:\d{2}(?:[.,]\d+)?(?:Z|[+-]\d{2}:\d{2})?)", line)
                    if match:
                        line_time = timestamp(match[1])
                    if not re.search(r"\b(?:ERROR|ERR|CRITICAL|FATAL|WARNING|WARN)\b|Traceback \(most recent call last\)|\b\w+(?:Error|Exception):|HTTP/[\d.]+\" [45]\d\d\b", line):
                        continue
                    event_time = line_time if line_time is not None else (None if previous is None else now)
                    if event_time is None or not now - 300 <= event_time <= now + 60:
                        continue
                    message = safe_text(line[match.end():].strip() if match else line, 180)
                    duplicate = next((event for event in reversed(self.events)
                                      if event["file"] == name and event["message"] == message
                                      and event_time - event["time"] <= 60), None)
                    if duplicate is not None:
                        duplicate["count"] += 1
                        duplicate["time"] = event_time
                    else:
                        self.events.append({"file": name, "message": message, "time": event_time, "count": 1})
            except OSError:
                issues.append(f"{name} 暂不可读，异常监视不完整")
        self.events = deque((event for event in self.events if event["time"] >= now - 300), maxlen=64)
        labels = {"api.err.log": "后端", "web.err.log": "网页", "api.log": "请求"}
        rows = []
        for event in sorted(self.events, key=lambda item: item["time"], reverse=True):
            clock = datetime.fromtimestamp(event["time"], timezone(timedelta(hours=8))).strftime("%H:%M:%S")
            count = f" ×{event['count']}" if event["count"] > 1 else ""
            rows.append(f"{clock} {labels[event['file']]}{count} | {event['message']}")
        return rows, issues


def tail_logs(log_root: Path, lines: int) -> str:
    result = []
    for name in LOG_NAMES:
        try:
            path = log_root / name
            updated = datetime.fromtimestamp(path.stat().st_mtime).strftime("%m-%d %H:%M:%S")
            result.append(f"[{name} · 最后写入 {updated}]")
            with path.open("rb") as stream:
                stream.seek(0, 2)
                length = stream.tell()
                stream.seek(max(0, length - 32768))
                content = stream.read().decode("utf-8", errors="replace").splitlines()
            result.extend(safe_text(line, 180) for line in content[-lines:] if line.strip())
            if not content:
                result.append("  暂无日志")
        except OSError:
            result.append(f"[{name}] 暂不可读")
    result.append("隧道日志未接入：请查看当前 cloudflared 终端；历史隧道文件不在此展示。")
    return "\n".join(result)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-root", type=Path, default=ROOT / "service_data")
    parser.add_argument("--log-root", type=Path, default=ROOT / ".codex_tmp" / "full-service")
    parser.add_argument("--interval", type=float, default=2)
    parser.add_argument("--lines", type=int, default=6)
    parser.add_argument("--once", action="store_true")
    parser.add_argument("--interface", help="exact network interface name; default displays per-interface counters")
    parser.add_argument("--record", type=Path, help="append sanitized snapshots to JSONL (32 MiB maximum)")
    parser.add_argument("--logs", action="store_true", help="show more recent exception summaries")
    parser.add_argument("--logs-only", action="store_true", help="show bounded raw logs in the fixed screen")
    args = parser.parse_args()
    if not math.isfinite(args.interval) or args.interval < 1:
        parser.error("--interval must be at least 1 second")
    if args.logs_only and args.record:
        parser.error("--record records dashboard metrics; use --logs instead of --logs-only")
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    rates = ReceiveRates()
    log_errors = RecentLogErrors(args.log_root)
    metrics = HostMetrics(interface=args.interface)
    job_samples = JobSamples()
    try:
        recorder = SnapshotRecorder(args.record, data_root=args.data_root) if args.record else None
    except (ValueError, OSError) as error:
        parser.error(str(error))
    try:
        with TerminalRenderer(once=args.once) as terminal:
            if not args.logs_only and (args.once or not terminal.interactive):
                # A one-shot view still needs two counter samples for a rate.
                now, monotonic = time.time(), time.monotonic()
                collect_snapshot(args.data_root, rates, now, monotonic,
                    host=metrics.sample(monotonic), job_samples=job_samples)
                time.sleep(1)
            while True:
                _, height = terminal.size()
                errors, issues = log_errors.poll()
                if args.logs_only:
                    per_file = max(1, min(args.lines, (height - 7) // 3))
                    content = "RallyMate 原始日志（固定刷新）\n" + tail_logs(args.log_root, per_file)
                    content += "\nCtrl+C 退出监视，服务继续运行。"
                else:
                    now, monotonic = time.time(), time.monotonic()
                    snapshot = collect_snapshot(args.data_root, rates, now, monotonic,
                        host=metrics.sample(monotonic), job_samples=job_samples)
                    if recorder:
                        issue = recorder.write(snapshot)
                        if issue:
                            issues.append(issue)
                    content = dashboard(args.data_root, rates, now=now, monotonic=monotonic,
                        height=height, log_errors=errors, log_issues=issues,
                        error_limit=args.lines if args.logs else 2, snapshot=snapshot)
                terminal.draw(content)
                if args.once or not terminal.interactive:
                    break
                time.sleep(args.interval)
    except KeyboardInterrupt:
        print("\n已关闭监视器，服务继续运行。")


if __name__ == "__main__":
    main()
