"""Recoverable generation stages; remote submission is never blindly replayed."""
from __future__ import annotations

import logging
import os
import socket
import sqlite3
import time
from pathlib import Path
from threading import Event, Thread
from typing import Callable

from rallymate_service.motion_replay_provider import MiniMaxVideoProvider, ProviderError, request_prompt
from rallymate_service.motion_replay_store import (
    APPEARANCE_REFERENCE, APPEARANCE_REFERENCE_VERSION, APPEARANCE_STYLE, MAX_CONSECUTIVE_REMOTE_FAILURES, MEDIA_FILES,
    LeaseLost, MotionReplaySettings, MotionReplayStore, sha256_path,
)


LOGGER = logging.getLogger(__name__)


class _Heartbeat:
    def __init__(self, store, row, lease_seconds):
        self.store, self.row, self.lease_seconds = store, row, lease_seconds
        self.stop = Event()
        self.thread = Thread(target=self._run, daemon=True, name="motion-replay-lease")

    def _run(self):
        while not self.stop.wait(min(10.0, self.lease_seconds / 3)):
            try:
                if not self.store.renew(self.row["id"], self.row["lease_token"], self.lease_seconds):
                    return
            except Exception:
                # A later fenced write still prevents publication or a second paid POST.
                LOGGER.warning("motion replay %s lease renewal delayed", self.row["id"])

    def __enter__(self):
        self.thread.start()
        return self

    def __exit__(self, *_):
        self.stop.set()
        self.thread.join(timeout=5)


def _render(*args, **kwargs):
    from rallymate_vision.motion_replay import export_motion_replay
    return export_motion_replay(*args, **kwargs)


def _validate_output(path: Path, duration_seconds: int) -> dict:
    import cv2
    from rallymate_vision.quality import probe_video
    try:
        metadata = probe_video(path)
    except Exception:
        raise ProviderError("invalid_output_video", "下载的视频无法正常解码。") from None
    if (metadata.width < 256 or metadata.height < 256 or metadata.fps <= 0 or metadata.frame_count > 900
            or abs(metadata.duration_ms - duration_seconds * 1000) > 1500):
        raise ProviderError("invalid_output_video", "下载的视频尺寸或时长不符合生成请求。")
    # MP4 metadata can remain readable after a truncated download. Verify every
    # claimed frame, including the tail, without changing timing or geometry.
    capture = cv2.VideoCapture(str(path))
    decoded = 0
    deadline = time.monotonic() + 60
    try:
        if not capture.isOpened():
            raise ProviderError("invalid_output_video", "下载的视频无法正常解码。")
        while decoded <= metadata.frame_count:
            ok, frame = capture.read()
            if not ok:
                break
            if frame.shape[:2] != (metadata.height, metadata.width) or time.monotonic() > deadline:
                raise ProviderError("invalid_output_video", "下载的视频帧校验未通过。")
            decoded += 1
    finally:
        capture.release()
    if decoded != metadata.frame_count:
        raise ProviderError("output_decode_incomplete", "生成视频未能完整解码，将重新查询原任务后重试下载。",
                            retryable=True, automatic_retry=True)
    return {"width": metadata.width, "height": metadata.height, "fps": metadata.fps,
            "frame_count": metadata.frame_count, "decoded_frame_count": decoded, "duration_ms": metadata.duration_ms,
            "requested_duration_ms": duration_seconds * 1000,
            "duration_difference_ms": metadata.duration_ms - duration_seconds * 1000,
            "timing_adjustment": "none", "fidelity_verified": False}


def _verify_source(request: dict) -> None:
    for name in ("frames", "primary_timeline"):
        path = request.get(name + "_path")
        expected = request.get(name + "_sha256")
        if not path or not expected or not Path(path).is_file() or sha256_path(Path(path)) != expected:
            raise ProviderError("source_evidence_changed", "分析输入已改变或缺失，请重新选择来源。")


