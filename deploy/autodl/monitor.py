#!/usr/bin/env python3
"""Bounded AutoDL health collection and a separate read-only snapshot command.

The snapshot deliberately excludes exception messages, job filenames, local
paths, HTTP bodies, credentials, GPU process listings and command output.
"""
from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
import csv
import hashlib
import json
import logging
from logging.handlers import RotatingFileHandler
import math
import os
from pathlib import Path
import re
import shutil
import signal
import sqlite3
import subprocess
import sys
import threading
import time
from datetime import datetime, timedelta, timezone
from urllib.error import HTTPError, URLError
from urllib.request import HTTPRedirectHandler, ProxyHandler, Request, build_opener

PROJECT_ROOT = Path(__file__).resolve().parents[2]
OPS_DIR = PROJECT_ROOT / "service_data" / "ops"
PROGRAMS = ("api", "worker", "web", "cloudflared", "monitor")
TUNNEL_URL = re.compile(r"https://[a-z0-9-]+\.trycloudflare\.com\b")
MAX_JSON_BYTES = 2 * 1024 * 1024


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def number(value, default=None):
    try:
        result = float(value)
    except (TypeError, ValueError):
        return default
    return result if math.isfinite(result) else default


def age_seconds(value, now: datetime):
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return max(0, round((now - parsed).total_seconds()))
    except (TypeError, ValueError):
        return None


def safe_job_id(value):
    return value if isinstance(value, str) and re.fullmatch(r"[A-Za-z0-9_-]{1,80}", value) else "redacted"


def read_json(path: Path):
    if path.stat().st_size > MAX_JSON_BYTES:
        raise ValueError("oversize_json")
    result = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(result, dict):
        raise ValueError("object_required")
    return result


def atomic_json(path: Path, value: dict):
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    try:
        with temporary.open("w", encoding="utf-8") as handle:
            json.dump(value, handle, ensure_ascii=False, allow_nan=False, separators=(",", ":"))
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(temporary, 0o600)
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def probe_http(url: str, *, api_key: str = "", readiness: bool = False):
    # Ignore proxy environment for both loopback and the direct cloud route.
    # Never attach the API key to a public URL or follow a redirect with it.
    headers = {"User-Agent": "RallyMateHealth/1"}
    if api_key and url == "http://127.0.0.1:8001/health/ready":
        headers["Authorization"] = f"Bearer {api_key}"
    started = time.monotonic()
    try:
        with build_opener(ProxyHandler({}), NoRedirect()).open(Request(url, headers=headers), timeout=8) as response:
            status = response.status
            result = {"ok": status == 200, "http_status": status}
            if readiness:
                payload = json.loads(response.read(128 * 1024))
                ready = payload.get("status") == "ready" if isinstance(payload, dict) else False
                result.update(ok=result["ok"] and ready, readiness="ready" if ready else "not_ready")
    except HTTPError as exc:
        result = {"ok": False, "http_status": exc.code, "error_code": "http_error"}
    except (URLError, TimeoutError, OSError):
        result = {"ok": False, "http_status": None, "error_code": "connection_or_tls_error"}
    except (ValueError, json.JSONDecodeError):
        result = {"ok": False, "http_status": None, "error_code": "invalid_health_response"}
    result["latency_ms"] = round((time.monotonic() - started) * 1000)
    return result


def process_health(root: Path):
    result = {name: {"state": "UNKNOWN", "pid": None} for name in PROGRAMS}
    try:
        command = [str(root / ".venv/bin/supervisorctl"), "-c", str(root / "deploy/autodl/supervisord.conf"), "status"]
        output = subprocess.run(command, capture_output=True, text=True, timeout=10, check=False).stdout
        for line in output.splitlines():
            parts = line.split()
            if len(parts) < 2 or parts[0] not in result:
                continue
            state = parts[1] if parts[1] in {"RUNNING", "STARTING", "BACKOFF", "FATAL", "STOPPED", "EXITED", "STOPPING", "UNKNOWN"} else "UNKNOWN"
            pid = re.search(r"\bpid (\d+)\b", line)
            result[parts[0]] = {"state": state, "pid": int(pid.group(1)) if pid else None}
    except (OSError, subprocess.SubprocessError):
        pass
    return result


