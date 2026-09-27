#!/usr/bin/env python3
"""Local, read-only SSH heartbeat. No password fallback or automatic deployment."""
from __future__ import annotations

import copy
from datetime import datetime, timezone
import hashlib
import json
import math
import os
from pathlib import Path
import re
import socket
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parents[2]
STATE_DIR = ROOT / ".codex_tmp" / "autodl"
HOST = "connect.cqa1.seetacloud.com"
PORT = 29196
MAX_REMOTE_BYTES = 2 * 1024 * 1024
ALLOWED_ROOTS = {"src", "scoring-demo-web", "deploy/autodl", "tests"}
CODE_SUFFIXES = {".py", ".ts", ".tsx", ".js", ".jsx", ".mjs", ".cjs", ".json", ".toml", ".yaml", ".yml", ".sh", ".ps1", ".css", ".html", ".md", ".txt", ".conf", ".ini"}
CACHE_NAMES = {"node_modules", ".git", ".venv", "__pycache__", "dist", "build", "coverage", ".next", ".cache", ".pytest_cache", ".mypy_cache", ".ruff_cache", ".codex_tmp"}
SECRET_NAME = re.compile(r"(?:^|[_.-])(?:secrets?|credentials?|tokens?|private|password|passwd)(?:[_.-]|$)", re.I)


class CheckFailure(Exception):
    """Only fixed public error codes cross this boundary, never raw exceptions."""


def safe_token(value):
    return value if isinstance(value, str) and re.fullmatch(r"[A-Za-z0-9_-]{1,80}", value) else None


def public_url(value):
    return value if isinstance(value, str) and re.fullmatch(r"https://[a-z0-9-]+\.trycloudflare\.com", value) else None


def finite(value):
    return value if isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value) else None


def safe_time(value):
    if not isinstance(value, str):
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        return parsed.isoformat() if parsed.tzinfo else None
    except ValueError:
        return None


def compact_alert(value):
    if not isinstance(value, dict):
        return None
    result = {key: token for key in ("code", "component", "job_id") if (token := safe_token(value.get(key))) is not None}
    return result if "code" in result else None


def compact_event(value):
    if not isinstance(value, dict) or value.get("type") not in {"alert_raised", "alert_resolved", "job_failed", "job_succeeded", "job_completed", "public_url_changed"}:
        return None
    result = {"type": value["type"]}
    for key in ("code", "component", "job_id"):
        if token := safe_token(value.get(key)):
            result[key] = token
    for key in ("occurred_at", "failed_at", "completed_at"):
        if timestamp := safe_time(value.get(key)):
            result[key] = timestamp
    if url := public_url(value.get("public_url")):
        result["public_url"] = url
    return result if result.get("occurred_at") else None


def compact_snapshot(snapshot):
    if not isinstance(snapshot, dict) or snapshot.get("schema_version") != "rallymate-health/1":
        raise CheckFailure("invalid_snapshot_schema")
    status = snapshot.get("overall_status")
    if status not in {"healthy", "attention", "stale", "unavailable"}:
        raise CheckFailure("invalid_snapshot_schema")
    result = {
        "status": status, "generated_at": safe_time(snapshot.get("generated_at")),
        "snapshot_age_seconds": finite(snapshot.get("snapshot_age_seconds")),
        "stale": snapshot.get("stale") is True,
        "public_url": public_url(snapshot.get("public_url")),
        "alerts": [alert for raw in snapshot.get("alerts", []) if (alert := compact_alert(raw))] if isinstance(snapshot.get("alerts", []), list) else [],
    }
    if result["stale"] and result["status"] != "unavailable":
        result["status"] = "stale"
    if result["status"] in {"healthy", "attention"} and result["generated_at"] is None:
        raise CheckFailure("invalid_snapshot_timestamp")
    result["http"] = {key: {"ok": value.get("ok") is True, "http_status": finite(value.get("http_status"))} for key, value in snapshot.get("http", {}).items() if key in {"api_live", "api_ready", "local_web", "cloudflare"} and isinstance(value, dict)} if isinstance(snapshot.get("http"), dict) else {}
    jobs = snapshot.get("jobs") if isinstance(snapshot.get("jobs"), dict) else {}
    counts = jobs.get("counts") if isinstance(jobs.get("counts"), dict) else {}
    result["job_counts"] = {key: finite(counts.get(key)) for key in ("queued", "running", "succeeded", "failed")}
    disk = snapshot.get("disk") if isinstance(snapshot.get("disk"), dict) else {}
    result["disk"] = {key: finite(disk.get(key)) for key in ("free_bytes", "free_percent")}
    events = snapshot.get("recent_events", [])
    result["recent_events"] = [event for raw in events[-200:] if (event := compact_event(raw))] if isinstance(events, list) else []
    return result