def _relative_artifacts(root: Path, render_dir: Path, manifest: dict) -> dict:
    advertised = manifest.get("artifacts") if isinstance(manifest, dict) else None
    if not isinstance(advertised, dict):
        raise ProviderError("render_output_invalid", "白模导出缺少必要产物。")
    result = {}
    for name, filename in MEDIA_FILES.items():
        if name == "generated_video":
            continue
        # Renderer names are fixed; never follow a path supplied in a manifest.
        path = render_dir / filename
        if advertised.get(name) != filename or not path.is_file() or path.stat().st_size <= 0:
            raise ProviderError("render_output_invalid", "白模导出缺少必要产物。")
        result[name] = path.relative_to(root).as_posix()
    return result


def _verify_render_snapshot(request: dict, manifest: dict) -> None:
    # Historical tasks retain their recorded video-only / capsule behavior.
    if "appearance_style" not in request:
        return
    render = manifest.get("render", {}) if isinstance(manifest, dict) else None
    if (not isinstance(render, dict) or manifest.get("version") != request.get("renderer_version")
            or render.get("style") != request["appearance_style"]
            or not isinstance(request.get("surface_renderer_version"), str)
            or render.get("renderer_version") != request["surface_renderer_version"]):
        raise ProviderError("renderer_snapshot_mismatch", "人体表面渲染版本已改变，请创建新请求；当前任务尚未提交生成。")


def _appearance_reference(row: dict, root: Path, reference: Path) -> Path | None:
    request = row["request"]
    # Old snapshots intentionally keep their original video-only contract.
    if "appearance_reference" not in request:
        return None
    if (request.get("appearance_reference") != APPEARANCE_REFERENCE
            or request.get("appearance_reference_version") != APPEARANCE_REFERENCE_VERSION):
        raise ProviderError("appearance_reference_unsupported", "该任务的人体外形参考版本不可用，请创建新请求。")
    _verify_render_snapshot(request, row.get("render_manifest") or {})
    relative = row["artifacts"].get("first_frame")
    if not isinstance(relative, str):
        raise ProviderError("appearance_reference_missing", "已导出的人体外形参考图缺失。")
    path = (root / relative).resolve()
    if (path.name != MEDIA_FILES["first_frame"] or not path.is_relative_to(root.resolve())
            or path.parent != reference.resolve().parent or not path.is_file() or path.stat().st_size <= 0):
        raise ProviderError("appearance_reference_missing", "已导出的人体外形参考图缺失。")
    expected = (row.get("render_manifest") or {}).get("artifact_checksums", {}).get("first.png")
    if not isinstance(expected, str) or sha256_path(path) != expected:
        raise ProviderError("appearance_reference_changed", "人体外形参考图与导出记录不一致，请创建新请求。")
    return path


def _record_error(store: MotionReplayStore, row: dict, error: ProviderError, *, retry_stage: str,
                  settings: MotionReplaySettings) -> None:
    updates = {"error_code": error.code, "error_message": error.public_message,
               "error_retryable": error.retryable, "retry_stage": retry_stage}
    remote_retry = (row["provider_task_id"] and retry_stage in {"provider_processing", "downloading"})
    if remote_retry:
        # Refreshing a URL must not reset repeated download failures. Query and
        # download successes only clear the failure counter for their own stage.
        counter = "download_failures" if row["generation_status"] == "downloading" else "query_failures"
        failure_count = row[counter] + 1
        updates[counter] = failure_count
        retry_available = failure_count < MAX_CONSECUTIVE_REMOTE_FAILURES
        retry_number = failure_count
    else:
        retry_available = row["attempts"] < row["max_attempts"]
        retry_number = row["attempts"]
    if error.submission_unknown:
        store.transition(row["id"], row["lease_token"], "submit_unknown", error_code=error.code,
                         error_message=error.public_message, error_retryable=False, retry_stage=None)
    elif error.retryable and error.automatic_retry and retry_available:
        if not remote_retry:
            updates["attempts"] = row["attempts"] + 1
        store.transition(row["id"], row["lease_token"], retry_stage, release=True,
                         delay=min(300.0, max(settings.poll_seconds, 5.0) * 2 ** (retry_number - 1)), **updates)
    else:
        store.transition(row["id"], row["lease_token"], "failed", **updates)