def current_tunnel_url(log: Path):
    try:
        with log.open("rb") as handle:
            handle.seek(max(0, log.stat().st_size - 256 * 1024))
            text = handle.read().decode("utf-8", errors="replace")
        current = text.rsplit("RALLYMATE_TUNNEL_STARTED", 1)[-1]
        matches = TUNNEL_URL.findall(current)
        return matches[-1] if matches else None
    except OSError:
        return None


def collect_jobs(database: Path, state: dict, now: datetime, stuck_seconds: int):
    result = {"ok": False, "counts": {}, "running": [], "new_failures": [], "new_completions": [], "oldest_queued_seconds": None}
    try:
        # mode=ro must never create a new queue, update leases or alter jobs.
        with closing(sqlite3.connect(f"{database.resolve().as_uri()}?mode=ro", uri=True, timeout=3)) as connection:
            connection.row_factory = sqlite3.Row
            result["counts"] = {row[0]: row[1] for row in connection.execute("SELECT status, COUNT(*) FROM jobs GROUP BY status") if row[0] in {"queued", "running", "succeeded", "failed"}}
            rows = connection.execute("SELECT id, updated_at, started_at, lease_expires_at, progress_json, attempts FROM jobs WHERE status = 'running' ORDER BY started_at LIMIT 100").fetchall()
            previous_progress = state.get("running_progress")
            previous_progress = previous_progress if isinstance(previous_progress, dict) else {}
            next_progress = {}
            for row in rows:
                try:
                    progress = json.loads(row["progress_json"] or "{}")
                    progress = progress if isinstance(progress, dict) else {}
                except (ValueError, TypeError):
                    progress = {}
                updated_age = age_seconds(row["updated_at"], now)
                try:
                    lease_expired = datetime.fromisoformat(row["lease_expires_at"]) < now if row["lease_expires_at"] else False
                except (ValueError, TypeError):
                    lease_expired = False
                phase = progress.get("phase")
                job_id = safe_job_id(row["id"])
                # Lease refreshes and repeated progress payloads do not prove
                # the GPU or encoder advanced. Persist only the fingerprint
                # of observable progress, never free-form messages or paths.
                fingerprint = hashlib.sha256(json.dumps([
                    phase if isinstance(phase, str) else None,
                    number(progress.get("processed_frames")), number(progress.get("total_frames")),
                    number(progress.get("percent")), row["attempts"],
                ], separators=(",", ":"), ensure_ascii=True).encode()).hexdigest()
                previous = previous_progress.get(job_id)
                previous = previous if isinstance(previous, dict) else {}
                if previous.get("fingerprint") == fingerprint and age_seconds(previous.get("changed_at"), now) is not None:
                    changed_at = previous["changed_at"]
                elif previous:
                    changed_at = now.isoformat()
                else:
                    # First observation can already have an old DB update.
                    changed_at = (now - timedelta(seconds=updated_age or 0)).isoformat()
                unchanged_seconds = age_seconds(changed_at, now)
                next_progress[job_id] = {"fingerprint": fingerprint, "changed_at": changed_at}
                result["running"].append({
                    "job_id": job_id, "running_seconds": age_seconds(row["started_at"], now),
                    "last_progress_seconds": unchanged_seconds, "last_update_seconds": updated_age, "lease_expired": lease_expired,
                    "stuck": lease_expired or (unchanged_seconds is not None and unchanged_seconds > stuck_seconds),
                    "phase": phase if isinstance(phase, str) and phase in {"queued", "loading_models", "inference", "processing", "rendering", "encoding", "finalizing", "scoring", "scoring_readiness", "complete", "completed"} else "other",
                    "percent": number(progress.get("percent")), "processed_frames": number(progress.get("processed_frames")),
                    "total_frames": number(progress.get("total_frames")),
                })
            oldest = connection.execute("SELECT MIN(created_at) FROM jobs WHERE status = 'queued'").fetchone()[0]
            result["oldest_queued_seconds"] = age_seconds(oldest, now) if oldest else None
            cursor = state.get("failure_cursor")
            if not isinstance(cursor, dict):
                # Baseline historical failures; do not re-alert the entire history.
                latest = connection.execute("SELECT updated_at, id FROM jobs WHERE status = 'failed' ORDER BY updated_at DESC, id DESC LIMIT 1").fetchone()
                state["failure_cursor"] = {"updated_at": latest[0], "id": latest[1]} if latest else {"updated_at": "", "id": ""}
            else:
                failures = connection.execute("SELECT id, updated_at FROM jobs WHERE status = 'failed' AND (updated_at > ? OR (updated_at = ? AND id > ?)) ORDER BY updated_at, id LIMIT 100", (str(cursor.get("updated_at", "")), str(cursor.get("updated_at", "")), str(cursor.get("id", "")))).fetchall()
                result["new_failures"] = [{"job_id": safe_job_id(row[0]), "failed_at": row[1]} for row in failures]
                if failures:
                    state["failure_cursor"] = {"updated_at": failures[-1][1], "id": failures[-1][0]}
            completed_cursor = state.get("completion_cursor")
            if not isinstance(completed_cursor, dict):
                latest = connection.execute("SELECT updated_at, id FROM jobs WHERE status = 'succeeded' ORDER BY updated_at DESC, id DESC LIMIT 1").fetchone()
                state["completion_cursor"] = {"updated_at": latest[0], "id": latest[1]} if latest else {"updated_at": "", "id": ""}
            else:
                completed = connection.execute("SELECT id, updated_at FROM jobs WHERE status = 'succeeded' AND (updated_at > ? OR (updated_at = ? AND id > ?)) ORDER BY updated_at, id LIMIT 100", (str(completed_cursor.get("updated_at", "")), str(completed_cursor.get("updated_at", "")), str(completed_cursor.get("id", "")))).fetchall()
                result["new_completions"] = [{"job_id": safe_job_id(row[0]), "completed_at": row[1]} for row in completed]
                if completed:
                    state["completion_cursor"] = {"updated_at": completed[-1][1], "id": completed[-1][0]}
            state["running_progress"] = next_progress
            result["ok"] = True
    except (OSError, sqlite3.Error):
        result["error_code"] = "queue_unavailable"
    return result


