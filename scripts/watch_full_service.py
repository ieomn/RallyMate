"""Read-only local upload/analysis monitor; no API credentials or service control."""
from __future__ import annotations

import argparse
from collections import deque
from contextlib import closing
from datetime import datetime, timedelta, timezone
import json
import math
import os
from pathlib import Path
import re
import sqlite3
import sys
import time
import unicodedata

ROOT = Path(__file__).resolve().parents[1]
RECENT_SECONDS = 24 * 60 * 60
LIMIT = 8
LABELS = {
    "queued": "排队中", "running": "分析中", "succeeded": "分析完成", "failed": "失败",
    "rendering": "白模渲染", "submitting": "提交 MiniMax", "provider_processing": "MiniMax 生成中",
    "downloading": "下载生成视频", "completed": "生成完成", "submit_unknown": "提交结果待确认",
}


def safe_text(value: object, length: int = 90) -> str:
    text = "".join(" " if unicodedata.category(char).startswith("C") else char for char in str(value or ""))
    text = re.sub(r"\bsk-[A-Za-z0-9_-]{12,}", "[已隐藏密钥]", text)
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
    uploads, bad = [], 0
    for manifest in (data_root / "uploads" / ".chunks").glob("*/manifest.json"):
        try:
            item = json.loads(manifest.read_text(encoding="utf-8"))
            total, updated = item["size"], float(item["updated_at"])
            if isinstance(total, bool) or not isinstance(total, int) or total <= 0 or not math.isfinite(updated):
                raise ValueError("invalid manifest")
            sizes = [chunk["size"] for chunk in item["chunks"].values()]
            if any(isinstance(size, bool) or not isinstance(size, int) or size < 0 for size in sizes):
                raise ValueError("invalid chunk size")
            received = sum(sizes)
            if received > total:
                raise ValueError("invalid byte count")
            if now - updated > RECENT_SECONDS:
                continue
            uploads.append({"id": str(item["upload_id"]), "name": safe_text(item["filename"], 65),
                            "total": total, "received": received, "updated": updated,
                            "job_id": item.get("job_id")})
        except (OSError, ValueError, TypeError, KeyError, AttributeError):
            bad += 1
    uploads.sort(key=lambda item: item["updated"], reverse=True)
    return uploads[:LIMIT], bad


def read_rows(path: Path, query: str, parameters: tuple) -> tuple[list[dict], str | None]:
    try:
        with closing(sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True, timeout=0.25)) as connection:
            connection.row_factory = sqlite3.Row
            return [dict(row) for row in connection.execute(query, parameters)], None
    except (OSError, sqlite3.Error):
        return [], "状态库暂不可读，下一轮自动重试"


def read_jobs(data_root: Path, now: float) -> tuple[list[dict], str | None]:
    cutoff = datetime.fromtimestamp(now - RECENT_SECONDS, timezone.utc).isoformat()
    return read_rows(data_root / "jobs.sqlite3", """
        SELECT id, original_filename, status, progress_json, updated_at FROM jobs
        WHERE status IN ('queued', 'running') OR updated_at >= ?
        ORDER BY CASE WHEN status IN ('queued', 'running') THEN 0 ELSE 1 END,
                 updated_at DESC LIMIT ?""", (cutoff, LIMIT))


def read_replays(data_root: Path, now: float) -> tuple[list[dict], str | None]:
    return read_rows(data_root / "motion-replays.sqlite3", """
        SELECT id, job_id, generation_status, updated_at FROM motion_replays
        WHERE generation_status IN ('queued', 'rendering', 'submitting', 'provider_processing', 'downloading')
              OR updated_at >= ?
        ORDER BY CASE WHEN generation_status IN ('queued', 'rendering', 'submitting', 'provider_processing', 'downloading')
                      THEN 0 ELSE 1 END, updated_at DESC LIMIT ?""", (now - RECENT_SECONDS, LIMIT))


def job_progress(job: dict) -> str:
    try:
        progress = json.loads(job.get("progress_json") or "{}")
        phase = safe_text(progress.get("phase", job["status"]), 35)
        percent = float(progress.get("percent", 100 if job["status"] == "succeeded" else 0))
        if not math.isfinite(percent):
            raise ValueError("invalid progress")
        label = "分析完成" if phase == "completed" else LABELS.get(phase, phase)
        return f"{label} {min(100, max(0, percent)):.1f}%"
    except (ValueError, TypeError, AttributeError):
        return "进度暂不可读"