def fetch_snapshot(key_path: Path):
    try:
        import paramiko
    except ImportError:
        raise CheckFailure("paramiko_unavailable") from None
    if not key_path.is_file():
        raise CheckFailure("monitor_key_unavailable")
    client = paramiko.SSHClient()
    try:
        client.load_system_host_keys()
        client.set_missing_host_key_policy(paramiko.RejectPolicy())
        client.connect(HOST, port=PORT, username="root", key_filename=str(key_path),
                       look_for_keys=False, allow_agent=False, timeout=10,
                       auth_timeout=10, banner_timeout=10)
        # The installed authorized_keys forced command ignores this fixed word.
        # No shell, SFTP, arbitrary argument or forwarded port is requested.
        stdin, stdout, _ = client.exec_command("snapshot", timeout=10, get_pty=False)
        stdin.close()
        channel = stdout.channel
        channel.settimeout(5)
        chunks, size, stderr_size = [], 0, 0
        deadline = time.monotonic() + 25
        while True:
            if time.monotonic() > deadline:
                raise CheckFailure("snapshot_timeout")
            if channel.recv_ready():
                chunk = channel.recv(min(32768, MAX_REMOTE_BYTES - size + 1))
                size += len(chunk)
                if size > MAX_REMOTE_BYTES:
                    raise CheckFailure("snapshot_too_large")
                chunks.append(chunk)
            if channel.recv_stderr_ready():
                stderr_size += len(channel.recv_stderr(4096))
                if stderr_size > 16384:
                    raise CheckFailure("unexpected_remote_output")
            if channel.exit_status_ready() and not channel.recv_ready() and not channel.recv_stderr_ready():
                break
            time.sleep(0.03)
        exit_code = channel.recv_exit_status()
        if exit_code not in (0, 3):
            raise CheckFailure("snapshot_command_failed")
        try:
            payload = json.loads(b"".join(chunks).decode("utf-8"))
        except (UnicodeError, ValueError):
            raise CheckFailure("invalid_snapshot_json") from None
        return compact_snapshot(payload)
    except paramiko.BadHostKeyException:
        raise CheckFailure("ssh_host_key_mismatch") from None
    except paramiko.AuthenticationException:
        raise CheckFailure("ssh_authentication_failed") from None
    except (socket.timeout, TimeoutError):
        raise CheckFailure("ssh_timeout") from None
    except (paramiko.SSHException, OSError):
        # Includes unknown hosts rejected by RejectPolicy. Never auto-enroll.
        raise CheckFailure("ssh_connection_or_host_verification_failed") from None
    finally:
        client.close()


def allowed_code_path(name: str):
    if not isinstance(name, str) or "\\" in name or ":" in name or name.startswith("/") or any(ord(char) < 32 or ord(char) == 127 for char in name):
        return False
    parts = name.split("/")
    if any(part in {"", ".", ".."} or part.lower() in CACHE_NAMES for part in parts):
        return False
    if not any(name.startswith(prefix + "/") for prefix in ALLOWED_ROOTS):
        return False
    filename = parts[-1].lower()
    if filename.startswith((".env", ".dev.vars", "id_rsa", "id_ed25519", "monitor_key")) or filename == "runtime.env" or re.search(r"api[_-]?key", filename) or any(SECRET_NAME.search(part) for part in parts):
        return False
    return Path(filename).suffix in CODE_SUFFIXES