def disk_health(data_root: Path):
    try:
        usage = shutil.disk_usage(data_root)
        free_percent = round(100 * usage.free / usage.total, 2)
        return {"ok": True, "total_bytes": usage.total, "free_bytes": usage.free, "free_percent": free_percent, "low_space": usage.free < 2 * 1024 ** 3 or free_percent < 5}
    except OSError:
        return {"ok": False, "error_code": "disk_unavailable"}


def gpu_health():
    try:
        command = ["nvidia-smi", "--query-gpu=index,utilization.gpu,memory.used,memory.total,temperature.gpu", "--format=csv,noheader,nounits"]
        output = subprocess.run(command, capture_output=True, text=True, timeout=8, check=False)
        devices = []
        if output.returncode == 0:
            for row in csv.reader(output.stdout.splitlines()):
                if len(row) == 5 and number(row[0]) is not None:
                    devices.append(dict(zip(("index", "utilization_percent", "memory_used_mib", "memory_total_mib", "temperature_c"), (number(value.strip()) for value in row))))
        return {"ok": bool(devices), "devices": devices}
    except (OSError, subprocess.SubprocessError):
        return {"ok": False, "devices": [], "error_code": "gpu_probe_unavailable"}


def collect_uploads(chunks_root: Path, now: datetime):
    result = {"ok": True, "active_sessions": 0, "expired_sessions": 0, "completed_sessions": 0, "invalid_manifests": 0, "part_files": 0, "partial_bytes": 0, "oldest_active_seconds": 0}
    try:
        for index, manifest in enumerate(chunks_root.glob("*/manifest.json")):
            if index >= 10000:
                result["scan_truncated"] = True
                break
            try:
                data = read_json(manifest)
                updated = number(data.get("updated_at"))
                if updated is None:
                    raise ValueError("missing_timestamp")
                age = max(0, round(now.timestamp() - updated))
                if data.get("job_id"):
                    result["completed_sessions"] += 1
                elif age > 86400:
                    result["expired_sessions"] += 1
                else:
                    result["active_sessions"] += 1
                    result["oldest_active_seconds"] = max(result["oldest_active_seconds"], age)
                for part in manifest.parent.glob("*.part*"):
                    try:
                        result["partial_bytes"] += part.stat().st_size
                        result["part_files"] += 1
                    except FileNotFoundError:
                        pass  # Completion or cleanup may remove a part concurrently.
            except (OSError, ValueError):
                result["invalid_manifests"] += 1
    except OSError:
        result.update(ok=False, error_code="upload_stats_unavailable")
    return result