def dashboard(data_root: Path, rates: ReceiveRates, now: float | None = None,
              monotonic: float | None = None) -> str:
    now = time.time() if now is None else now
    monotonic = time.monotonic() if monotonic is None else monotonic
    lines = ["RallyMate 上传 / 分析 / MiniMax 实时状态",
             datetime.fromtimestamp(now, timezone(timedelta(hours=8))).strftime("%Y-%m-%d %H:%M:%S 北京时间"),
             f"原片保存目录：{data_root / 'uploads'}",
             "Ctrl+C 仅关闭监视器，不会停止服务。每区最多显示 8 项，完成记录保留显示 24 小时。",
             "速度 = 服务端确认分块字节增量的近 10 秒接收估算；不是朋友的真实网络带宽。",
             "未完成的分块、重传流量不计入；首次采样需等待下一轮。", "", "[上传]"]
    uploads, bad = read_uploads(data_root, now)
    rates.retain({item["id"] for item in uploads})
    for item in uploads:
        rate = rates.sample(item["id"], item["received"], monotonic)
        status = ("已提交分析" if item["job_id"] else "待合并/提交" if item["received"] == item["total"]
                  else "等待续传" if now - item["updated"] > 120 else "接收中")
        speed = "已传完" if item["received"] == item["total"] else "采样中" if rate is None else byte_text(rate) + "/s"
        lines.extend([f"  {item['name']}  [{safe_text(item['id'], 8)}] {status}",
                      f"    {byte_text(item['received'])} / {byte_text(item['total'])}"
                      f"  {100 * item['received'] / item['total']:.1f}%  接收估算 {speed}"])
    if not uploads:
        lines.append("  暂无最近 24 小时的分片上传。等待朋友选择视频。")
    if bad:
        lines.append(f"  {bad} 个上传记录暂不可读，已跳过；下一轮重试。")
    jobs, error = read_jobs(data_root, now)
    lines.extend(["", "[分析队列 / GPU]"])
    for job in jobs:
        lines.append(f"  {safe_text(job['original_filename'], 60)} [{safe_text(job['id'], 8)}] "
                     f"{LABELS.get(job['status'], safe_text(job['status']))} · {job_progress(job)}")
    if not jobs:
        lines.append("  " + (error or "暂无正在排队、分析或近期完成的任务。"))
    replays, error = read_replays(data_root, now)
    lines.extend(["", "[白模 / MiniMax]"])
    for replay in replays:
        status = replay["generation_status"]
        lines.append(f"  任务 {safe_text(replay['job_id'], 8)} / 回放 {safe_text(replay['id'], 8)}"
                     f"  {LABELS.get(status, safe_text(status))}")
    if not replays:
        lines.append("  " + (error or "暂无生成中或近期完成的回放。"))
    lines.extend(["", "查看原始日志：watch_full_service.ps1 -LogsOnly"])
    return "\n".join(lines)


def tail_logs(log_root: Path, lines: int) -> str:
    result = []
    tunnel_log = "cloudflared.err.log"
    try:
        candidates = [path for path in log_root.glob("cloudflared*.log")
                      if path.name.endswith((".stderr.log", ".err.log"))]
        if candidates:
            tunnel_log = max(candidates, key=lambda path: path.stat().st_mtime).name
    except OSError:
        pass
    for name in ("api.log", "api.err.log", "web.err.log", tunnel_log):
        result.append(f"\n[{name}]")
        try:
            with (log_root / name).open("rb") as stream:
                stream.seek(0, 2)
                length = stream.tell()
                stream.seek(max(0, length - 32768))
                content = stream.read().decode("utf-8", errors="replace").splitlines()
            result.extend(safe_text(line, 180) for line in content[-lines:])
        except OSError:
            result.append("日志暂不可读。")
    return "\n".join(result)


def enable_terminal() -> None:
    if os.name == "nt" and sys.stdout.isatty():
        import ctypes
        from ctypes import wintypes
        kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel.GetStdHandle.restype = wintypes.HANDLE
        kernel.GetConsoleMode.argtypes = [wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD)]
        kernel.SetConsoleMode.argtypes = [wintypes.HANDLE, wintypes.DWORD]
        handle = kernel.GetStdHandle(-11)
        mode = wintypes.DWORD()
        if kernel.GetConsoleMode(handle, ctypes.byref(mode)):
            kernel.SetConsoleMode(handle, mode.value | 4)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-root", type=Path, default=ROOT / "service_data")
    parser.add_argument("--log-root", type=Path, default=ROOT / ".codex_tmp" / "full-service")
    parser.add_argument("--interval", type=float, default=2)
    parser.add_argument("--lines", type=int, default=6)
    parser.add_argument("--once", action="store_true")
    parser.add_argument("--logs", action="store_true")
    parser.add_argument("--logs-only", action="store_true")
    args = parser.parse_args()
    if not math.isfinite(args.interval) or args.interval < 1:
        parser.error("--interval must be at least 1 second")
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    enable_terminal()
    rates = ReceiveRates()
    try:
        while True:
            content = "" if args.logs_only else dashboard(args.data_root, rates)
            if args.logs or args.logs_only:
                content += tail_logs(args.log_root, max(1, min(args.lines, 100)))
            if not args.once and sys.stdout.isatty():
                print("\033[2J\033[H", end="")
            print(content, flush=True)
            if args.once:
                break
            time.sleep(args.interval)
    except KeyboardInterrupt:
        print("\n已关闭监视器，服务继续运行。")


if __name__ == "__main__":
    main()
