"""Authenticated motion-replay creation, read-only status, and local media."""
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Callable

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import FileResponse, JSONResponse

from rallymate_service.database import JobDatabase
from rallymate_service.motion_replay_provider import PROMPT
from rallymate_service.motion_replay_source import SOURCE_MEDIA_FILE, SourceVideoError, source_video_path
from rallymate_service.motion_replay_store import (
    APPEARANCE_REFERENCE, APPEARANCE_REFERENCE_VERSION, APPEARANCE_STYLE, MEDIA_FILES,
    PROMPT_VERSION, REPLAY_VERSION, MotionReplaySettings,
    MotionReplayStore, ReplayConflict, sha256_path, timestamp_iso,
)


_KEY = re.compile(r"^[A-Za-z0-9._:/-]{1,128}$")
_ID = re.compile(r"^[a-f0-9]{32}$")
MAX_REQUEST_BYTES = 4096


class MotionReplayInputError(ValueError):
    def __init__(self, message: str, status_code: int = 422):
        super().__init__(message)
        self.status_code = status_code


def _source_files(job: dict) -> tuple[Path, Path]:
    output = Path(job["output_dir"]).resolve()
    paths = (output / "frames.jsonl", output / "primary-player.jsonl")
    for path in paths:
        if not path.is_file() or not path.resolve().is_relative_to(output) or path.stat().st_size <= 0:
            raise MotionReplayInputError("当前分析任务缺少可用人体关键点或主体时间线。", 409)
    return paths


def enqueue_job_replay(*, jobs: JobDatabase, store: MotionReplayStore, settings: MotionReplaySettings,
                       job_id: str, payload: dict | None = None, idempotency_key: str | None = None,
                       summary_loader: Callable | None = None) -> dict:
    if not settings.enabled:
        raise MotionReplayInputError("个人动作白模生成尚未启用。", 503)
    payload = {} if payload is None else payload
    if not isinstance(payload, dict) or set(payload) - {"start_ms", "duration_ms", "episode_id"}:
        raise MotionReplayInputError("只支持 episode_id、start_ms 和 duration_ms。")
    if idempotency_key is not None and not _KEY.fullmatch(idempotency_key):
        raise MotionReplayInputError("Idempotency-Key 必须是 1～128 位安全字符。")
    if "episode_id" in payload and "start_ms" in payload:
        raise MotionReplayInputError("episode_id 与 start_ms 只能选择一个。")
    duration = payload.get("duration_ms", settings.duration_seconds * 1000)
    if isinstance(duration, bool) or not isinstance(duration, int) or duration != 6000:
        raise MotionReplayInputError("当前生成片段时长固定为 6000 毫秒。")
    episode_id = payload.get("episode_id")
    if "episode_id" in payload and (not isinstance(episode_id, str) or not 1 <= len(episode_id) <= 256):
        raise MotionReplayInputError("episode_id 无效。")
    if "start_ms" in payload:
        start = payload["start_ms"]
        if isinstance(start, bool) or not isinstance(start, int) or not 0 <= start <= 30 * 60 * 1000:
            raise MotionReplayInputError("start_ms 必须是有效的非负整数毫秒。")
    client_request = {"duration_ms": duration}
    if "episode_id" in payload:
        client_request["episode_id"] = episode_id
    elif "start_ms" in payload:
        client_request["start_ms"] = payload["start_ms"]
    else:
        client_request["selection"] = "automatic"
    job = jobs.get_job(job_id)
    if job is None:
        raise MotionReplayInputError("分析任务不存在。", 404)
    if job["status"] != "succeeded":
        raise MotionReplayInputError("请等待分析任务完成后再生成白模。", 409)
    if idempotency_key:
        original = store.find_idempotent(job_id, idempotency_key, client_request)
        if original is not None:
            return original
    frames, primary = _source_files(job)
    if "episode_id" in payload:
        summary = summary_loader(job) if summary_loader else job.get("summary") or {}
        families = (summary.get("action_recognition") or {}).get("motion_analysis", {}).get("families", {})
        episodes = [episode for family in families.values() if isinstance(family, dict)
                    for episode in family.get("episodes", []) if isinstance(episode, dict)] if isinstance(families, dict) else []
        matching = [episode for episode in episodes if episode.get("episode_id") == episode_id]
        if len(matching) != 1:
            raise MotionReplayInputError("所选动作片段不存在或存在歧义。", 404)
        start = matching[0].get("start_ms")
    elif "start_ms" in payload:
        start = payload["start_ms"]
    else:
        from rallymate_vision.motion_replay import MotionReplayError, select_motion_replay_window
        try:
            start = select_motion_replay_window(frames, primary, duration_ms=duration)["start_ms"]
        except (MotionReplayError, OSError, ValueError):
            raise MotionReplayInputError("未找到连续可观测的 6 秒动作片段，可指定其他起始时间或补充清晰视频。", 409) from None
    if isinstance(start, bool) or not isinstance(start, int) or not 0 <= start <= 30 * 60 * 1000:
        raise MotionReplayInputError("start_ms 必须是有效的非负整数毫秒。")
    from rallymate_vision.motion_replay import FULL_BODY_RENDERER_VERSION, MOTION_REPLAY_VERSION
    request = {
        "version": REPLAY_VERSION, "prompt_version": PROMPT_VERSION, "prompt": PROMPT,
        "prompt_strategy": "structured",
        "renderer_version": MOTION_REPLAY_VERSION,
        "surface_renderer_version": FULL_BODY_RENDERER_VERSION,
        "appearance_style": APPEARANCE_STYLE,
        "appearance_reference": APPEARANCE_REFERENCE,
        "appearance_reference_version": APPEARANCE_REFERENCE_VERSION,
        "client_request": client_request,
        "frames_path": str(frames), "frames_sha256": sha256_path(frames),
        "primary_timeline_path": str(primary), "primary_timeline_sha256": sha256_path(primary),
        "episode_id": episode_id, "start_ms": start, "duration_ms": duration,
        "model": settings.model, "resolution": settings.resolution,
        "duration_seconds": settings.duration_seconds, "max_pending_seconds": settings.max_pending_seconds,
    }
    return store.create(job_id, request, max_attempts=settings.max_attempts, idempotency_key=idempotency_key)


