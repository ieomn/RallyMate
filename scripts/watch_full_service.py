"""Read-only local upload/analysis monitor; no API credentials or service control."""
from __future__ import annotations

import argparse
from collections import deque
from contextlib import closing
from datetime import datetime, timedelta, timezone
import json
import math
from pathlib import Path
import re
import sqlite3
import sys
import time
import unicodedata

try:
    from service_monitor_terminal import TerminalRenderer
except ModuleNotFoundError:
    from scripts.service_monitor_terminal import TerminalRenderer

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
            if now - updated > RECENT_SECONDS or item.get("job_id"):
                continue
            uploads.append({"id": str(item["upload_id"]), "name": safe_text(item["filename"], 65),
                            "total": total, "received": received, "updated": updated,
                            "job_id": item.get("job_id")})
        except (OSError, ValueError, TypeError, KeyError, AttributeError):
            bad += 1
    uploads.sort(key=lambda item: item["updated"], reverse=True)
    return uploads, bad


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
        SELECT id, original_filename, status, progress_json, updated_at FROM jobs
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


def dashboard(data_root: Path, rates: ReceiveRates, now: float | None = None,
              monotonic: float | None = None, *, height: int = 32,
              log_errors: list[str] | None = None, log_issues: list[str] | None = None,
              error_limit: int = 2) -> str:
    now = time.time() if now is None else now
    monotonic = time.monotonic() if monotonic is None else monotonic
    uploads, bad = read_uploads(data_root, now)
    rates.retain({item["id"] for item in uploads})
    counts, count_error = read_counts(data_root, now)
    jobs, job_error = read_jobs(data_root, now)
    history, history_error = read_history(data_root, now)
    clock = datetime.fromtimestamp(now, timezone(timedelta(hours=8))).strftime("%m-%d %H:%M:%S")
    lines = [f"RallyMate 实时监控  |  {clock} 北京时间",
             (f"上传 {len(uploads)}  |  分析 {counts.get('running', 0)}  |  排队 {counts.get('queued', 0)}"
              if not count_error else f"上传 {len(uploads)}  |  分析 / 排队：未知"),
             (f"近24小时：完成 {counts.get('succeeded', 0)}  失败 {counts.get('failed', 0)}  已取消 {counts.get('cancelled', 0)}（历史记录）"
              if not count_error else "历史任务：暂不可读")]
    compact = height < 28
    notices = list(dict.fromkeys(filter(None, [count_error, job_error, history_error, *(log_issues or [])])))
    if bad:
        notices.append(f"{bad} 个上传记录暂不可读，下一轮重试")
    notices.extend(log_errors or [])
    lines += ["", "[需要关注 · 监视期间最近5分钟日志]"]
    lines += ["  " + safe_text(line, 180) for line in notices[:max(1, min(error_limit, 2 if compact else 4))]] or [
        "  未发现新增异常；历史失败见下方。"]
    if len(notices) > max(1, min(error_limit, 2 if compact else 4)):
        lines.append(f"  另有 {len(notices) - max(1, min(error_limit, 2 if compact else 4))} 条提示，使用 -LogsOnly 查看详情")
    lines += ["", "[当前任务]"]
    shown_jobs = jobs[:1 if compact else 3]
    for job in shown_jobs:
        lines.append(f"  [{safe_text(job['id'], 9)}] {job_progress(job)}  {safe_text(job['original_filename'], 32)}")
        try:
            progress = json.loads(job.get("progress_json") or "{}")
            detail = safe_text(progress.get("message", ""), 54)
            processed, total = progress.get("processed_frames"), progress.get("total_frames")
            frame_text = f"帧 {processed}/{total} · " if isinstance(processed, int) and isinstance(total, int) else ""
            lines.append(f"    {frame_text}{age_text(job['updated_at'], now)}  {detail}")
        except (ValueError, TypeError, AttributeError):
            lines.append("    进度详情暂不可读")
    active_count = counts.get("running", 0) + counts.get("queued", 0)
    if not jobs:
        lines.append("  状态未知，不能判断是否空闲" if job_error or count_error else
                     "  任务状态正在变化，下一轮刷新" if active_count else "  空闲，等待新任务")
    if active_count > len(shown_jobs):
        lines.append(f"  另有 {active_count - len(shown_jobs)} 个任务未展开（分析任务优先）")
    lines += ["", "[上传中 / 待提交]"]
    shown_uploads = uploads[:1 if compact else 2]
    for item in uploads:
        rate = rates.sample(item["id"], item["received"], monotonic)
        if item not in shown_uploads:
            continue
        status = ("待合并/提交" if item["received"] == item["total"]
                  else "等待续传" if now - item["updated"] > 120 else "接收中")
        speed = "已传完" if item["received"] == item["total"] else "采样中" if rate is None else byte_text(rate) + "/s"
        lines.extend([f"  [{safe_text(item['id'], 9)}] {status} {100 * item['received'] / item['total']:.1f}%  {safe_text(item['name'], 32)}",
                      f"    {byte_text(item['received'])} / {byte_text(item['total'])}  接收估算 {speed}"])
    if not uploads:
        lines.append("  当前没有待提交的上传")
    if len(uploads) > len(shown_uploads):
        lines.append(f"  另有 {len(uploads) - len(shown_uploads)} 个上传未展开")
    history_lines = ["", "[近24小时记录 · 失败优先，不代表当前故障]"]
    for job in history[:1 if compact else 3]:
        history_lines.append(f"  [{safe_text(job['id'], 9)}] {job_progress(job)}  {safe_text(job['original_filename'], 32)}")
        if job["status"] == "failed" and job.get("error"):
            history_lines.append("    原因：" + safe_text(job["error"], 110))
    if not history:
        history_lines.append("  近24小时无结束任务" if not history_error else "  历史暂不可读")
    max_lines = max(5, height - 1)
    available = max_lines - len(lines) - 2
    if available >= 3:
        lines.extend(history_lines[:available])
    footer = "Ctrl+C 退出监视（服务继续） | -LogsOnly 原始日志 | 上传速度为服务端接收估算"
    if len(lines) > max_lines - 2:
        lines = lines[:max_lines - 3] + ["… 窗口较小，部分明细已隐藏；总数见顶部"]
    return "\n".join([*lines, "", footer])


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
    parser.add_argument("--logs", action="store_true", help="show more recent exception summaries")
    parser.add_argument("--logs-only", action="store_true", help="show bounded raw logs in the fixed screen")
    args = parser.parse_args()
    if not math.isfinite(args.interval) or args.interval < 1:
        parser.error("--interval must be at least 1 second")
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    rates = ReceiveRates()
    log_errors = RecentLogErrors(args.log_root)
    try:
        with TerminalRenderer(once=args.once) as terminal:
            while True:
                _, height = terminal.size()
                errors, issues = log_errors.poll()
                if args.logs_only:
                    per_file = max(1, min(args.lines, (height - 7) // 3))
                    content = "RallyMate 原始日志（固定刷新）\n" + tail_logs(args.log_root, per_file)
                    content += "\nCtrl+C 退出监视，服务继续运行。"
                else:
                    content = dashboard(args.data_root, rates, height=height, log_errors=errors,
                                        log_issues=issues, error_limit=args.lines if args.logs else 2)
                terminal.draw(content)
                if args.once or not terminal.interactive:
                    break
                time.sleep(args.interval)
    except KeyboardInterrupt:
        print("\n已关闭监视器，服务继续运行。")


if __name__ == "__main__":
    main()
