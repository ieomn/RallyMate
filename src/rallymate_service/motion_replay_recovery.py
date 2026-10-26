"""Explicitly recover a known successful provider task; never submit media.

This path only queries and downloads a task already bound to one failed replay.
It keeps normal URL/TLS checks, fences concurrent workers, and records the old
failure without storing a provider URL or credential in its recovery audit.
"""
from __future__ import annotations

import json
from pathlib import Path
import re
import time
import urllib.parse
import uuid

from rallymate_service.motion_replay_provider import ProviderError
from rallymate_service.motion_replay_store import LeaseLost, MotionReplaySettings, MotionReplayStore, sha256_path
from rallymate_service.motion_replay_worker import _Heartbeat, _validate_output


def recover_existing_download(store: MotionReplayStore, settings: MotionReplaySettings, *,
                              replay_id: str, job_id: str, provider, output_validator=None) -> dict:
    if not re.fullmatch(r"[a-f0-9]{32}", replay_id):
        raise ProviderError("recovery_binding_invalid", "恢复任务编号无效。")
    row = store.get(replay_id)
    if (row is None or row["job_id"] != job_id or row["generation_status"] != "failed"
            or row["quality_status"] != "pending_review" or row["lease_token"] is not None
            or row["retry_stage"] not in {"provider_processing", "downloading"}
            or not row["provider_task_id"] or row["provider_task_id"] not in (row["provider_history"] or [])
            or (row["artifacts"] or {}).get("generated_video")):
        raise ProviderError("recovery_binding_invalid", "只能恢复当前分析任务已绑定且尚未发布成品的失败下载。")
    duration = row["request"].get("duration_seconds")
    if not isinstance(duration, int) or isinstance(duration, bool) or duration != settings.duration_seconds:
        raise ProviderError("recovery_binding_invalid", "恢复任务的原始媒体配置不一致。")
    result = provider.query(row["provider_task_id"])
    if result.get("status") != "succeeded" or not isinstance(result.get("download_url"), str):
        raise ProviderError("recovery_provider_not_succeeded", "既有服务商任务尚未确认成功，未启动下载恢复。")
    # The provider's existing download method remains the sole URL validator.
    # This parsing only creates a non-sensitive audit category.
    parsed = urllib.parse.urlsplit(result["download_url"])
    root = (settings.output_root / replay_id).resolve()
    if root.parent != settings.output_root.resolve():
        raise ProviderError("recovery_output_path_invalid", "恢复产物目录无效。")
    token = uuid.uuid4().hex
    directory = root / ("recovery-" + token)
    directory.mkdir(parents=True, exist_ok=False)
    audit_path = directory / "recovery-audit.json"
    audit = {"version": "motion-replay-download-recovery-v1", "replay_id": replay_id, "job_id": job_id,
             "provider_task_id": row["provider_task_id"], "new_generation_created": False,
             "original": {key: row[key] for key in ("generation_status", "quality_status", "error_code", "error_message",
                          "error_retryable", "retry_stage", "attempts", "provider_history", "created_at", "updated_at")},
             "provider_status": "succeeded", "media_domain": parsed.hostname,
             "media_path_category": "mp4_media" if parsed.path.lower().endswith(".mp4") else "other",
             "proxy_dns_compatibility": getattr(provider, "allow_proxy_dns", False) is True,
             "reason": "resume_existing_successful_task_download_with_existing_url_and_tls_checks",
             "status": "prepared", "started_at": time.time()}

    def write_audit():
        audit_path.write_text(json.dumps(audit, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    write_audit()  # Persist the old failure before changing the task row.
    now = time.time()
    with store.connect() as connection:
        connection.execute("BEGIN IMMEDIATE")
        changed = connection.execute("""UPDATE motion_replays SET generation_status='downloading',
            lease_token=?,lease_expires_at=?,worker_id='explicit-download-recovery',updated_at=?,
            download_url=?,completed_at=NULL,next_attempt_at=NULL
            WHERE id=? AND job_id=? AND provider_task_id=? AND generation_status='failed'
            AND quality_status='pending_review' AND lease_token IS NULL AND updated_at=?""",
            (token, now + settings.lease_seconds, now, result["download_url"], replay_id, job_id,
             row["provider_task_id"], row["updated_at"])).rowcount
        connection.commit()
    if changed != 1:
        audit.update(status="aborted", error_code="recovery_state_changed")
        write_audit()
        raise ProviderError("recovery_state_changed", "任务状态已变化，未下载或覆盖其他工作进程的结果。")
    claimed = store.get(replay_id)
    target = directory / "generated.mp4"
    try:
        with _Heartbeat(store, claimed, settings.lease_seconds):
            provider.download(result["download_url"], target)
            metadata = (output_validator or _validate_output)(target, duration)
            metadata = {**metadata, "fidelity_verified": False,
                        "download_recovery_audit": audit_path.relative_to(root).as_posix()}
            audit.update(status="media_validated", media_sha256=sha256_path(target), media_bytes=target.stat().st_size,
                         generated_metadata=metadata, quality_status="pending_review")
            write_audit()
            completed = store.transition(replay_id, token, "completed",
                artifacts={**(row["artifacts"] or {}), "generated_video": target.relative_to(root).as_posix()},
                generated_metadata=metadata, error_code=None, error_message=None, error_retryable=False,
                retry_stage=None, query_failures=0, download_failures=0)
        audit.update(status="completed", completed_at=time.time())
        write_audit()
        return {"id": replay_id, "job_id": job_id, "generation_status": completed["generation_status"],
                "quality_status": completed["quality_status"], "provider_task_id": completed["provider_task_id"],
                "attempts": completed["attempts"], "new_generation_created": False,
                "generated_metadata": metadata, "audit_path": str(audit_path), "media_path": str(target)}
    except Exception as exc:
        code = exc.code if isinstance(exc, ProviderError) else "recovery_lease_lost" if isinstance(exc, LeaseLost) else "recovery_download_failed"
        audit.update(status="failed", error_code=code, completed_at=time.time())
        write_audit()
        if not isinstance(exc, LeaseLost):
            try:
                store.transition(replay_id, token, "failed", error_code=code,
                    error_message="既有成品下载恢复未完成，未创建新的生成任务。", error_retryable=True,
                    retry_stage="provider_processing")
            except LeaseLost:
                pass
        raise ProviderError(code, "既有成品下载恢复未完成，请查看本地恢复审计。") from None