def cleanup_uploads():
    try:
        output = subprocess.run([sys.executable, str(Path(__file__).resolve()), "--cleanup-uploads"], capture_output=True, text=True, timeout=15, check=False)
        payload = json.loads(output.stdout)
        if output.returncode != 0 or payload.get("ok") is not True:
            raise ValueError("cleanup_failed")
        return {"ok": True, "eligible_sessions_checked": int(payload.get("eligible_sessions_checked", 0))}
    except (OSError, subprocess.SubprocessError, ValueError, TypeError):
        return {"ok": False, "error_code": "upload_cleanup_deferred"}


def clean_upload_parts_once():
    # Call the service-owned policy: per-session locks, 24h TTL, retain manifests.
    # It also removes leftover parts for uploads already committed to a job.
    try:
        from rallymate_service.config import load_settings
        from rallymate_service.uploads import UploadStore
        settings = load_settings()
        count = UploadStore(settings.uploads_dir / ".chunks", settings.max_upload_bytes).cleanup()
        print(json.dumps({"ok": True, "eligible_sessions_checked": count}))
        return 0
    except Exception:
        print(json.dumps({"ok": False, "error_code": "upload_cleanup_deferred"}))
        return 1


def collect_snapshot(root: Path, state: dict, interval: int, stuck_seconds: int):
    now = utc_now()
    data_root = Path(os.getenv("RALLYMATE_DATA_ROOT", str(root / "service_data"))).resolve()
    database = Path(os.getenv("RALLYMATE_DATABASE_PATH", str(data_root / "jobs.sqlite3"))).resolve()
    # Independent bounded probes run together so several unreachable services
    # do not consume the entire 60-second sampling window serially.
    with ThreadPoolExecutor(max_workers=6) as executor:
        pending_checks = {
            "api_live": executor.submit(probe_http, "http://127.0.0.1:8001/health/live"),
            "api_ready": executor.submit(probe_http, "http://127.0.0.1:8001/health/ready", api_key=os.getenv("RALLYMATE_API_KEY", ""), readiness=True),
            "local_web": executor.submit(probe_http, "http://127.0.0.1:8000/"),
        }
        pending_processes = executor.submit(process_health, root)
        pending_gpu = executor.submit(gpu_health)
        pending_cleanup = executor.submit(cleanup_uploads)
        jobs = collect_jobs(database, state, now, stuck_seconds)
        disk = disk_health(data_root)
        processes = pending_processes.result()
        public_url = current_tunnel_url(root / "service_data/ops/cloudflared.log") if processes["cloudflared"]["state"] == "RUNNING" else None
        if public_url:
            pending_checks["cloudflare"] = executor.submit(probe_http, public_url + "/")
        checks = {name: future.result() for name, future in pending_checks.items()}
        if not public_url:
            checks["cloudflare"] = {"ok": False, "error_code": "tunnel_url_unavailable"}
        gpu, cleanup = pending_gpu.result(), pending_cleanup.result()
    uploads = collect_uploads(data_root / "uploads/.chunks", now)
    uploads["cleanup"] = cleanup
    alerts = []
    for name, process in processes.items():
        if process["state"] != "RUNNING" and not (name == "monitor" and process["state"] == "STARTING"):
            alerts.append({"code": "process_not_running", "component": name})
    for name, check in checks.items():
        if not check["ok"]:
            alerts.append({"code": "health_check_failed", "component": name})
    for name, component in (("queue", jobs), ("disk", disk), ("gpu", gpu), ("uploads", uploads)):
        if not component["ok"]:
            alerts.append({"code": "probe_failed", "component": name})
    if disk.get("low_space"):
        alerts.append({"code": "disk_space_low", "component": "disk"})
    if uploads["invalid_manifests"]:
        alerts.append({"code": "invalid_upload_manifest", "component": "uploads"})
    for job in jobs["running"]:
        if job["stuck"]:
            alerts.append({"code": "job_progress_stalled", "job_id": job["job_id"]})
    prior_alerts = state.get("alerts", [])
    events = []
    for alert in alerts:
        if alert not in prior_alerts:
            events.append({"type": "alert_raised", **alert})
    for alert in prior_alerts:
        if alert not in alerts:
            events.append({"type": "alert_resolved", **alert})
    events.extend({"type": "job_failed", **failure} for failure in jobs["new_failures"])
    events.extend({"type": "job_succeeded", **completed} for completed in jobs.get("new_completions", []))
    if state.get("public_url") and public_url and state["public_url"] != public_url:
        events.append({"type": "public_url_changed", "public_url": public_url})
    state.update(alerts=alerts, public_url=public_url or state.get("public_url"))
    # Keep recent meaningful events across 60s samples so a 15-minute reader
    # cannot miss a failure that occurred between its polls.
    cutoff = now.timestamp() - 24 * 3600
    recent = [event for event in state.get("recent_events", []) if number(event.get("unix_time"), 0) >= cutoff]
    recent.extend({"occurred_at": now.isoformat(), "unix_time": now.timestamp(), **event} for event in events)
    state["recent_events"] = recent[-200:]
    return {
        "schema_version": "rallymate-health/1", "generated_at": now.isoformat(), "interval_seconds": interval,
        "overall_status": "attention" if alerts else "healthy", "public_url": public_url,
        "processes": processes, "http": checks, "jobs": jobs, "disk": disk, "gpu": gpu, "uploads": uploads,
        "alerts": alerts, "new_events": events, "recent_events": state["recent_events"],
    }