def scan_code(root: Path):
    try:
        command = ["git", "ls-files", "--cached", "--others", "--exclude-standard", "-z", "--", "src", "scoring-demo-web", "deploy/autodl", "tests"]
        output = subprocess.run(command, cwd=root, capture_output=True, timeout=15, check=False)
        if output.returncode != 0:
            raise CheckFailure("code_inventory_unavailable")
        names = sorted(set(output.stdout.decode("utf-8").split("\0")))
        hashes, skipped, total_bytes = {}, 0, 0
        deadline = time.monotonic() + 20
        for name in names:
            if not allowed_code_path(name):
                continue
            path = root / name
            try:
                path.resolve().relative_to(root.resolve())
                if path.is_symlink() or not path.is_file() or path.stat().st_size > 2 * 1024 * 1024:
                    skipped += 1
                    continue
                total_bytes += path.stat().st_size
                if len(hashes) >= 10000 or total_bytes > 128 * 1024 * 1024 or time.monotonic() > deadline:
                    raise CheckFailure("code_inventory_unavailable")
                # Stream bytes only for hashing; never decode or print source.
                with path.open("rb") as handle:
                    hashes[name] = hashlib.file_digest(handle, "sha256").hexdigest()
            except (OSError, ValueError):
                skipped += 1
        return {"ok": True, "hashes": hashes, "skipped_files": skipped}
    except (OSError, subprocess.SubprocessError, UnicodeError):
        raise CheckFailure("code_inventory_unavailable") from None


def read_state(path: Path):
    try:
        if path.stat().st_size > 4 * 1024 * 1024:
            raise ValueError("oversize")
        state = json.loads(path.read_text("utf-8"))
        return state if isinstance(state, dict) and state.get("schema_version") == "rallymate-heartbeat-state/1" else {}
    except (OSError, ValueError):
        return {}