def _media_path(row: dict, settings: MotionReplaySettings, key: str) -> Path | None:
    if not _ID.fullmatch(row["id"]) or key not in MEDIA_FILES:
        return None
    if key == "generated_video" and row["generation_status"] != "completed":
        return None
    relative = row["artifacts"].get(key)
    if not isinstance(relative, str):
        return None
    base = settings.output_root.resolve()
    root = (base / row["id"]).resolve()
    if not root.is_relative_to(base):
        return None
    path = (root / relative).resolve()
    if path.name != MEDIA_FILES[key] or not path.is_relative_to(root) or not path.is_file():
        return None
    return path


def public_motion_replay(row: dict, settings: MotionReplaySettings) -> dict:
    prefix = (settings.public_base_url or "").rstrip("/")
    url = f"{prefix}/v1/motion-replays/{row['id']}"
    request = row["request"]
    return {
        "id": row["id"], "job_id": row["job_id"], "version": request.get("version", REPLAY_VERSION),
        "renderer_version": request.get("renderer_version"),
        "surface_renderer_version": request.get("surface_renderer_version"),
        "prompt_version": request.get("prompt_version"),
        "prompt_strategy": request.get("prompt_strategy", "legacy"),
        "appearance_reference": request.get("appearance_reference"),
        "generation_status": row["generation_status"], "quality_status": row["quality_status"],
        "model": request["model"], "resolution": request["resolution"], "duration_seconds": request["duration_seconds"],
        "source_window": {"start_ms": request["start_ms"], "duration_ms": request["duration_ms"]},
        "episode_id": request.get("episode_id"),
        "generated_metadata": row.get("generated_metadata"),
        "created_at": timestamp_iso(row["created_at"]), "updated_at": timestamp_iso(row["updated_at"]),
        "completed_at": timestamp_iso(row["completed_at"]), "attempts": row["attempts"],
        "next_attempt_at": timestamp_iso(row["next_attempt_at"]),
        "error": ({"code": row["error_code"], "message": row["error_message"], "retryable": row["error_retryable"]}
                  if row["error_code"] else None),
        "retry_allowed": settings.enabled and MotionReplayStore.can_retry(row),
        "media": {"original_video": f"{url}/media/{SOURCE_MEDIA_FILE}",
                  **{key: f"{url}/media/{filename}" if _media_path(row, settings, key) else None
                     for key, filename in MEDIA_FILES.items()}},
        "links": {"self": url, "retry": f"{url}/retry"},
        "semantics": {"original": "verified_uploaded_video_source_window", "original_playback_start_ms": 0,
                      "original_audio_included": False,
                      "reference": "deterministic_replay_of_2d_pose_with_labeled_display_continuity"
                      if (row.get("render_manifest") or {}).get("render", {}).get("display_continuity")
                      else "deterministic_replay_of_observed_2d_pose",
                      "generated": "ai_visualization_pending_fidelity_review", "used_for_scoring": False,
                      "fidelity_verified": row["quality_status"] == "approved",
                      "message_zh": "白模由二维轨迹驱动，体表厚度和光照仅作示意，不是真实体型或三维重建；AI 视频的动作保真待复核，二者均不参与评分。"},
    }


