"""Source-aligned original footage, derived only from a verified uploaded video."""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import uuid
from pathlib import Path
from threading import Lock

from rallymate_service.motion_replay_store import MotionReplaySettings, sha256_path


SOURCE_MEDIA_FILE = "source.mp4"
_CACHE_VERSION = "source-window-v1"
_LOCKS = tuple(Lock() for _ in range(16))
_VIDEO_SUFFIXES = {".mp4", ".mov", ".m4v", ".avi", ".mkv"}


class SourceVideoError(ValueError):
    """A safe public source error; never includes local paths or process output."""


def _source_identity(row: dict, job: dict | None, settings: MotionReplaySettings) -> tuple[Path, dict]:
    if not job or job.get("id") != row["job_id"] or job.get("status") != "succeeded":
        raise SourceVideoError("原视频所属分析任务不可用。")
    base = Path(settings.data_root).resolve()
    uploads = (base / "uploads").resolve()
    source = Path(job["video_path"]).resolve()
    if (not uploads.is_relative_to(base) or source.parent != uploads or source.stem != job["id"]
            or source.suffix.lower() not in _VIDEO_SUFFIXES or not source.is_file()):
        raise SourceVideoError("已上传的原视频缺失或来源路径无效。")
    summary = job.get("summary") or {}
    provenance = (summary.get("minimum_scoring_loop") or {}).get("provenance") or {}
    digest = provenance.get("video_sha256")
    frames_digest = provenance.get("frames_sha256")
    if (not isinstance(digest, str) or not re.fullmatch(r"[a-fA-F0-9]{64}", digest)
            or not isinstance(frames_digest, str)
            or frames_digest.lower() != row["request"].get("frames_sha256", "").lower()):
        raise SourceVideoError("该分析记录缺少可验证的原视频来源，无法提供准确对照。")
    provenance_path = provenance.get("video_path")
    if not isinstance(provenance_path, str) or Path(provenance_path).resolve() != source:
        raise SourceVideoError("原视频路径与分析来源记录不一致。")
    start, duration = row["request"]["start_ms"], row["request"]["duration_ms"]
    if (isinstance(start, bool) or not isinstance(start, int) or not 0 <= start <= 1800000
            or duration != 6000 or isinstance(duration, bool)):
        raise SourceVideoError("原视频对照片段时间窗无效。")
    return source, {"version": _CACHE_VERSION, "replay_id": row["id"], "source_video_sha256": digest.lower(),
                    "frames_sha256": frames_digest.lower(), "start_ms": start, "duration_ms": duration}


def _verify_original(source: Path, identity: dict) -> None:
    if not source.is_file() or sha256_path(source) != identity["source_video_sha256"]:
        raise SourceVideoError("原视频已改变，不能与现有关键点结果准确对照。")


def _validate_clip(path: Path, duration_ms: int) -> dict:
    from rallymate_service.motion_replay_worker import _validate_output
    from rallymate_service.motion_replay_provider import ProviderError
    try:
        metadata = _validate_output(path, duration_ms // 1000)
    except ProviderError:
        raise SourceVideoError("原视频片段未能完整导出。") from None
    if abs(metadata["duration_ms"] - duration_ms) > max(100, 1000 / metadata["fps"]):
        raise SourceVideoError("原视频片段长度不足，无法覆盖所选时间窗。")
    return metadata


def _export_clip(source: Path, target: Path, start_ms: int, duration_ms: int) -> dict:
    from rallymate_vision.pipeline import _find_ffmpeg_executable
    ffmpeg = _find_ffmpeg_executable()
    if not ffmpeg:
        raise SourceVideoError("原视频片段导出工具暂不可用。")
    # Transcoding enables ffmpeg's accurate seek: decode from the prior keyframe
    # and discard frames before the requested source timestamp. No AI or overlay.
    command = [ffmpeg, "-hide_banner", "-loglevel", "error", "-nostdin", "-y",
               "-ss", f"{start_ms / 1000:.3f}", "-i", str(source), "-t", f"{duration_ms / 1000:.3f}",
               "-map", "0:v:0", "-an", "-sn", "-dn", "-map_metadata", "-1",
               "-vf", "pad=ceil(iw/2)*2:ceil(ih/2)*2", "-c:v", "libx264", "-preset", "fast",
               "-crf", "20", "-pix_fmt", "yuv420p", "-fps_mode", "passthrough",
               "-movflags", "+faststart", str(target)]
    try:
        subprocess.run(command, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, timeout=120)
    except (OSError, subprocess.SubprocessError):
        raise SourceVideoError("原视频片段导出失败，请稍后重试。") from None
    return _validate_clip(target, duration_ms)


def source_video_path(row: dict, job: dict | None, settings: MotionReplaySettings) -> Path:
    """Validate provenance, then return an atomic cached source-window export.

    Only this media GET builds the cache. Status/list GETs and replay queue state
    remain read-only; old replay requests do not need migration or regeneration.
    """
    if not re.fullmatch(r"[a-f0-9]{32}", row["id"]):
        raise SourceVideoError("原视频对照任务无效。")
    source, identity = _source_identity(row, job, settings)
    with _LOCKS[int(row["id"][:2], 16) % len(_LOCKS)]:
        try:
            _verify_original(source, identity)
            base = settings.output_root.resolve()
            root = (base / row["id"]).resolve()
            cache = root / "source-window"
            if not root.is_relative_to(base) or not cache.resolve().is_relative_to(root):
                raise SourceVideoError("原视频缓存路径无效。")
            cache.mkdir(parents=True, exist_ok=True)
            target, manifest_path = cache / SOURCE_MEDIA_FILE, cache / "source.json"
            if any(path.is_symlink() or not path.resolve().is_relative_to(cache.resolve()) for path in (target, manifest_path)):
                raise SourceVideoError("原视频缓存路径无效。")
            if target.is_file() and manifest_path.is_file() and manifest_path.stat().st_size <= 16384:
                try:
                    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
                except (ValueError, UnicodeError):
                    manifest = {}
                if (manifest.get("source") == identity and target.stat().st_size > 0
                        and manifest.get("output_sha256") == sha256_path(target)):
                    return target
            temp = cache / ("pending-" + uuid.uuid4().hex)
            temp.mkdir()
            try:
                temporary_video = temp / SOURCE_MEDIA_FILE
                metadata = _export_clip(source, temporary_video, identity["start_ms"], identity["duration_ms"])
                _verify_original(source, identity)
                # Resolve again after the potentially long export before publishing.
                latest_source, latest_identity = _source_identity(row, job, settings)
                if latest_source != source or latest_identity != identity or cache.resolve().parent != root:
                    raise SourceVideoError("原视频来源在导出期间发生变化。")
                manifest = {"source": identity, "output_sha256": sha256_path(temporary_video),
                            "video": metadata, "audio_included": False, "playback_start_ms": 0}
                temporary_manifest = temp / "source.json"
                temporary_manifest.write_text(json.dumps(manifest, ensure_ascii=False), encoding="utf-8")
                os.replace(temporary_video, target)
                os.replace(temporary_manifest, manifest_path)
                return target
            finally:
                # This is a freshly created UUID directory strictly under the
                # verified cache; never remove arbitrary paths from a manifest.
                if temp.resolve().parent == cache.resolve() and not temp.is_symlink():
                    shutil.rmtree(temp)
        except SourceVideoError:
            raise
        except (OSError, ValueError, TypeError):
            raise SourceVideoError("原视频对照暂不可读取。") from None