def process_motion_replay(store: MotionReplayStore, settings: MotionReplaySettings,
                          provider_factory: Callable | None = None, renderer: Callable | None = None,
                          worker_id: str | None = None, output_validator: Callable | None = None) -> bool:
    if not settings.enabled:
        return False
    row = store.claim_next(worker_id or f"replay-{socket.gethostname()}-{os.getpid()}", settings.lease_seconds)
    if row is None:
        return False
    renderer, provider_factory = renderer or _render, provider_factory or MiniMaxVideoProvider
    output_validator = output_validator or _validate_output
    root = settings.output_root / row["id"]
    root.mkdir(parents=True, exist_ok=True)
    retry_stage = "queued"
    with _Heartbeat(store, row, settings.lease_seconds):
        try:
            status, request = row["generation_status"], row["request"]
            if status in {"provider_processing", "downloading"}:
                retry_stage = "provider_processing"
                if row["poll_deadline_at"] is not None and time.time() > row["poll_deadline_at"]:
                    raise ProviderError("provider_pending_timeout", "等待生成超过时限；可稍后继续查询同一任务。", retryable=True)
            if status in {"queued", "rendering"}:
                if not row["artifacts"]:
                    row = store.transition(row["id"], row["lease_token"], "rendering")
                    _verify_source(request)
                    appearance_style = request.get("appearance_style", "legacy_capsule")
                    if appearance_style not in {APPEARANCE_STYLE, "legacy_capsule"}:
                        raise ProviderError("appearance_style_unsupported", "该任务的人体表面样式不可用，请创建新请求。")
                    render_dir = root / ("render-" + row["lease_token"])
                    try:
                        manifest = renderer(Path(request["frames_path"]), render_dir,
                                            primary_timeline_path=Path(request["primary_timeline_path"]),
                                            start_ms=request["start_ms"], duration_ms=request["duration_ms"],
                                            fps=24, width=1280, height=720, appearance_style=appearance_style)
                    except ProviderError:
                        raise
                    except Exception as exc:
                        code = getattr(exc, "code", str(exc))
                        code = code if isinstance(code, str) and code.isidentifier() else "render_failed"
                        raise ProviderError(code, "参考白模生成未通过质量检查，请检查人体连续观测和片段长度。") from None
                    artifacts = _relative_artifacts(root, render_dir, manifest)
                    _verify_render_snapshot(request, manifest)
                    # Recheck source after rendering to reject a concurrent artifact replacement.
                    _verify_source(request)
                    row = store.transition(row["id"], row["lease_token"], "queued",
                                           artifacts=artifacts, render_manifest=manifest)
                provider = provider_factory()  # Credentials are checked before the submitting marker.
                prompt = request_prompt(request)
                reference = root / row["artifacts"]["reference_video"]
                if not reference.is_file():
                    raise ProviderError("reference_video_missing", "已导出的参考视频缺失。")
                image = _appearance_reference(row, root, reference)
                appearance_options = {"reference_image": image} if image is not None else {}
                row = store.transition(row["id"], row["lease_token"], "submitting",
                                       provider_submitted_at=time.time(), poll_deadline_at=time.time() + settings.max_pending_seconds,
                                       error_code=None, error_message=None, error_retryable=False)
                task_id = provider.submit(reference, model=request["model"], resolution=request["resolution"],
                                          duration=request["duration_seconds"], prompt=prompt, **appearance_options)
                history = [*row["provider_history"], task_id]
                store.transition(row["id"], row["lease_token"], "provider_processing", release=True,
                                 delay=settings.poll_seconds, provider_task_id=task_id, provider_history=history,
                                 query_failures=0, download_failures=0)
            elif status == "provider_processing":
                retry_stage = "provider_processing"
                provider = provider_factory()
                result = provider.query(row["provider_task_id"])
                if result["status"] in {"queued", "running"}:
                    store.transition(row["id"], row["lease_token"], "provider_processing", release=True,
                                     delay=settings.poll_seconds, query_failures=0,
                                     error_code=None, error_message=None, error_retryable=False)
                elif result["status"] in {"failed", "cancelled"}:
                    row = store.transition(row["id"], row["lease_token"], "provider_processing", query_failures=0)
                    retry_stage = "queued"  # Only an explicit user retry can create another paid task.
                    raise ProviderError("provider_generation_failed", "服务商任务未完成，可手动重试生成。", retryable=True)
                else:
                    store.transition(row["id"], row["lease_token"], "downloading", release=True,
                                     download_url=result["download_url"], query_failures=0,
                                     error_code=None, error_message=None, error_retryable=False)
            elif status == "downloading":
                # If a signed URL expires, query the same task again; never resubmit it.
                retry_stage = "provider_processing"
                provider = provider_factory()
                generated_dir = root / ("download-" + row["lease_token"])
                target = generated_dir / "generated.mp4"
                provider.download(row["download_url"], target)
                metadata = output_validator(target, request["duration_seconds"])
                artifacts = {**row["artifacts"], "generated_video": target.relative_to(root).as_posix()}
                store.transition(row["id"], row["lease_token"], "completed", artifacts=artifacts, generated_metadata=metadata,
                                 error_code=None, error_message=None, error_retryable=False, retry_stage=None,
                                 query_failures=0, download_failures=0)
                # quality_status stays pending_review. Decoding is not fidelity validation.
        except LeaseLost:
            LOGGER.warning("motion replay %s lease lost; no result published", row["id"])
        except ProviderError as exc:
            try:
                _record_error(store, row, exc, retry_stage=retry_stage, settings=settings)
            except LeaseLost:
                pass
            LOGGER.warning("motion replay %s: %s", row["id"], exc.code)
        except Exception:
            # Unclassified exceptions must not leak keys, request bodies or local paths.
            unknown = row["generation_status"] == "submitting"
            error = ProviderError("submission_outcome_unknown" if unknown else "generation_worker_failed",
                                  "提交结果尚未确认，不会自动重复提交。" if unknown else "生成工作进程未能完成当前步骤。",
                                  submission_unknown=unknown)
            try:
                _record_error(store, row, error, retry_stage=retry_stage, settings=settings)
            except LeaseLost:
                pass
            LOGGER.warning("motion replay %s: %s", row["id"], error.code)
    return True


def run_motion_replay_worker(store: MotionReplayStore, settings: MotionReplaySettings,
                             provider_factory: Callable | None = None, stop_event: Event | None = None,
                             poll_seconds: float = 1.0, once: bool = False) -> None:
    stop_event = stop_event or Event()
    initialized = False
    infrastructure_failures = 0
    while not stop_event.is_set():
        try:
            if not initialized:
                store.initialize()
                initialized = True
            handled = process_motion_replay(store, settings, provider_factory=provider_factory)
            infrastructure_failures = 0
        except (sqlite3.OperationalError, OSError) as exc:
            # A transient lock or unavailable output volume must not kill the
            # only worker thread. Leases still bound recovery of a claimed job.
            infrastructure_failures += 1
            LOGGER.warning("motion replay infrastructure unavailable (%s)", type(exc).__name__)
            if once:
                return
            stop_event.wait(min(30.0, max(1.0, poll_seconds) * 2 ** min(infrastructure_failures - 1, 5)))
            continue
        if once:
            return
        if not handled:
            stop_event.wait(max(0.1, poll_seconds))