def _public_manifest(payload: dict, *, trajectory: bool = False) -> dict:
    # Source paths, prompts, provider responses and arbitrary extension keys are never exposed.
    allowed = {"schema_version", "version", "result_kind", "render", "quality", "video", "artifact_checksums", "frame_map"}
    result = {key: payload[key] for key in allowed if key in payload}
    source = payload.get("source", {})
    source_allowed = {"frames_sha256", "primary_timeline_sha256", "start_ms", "duration_ms", "width", "height",
                      "timestamp_source", "observed_frame_count", "selection_source", "person_track_id"}
    result["source"] = {key: source[key] for key in source_allowed if key in source}
    if trajectory:
        result.update({key: payload[key] for key in ("observations", "display_frames", "frames") if key in payload})
    result["generated_fidelity_status"] = "pending_review"
    return result


async def _read_payload(request: Request) -> dict:
    body = bytearray()
    async for part in request.stream():
        if len(body) + len(part) > MAX_REQUEST_BYTES:
            raise HTTPException(413, "生成请求过大。")
        body.extend(part)
    try:
        payload = json.loads(body) if body else {}
    except (ValueError, UnicodeDecodeError):
        raise HTTPException(422, "请求体必须为 JSON 对象。") from None
    if not isinstance(payload, dict):
        raise HTTPException(422, "请求体必须为 JSON 对象。")
    return payload


def build_motion_replay_router(*, jobs: JobDatabase, store: MotionReplayStore, settings: MotionReplaySettings,
                               authorize: Callable, summary_loader: Callable | None = None) -> APIRouter:
    router = APIRouter(tags=["motion-replays"], dependencies=[Depends(authorize)])

    def get_row(replay_id):
        row = store.get(replay_id) if _ID.fullmatch(replay_id) else None
        if row is None:
            raise HTTPException(404, "动作白模任务不存在。")
        return row

    @router.post("/v1/jobs/{job_id}/motion-replays", status_code=202)
    async def create_replay(job_id: str, request: Request):
        from starlette.concurrency import run_in_threadpool
        payload = await _read_payload(request)
        try:
            row = await run_in_threadpool(enqueue_job_replay, jobs=jobs, store=store, settings=settings,
                                          job_id=job_id, payload=payload,
                                          idempotency_key=request.headers.get("Idempotency-Key"), summary_loader=summary_loader)
        except MotionReplayInputError as exc:
            raise HTTPException(exc.status_code, str(exc)) from None
        except ReplayConflict:
            raise HTTPException(409, "该 Idempotency-Key 已用于其他生成请求。") from None
        return public_motion_replay(row, settings)

    @router.get("/v1/jobs/{job_id}/motion-replays")
    def list_replays(job_id: str, limit: int = Query(30, ge=1, le=100)):
        if jobs.get_job(job_id) is None:
            raise HTTPException(404, "分析任务不存在。")
        return {"items": [public_motion_replay(row, settings) for row in store.list_for_job(job_id, limit)]}

    @router.get("/v1/motion-replays/{replay_id}")
    def replay_status(replay_id: str):
        return public_motion_replay(get_row(replay_id), settings)

    @router.post("/v1/motion-replays/{replay_id}/retry", status_code=202)
    async def retry_replay(replay_id: str, request: Request):
        if await _read_payload(request):
            raise HTTPException(422, "重试请求体应为空对象。")
        get_row(replay_id)
        if not settings.enabled:
            raise HTTPException(503, "个人动作白模生成尚未启用。")
        try:
            return public_motion_replay(store.retry(replay_id), settings)
        except ReplayConflict:
            raise HTTPException(409, "该任务不可安全重试；提交结果未知的任务需先核对服务商记录。") from None

    @router.get("/v1/motion-replays/{replay_id}/media/{artifact}")
    def replay_media(replay_id: str, artifact: str):
        row = get_row(replay_id)
        key = next((key for key, filename in MEDIA_FILES.items() if filename == artifact), None)
        if artifact == SOURCE_MEDIA_FILE:
            try:
                path = source_video_path(row, jobs.get_job(row["job_id"]), settings)
            except SourceVideoError as exc:
                raise HTTPException(409, str(exc)) from None
        else:
            path = _media_path(row, settings, key) if key else None
        if path is None:
            raise HTTPException(404, "该媒体尚未生成或不存在。")
        headers = {"Cache-Control": "private, max-age=0, must-revalidate", "X-Content-Type-Options": "nosniff"}
        if artifact.endswith(".json"):
            try:
                if path.stat().st_size > 8 * 1024 * 1024:
                    raise ValueError()
                payload = json.loads(path.read_text(encoding="utf-8"))
                if not isinstance(payload, dict):
                    raise ValueError()
                return JSONResponse(_public_manifest(payload, trajectory=key == "trajectory"), headers=headers)
            except (OSError, ValueError):
                raise HTTPException(409, "白模记录暂不可读取。") from None
        return FileResponse(path, media_type="video/mp4" if artifact.endswith(".mp4") else "image/png", headers=headers)

    return router