def read_snapshot(path: Path, now: datetime | None = None):
    try:
        snapshot = read_json(path)
        if snapshot.get("schema_version") != "rallymate-health/1":
            raise ValueError("wrong_schema")
        age = age_seconds(snapshot.get("generated_at"), now or utc_now())
        interval = number(snapshot.get("interval_seconds"), 60)
        snapshot["snapshot_age_seconds"] = age
        snapshot["stale"] = age is None or age > max(180, interval * 3)
        if snapshot["stale"]:
            snapshot["overall_status"] = "stale"
        return snapshot
    except (OSError, ValueError):
        return {"schema_version": "rallymate-health/1", "overall_status": "unavailable", "stale": True, "error_code": "snapshot_unavailable"}


def main():
    parser = argparse.ArgumentParser(description="RallyMate private service health monitor")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--snapshot", action="store_true", help="Read the last snapshot only; no environment/config loading or probes")
    mode.add_argument("--once", action="store_true", help="Collect and persist one sample including upload cleanup")
    mode.add_argument("--cleanup-uploads", action="store_true", help=argparse.SUPPRESS)
    args = parser.parse_args()
    if args.snapshot:
        snapshot = read_snapshot(OPS_DIR / "health.json")
        print(json.dumps(snapshot, ensure_ascii=False, allow_nan=False))
        return 0 if not snapshot["stale"] else 3
    if args.cleanup_uploads:
        return clean_upload_parts_once()
    os.umask(0o077)
    OPS_DIR.mkdir(parents=True, exist_ok=True)
    interval = max(15, min(3600, int(number(os.getenv("RALLYMATE_MONITOR_INTERVAL_SECONDS"), 60))))
    stuck_seconds = max(60, int(number(os.getenv("RALLYMATE_MONITOR_STUCK_SECONDS"), 900)))
    handler = RotatingFileHandler(OPS_DIR / "health.jsonl", maxBytes=5 * 1024 * 1024, backupCount=5, encoding="utf-8")
    handler.setFormatter(logging.Formatter("%(message)s"))
    logger = logging.getLogger("rallymate.health")
    logger.setLevel(logging.INFO)
    logger.addHandler(handler)
    try:
        state = read_json(OPS_DIR / "monitor-state.json")
    except (OSError, ValueError):
        state = {}
    stop = threading.Event()
    signal.signal(signal.SIGTERM, lambda *_: stop.set())
    signal.signal(signal.SIGINT, lambda *_: stop.set())
    while not stop.is_set():
        started = time.monotonic()
        sample_ok = False
        try:
            snapshot = collect_snapshot(PROJECT_ROOT, state, interval, stuck_seconds)
            atomic_json(OPS_DIR / "health.json", snapshot)
            atomic_json(OPS_DIR / "monitor-state.json", state)
            logger.info(json.dumps(snapshot, ensure_ascii=False, allow_nan=False, separators=(",", ":")))
            sample_ok = True
        except Exception:
            # Do not serialize exception messages: they frequently contain
            # paths or response bodies. The older snapshot will become stale.
            logger.error(json.dumps({"generated_at": utc_now().isoformat(), "event": "monitor_sample_failed"}))
        if args.once:
            handler.close()
            return 0 if sample_ok else 1
        stop.wait(max(0, interval - (time.monotonic() - started)))
    handler.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