def plan_result(previous: dict, snapshot: dict | None, failure: str | None, code: dict, checked_at: str):
    state = copy.deepcopy(previous)
    state["schema_version"] = "rallymate-heartbeat-state/1"
    reasons, notifications = [], []
    output = {"schema_version": "rallymate-heartbeat-check/1", "checked_at": checked_at, "notify": False}
    connection = previous.get("connection") if isinstance(previous.get("connection"), dict) else {}
    last_good = previous.get("last_good_snapshot") if isinstance(previous.get("last_good_snapshot"), dict) else None
    last_url = public_url(previous.get("last_public_url")) or public_url((last_good or {}).get("public_url"))
    if failure:
        output.update(remote_status="offline", error_code=safe_token(failure) or "remote_check_failed", health=None, new_events=[])
        if connection.get("status") != "offline" or connection.get("error_code") != failure:
            reasons.append("remote_unavailable")
        state["connection"] = {"status": "offline", "error_code": safe_token(failure), "since": connection.get("since") if connection.get("status") == "offline" else checked_at}
        # Keep the last successful snapshot, URL and event cursor unchanged.
    else:
        assert snapshot is not None
        prior_keys = previous.get("seen_event_keys", [])
        prior_keys = [key for key in prior_keys if isinstance(key, str) and re.fullmatch(r"[a-f0-9]{64}", key)]
        seen = set(prior_keys)
        first = last_good is None
        for event in snapshot.get("recent_events", []):
            key = hashlib.sha256(json.dumps(event, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
            if not first and key not in seen:
                notifications.append(event)
            if key not in seen:
                prior_keys.append(key)
                seen.add(key)
        if connection.get("status") == "offline":
            reasons.append("remote_connection_restored")
        if first:
            if snapshot["status"] != "healthy" or snapshot["alerts"]:
                reasons.append("initial_health_requires_attention")
        elif snapshot["status"] != last_good.get("status") or snapshot["alerts"] != last_good.get("alerts"):
            reasons.append("health_state_changed")
        if snapshot.get("public_url") and last_url and snapshot["public_url"] != last_url:
            reasons.append("public_url_changed")
        if notifications:
            reasons.append("new_monitor_events")
        state["seen_event_keys"] = prior_keys[-1000:]
        state["connection"] = {"status": "online", "last_success_at": checked_at}
        state["last_good_snapshot"] = {key: value for key, value in snapshot.items() if key != "recent_events"}
        last_url = snapshot.get("public_url") or last_url
        output.update(remote_status="online", health=state["last_good_snapshot"], new_events=notifications)
    if code.get("ok"):
        hashes = {name: digest for name, digest in code["hashes"].items() if allowed_code_path(name) and re.fullmatch(r"[a-f0-9]{64}", digest)}
        old = previous.get("code_hashes")
        baseline = not isinstance(old, dict)
        old = {name: digest for name, digest in (old if isinstance(old, dict) else {}).items() if allowed_code_path(name) and isinstance(digest, str)}
        added = sorted(hashes.keys() - old.keys()) if not baseline else []
        deleted = sorted(old.keys() - hashes.keys())
        modified = sorted(name for name in hashes.keys() & old.keys() if hashes[name] != old[name])
        total = len(added) + len(deleted) + len(modified)
        output["code_changes"] = {"ok": True, "baseline_created": baseline, "file_count": len(hashes), "counts": {"added": len(added), "modified": len(modified), "deleted": len(deleted)}, "paths": {"added": added[:200], "modified": modified[:200], "deleted": deleted[:200]}, "paths_truncated": any(len(values) > 200 for values in (added, modified, deleted)), "skipped_files": code.get("skipped_files", 0), "deployment_performed": False}
        if total:
            reasons.append("local_code_changes_for_review")
        state["code_hashes"] = hashes
    else:
        output["code_changes"] = {"ok": False, "error_code": "code_inventory_unavailable", "deployment_performed": False}
        if previous.get("code_scan_ok") is not False:
            reasons.append("code_inventory_unavailable")
    state["code_scan_ok"] = code.get("ok") is True
    state["last_checked_at"] = checked_at
    state["last_public_url"] = last_url
    output["last_public_url"] = last_url
    output["notify_reasons"] = reasons
    output["notify"] = bool(reasons)
    if failure and last_good:
        output["last_success_at"] = safe_time(connection.get("last_success_at") or previous.get("last_success_at"))
    if snapshot is not None:
        state["last_success_at"] = checked_at
    return output, state


def write_state(path: Path, state: dict):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    try:
        with temporary.open("w", encoding="utf-8") as handle:
            json.dump(state, handle, ensure_ascii=False, allow_nan=False, separators=(",", ":"))
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(temporary, 0o600)
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def main():
    os.umask(0o077)
    previous = read_state(STATE_DIR / "heartbeat-state.json")
    checked_at = datetime.now(timezone.utc).isoformat()
    try:
        snapshot, failure = fetch_snapshot(STATE_DIR / "monitor_key"), None
    except CheckFailure as exc:
        snapshot, failure = None, str(exc)
    except Exception:
        snapshot, failure = None, "remote_check_failed"
    try:
        code = scan_code(ROOT)
    except CheckFailure:
        code = {"ok": False}
    output, state = plan_result(previous, snapshot, failure, code, checked_at)
    try:
        write_state(STATE_DIR / "heartbeat-state.json", state)
    except OSError:
        output["state_persisted"] = False
        output["notify"] = True
        output["notify_reasons"].append("heartbeat_state_write_failed")
    else:
        output["state_persisted"] = True
    print(json.dumps(output, ensure_ascii=False, allow_nan=False))
    # Offline is data, not a traceback. The scheduler uses notify + reasons;
    # repeat outages stay quiet while the last good state remains available.
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
