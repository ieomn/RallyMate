"""Read-only access to an uploaded original, including CPU-recovered reports.

Historical provenance paths remain untouched after a deployment migration. The
current database supplies the local location; the report supplies source identity
and the content digest. A path alone is never sufficient to expose a video.
"""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import math
from pathlib import Path, PurePosixPath
import re

from rallymate_service.config import ServiceSettings

_JOB_ID = re.compile(r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$")
_SHA = re.compile(r"^[0-9a-fA-F]{64}$")
_SUFFIXES = {".mp4", ".mov", ".m4v", ".avi", ".mkv"}


class OriginalVideoUnavailable(ValueError):
    """Public-safe error: no filesystem paths or raw metadata."""


def _require(condition: bool) -> None:
    if not condition:
        raise OriginalVideoUnavailable("原视频缺失或来源校验未通过，无法提供原片回放。")


def _object(value) -> dict:
    return value if isinstance(value, dict) else {}


def _portable_upload_name(value) -> str | None:
    if not isinstance(value, str) or not value:
        return None
    # Read the historical name without resolving a Linux path on Windows (or
    # vice versa). The digest below proves bytes; this only checks the binding.
    path = PurePosixPath(value.replace("\\", "/"))
    if ".." in path.parts or path.parent.name != "uploads":
        return None
    return path.name


@dataclass(frozen=True)
class VerifiedOriginalVideo:
    path: Path
    source_job_id: str
    sha256: str
    size: int

    def public(self, job_id: str) -> dict:
        return {
            # Browser playback always uses the authenticated same-origin proxy.
            "url": f"/v1/jobs/{job_id}/original-video",
            "sha256": self.sha256,
            "bytes": self.size,
            "source_job_id": self.source_job_id,
        }


def verify_original_video(job: dict, settings: ServiceSettings) -> VerifiedOriginalVideo:
    """Verify current bytes against existing evidence; never rewrite provenance."""
    try:
        job_id = job.get("id")
        _require(isinstance(job_id, str) and _JOB_ID.fullmatch(job_id) is not None)
        _require(job.get("status") == "succeeded" and not job.get("cancel_requested"))
        summary = _object(job.get("summary"))
        _require(summary.get("job_id") == job_id)
        provenance = _object(_object(summary.get("minimum_scoring_loop")).get("provenance"))
        source_id = provenance.get("pipeline_job_id")
        digest = provenance.get("video_sha256")
        _require(isinstance(source_id, str) and _JOB_ID.fullmatch(source_id) is not None)
        _require(isinstance(digest, str) and _SHA.fullmatch(digest) is not None)
        digest = digest.lower()
        recovery = _object(summary.get("recovery"))
        if source_id != job_id:
            # A recovered display report refers to the original inference job,
            # not a renamed upload. All independent recovery bindings must agree.
            _require(summary.get("source_failed_job_id") == source_id
                     and recovery.get("source_failed_job_id") == source_id
                     and recovery.get("recovery_job_id") == job_id
                     and recovery.get("mode") == "cpu_finalization"
                     and recovery.get("status") == "completed"
                     and recovery.get("reused_inference") is True
                     and recovery.get("inference_identity_preserved") is True
                     and str(recovery.get("video_sha256", "")).lower() == digest)
        else:
            _require(not summary.get("source_failed_job_id") and not recovery)

        base = settings.data_root.resolve()
        uploads = settings.uploads_dir.resolve()
        raw_path = Path(job["video_path"])
        path = raw_path.resolve(strict=True)
        _require(uploads.is_relative_to(base) and path.parent == uploads
                 and not raw_path.is_symlink() and path.is_file()
                 and path.stem == source_id and path.suffix.lower() in _SUFFIXES)
        source_input = _object(summary.get("input"))
        _require(_portable_upload_name(provenance.get("video_path")) == path.name
                 and _portable_upload_name(source_input.get("video_path")) == path.name)
        before = path.stat()
        _require(0 < before.st_size <= settings.max_upload_bytes)
        upload_bytes = _object(source_input.get("upstream_metadata")).get("upload_bytes")
        if upload_bytes is not None:
            _require(type(upload_bytes) is int and upload_bytes == before.st_size)
        content = hashlib.sha256()
        with path.open("rb") as handle:
            for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                content.update(chunk)
        after = path.stat()
        _require((before.st_size, before.st_mtime_ns, before.st_ctime_ns, before.st_ino)
                 == (after.st_size, after.st_mtime_ns, after.st_ctime_ns, after.st_ino)
                 and content.hexdigest() == digest)
        return VerifiedOriginalVideo(path, source_id, digest, before.st_size)
    except (OSError, KeyError, TypeError, ValueError) as exc:
        if isinstance(exc, OriginalVideoUnavailable):
            raise
        raise OriginalVideoUnavailable("原视频缺失或来源校验未通过，无法提供原片回放。") from None


def original_video_descriptor(job: dict, settings: ServiceSettings) -> dict | None:
    try:
        original = verify_original_video(job, settings)
        result = original.public(job["id"])
        binding = original_pose_time_binding(job, settings, original)
        if binding is not None:
            result["pose_time_binding"] = binding
        return result
    except OriginalVideoUnavailable:
        return None


def original_pose_time_binding(job: dict, settings: ServiceSettings, original: VerifiedOriginalVideo) -> dict | None:
    """Bind original playback to retained decoder PTS, never average FPS.

    SourceVideoClock subtracts the first decoded frame's PTS even when analysis
    starts later. Validate that declared origin against the hashed source-frame
    artifact. Legacy reports without this evidence retain file-only playback.
    """
    summary = _object(job.get("summary"))
    processing = _object(summary.get("processing"))
    timing = _object(processing.get("source_timing"))
    origin = timing.get("source_origin_ms")
    count = processing.get("processed_frames")
    if (timing.get("method") != "decoder_presentation_timestamps" or timing.get("timing_verified") is not True
            or type(timing.get("fallback_timestamp_frames")) is not int or timing["fallback_timestamp_frames"] != 0
            or type(count) is not int or count < 1
            or type(timing.get("decoder_timestamp_frames")) is not int or timing["decoder_timestamp_frames"] < max(count, 2)
            or type(origin) not in (int, float) or not math.isfinite(origin) or origin < 0):
        return None
    provenance = _object(_object(summary.get("minimum_scoring_loop")).get("provenance"))
    frames_sha = provenance.get("frames_sha256")
    if not isinstance(frames_sha, str) or _SHA.fullmatch(frames_sha) is None:
        return None
    try:
        output = Path(job["output_dir"]).resolve(strict=True)
        expected = (settings.runs_dir / job["id"]).resolve(strict=True)
        path = output / "frames.jsonl"
        if output != expected or path.is_symlink() or path.resolve().parent != output:
            return None
        before = path.stat()
        if not 0 < before.st_size <= 1024 * 1024 * 1024:
            return None
        digest = hashlib.sha256()
        with path.open("rb") as stream:
            first = stream.readline(1024 * 1024 + 1)
            if len(first) > 1024 * 1024 or not first.endswith(b"\n"):
                return None
            digest.update(first)
            for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                digest.update(chunk)
        after = path.stat()
        if ((before.st_size, before.st_mtime_ns, before.st_ctime_ns, before.st_ino)
                != (after.st_size, after.st_mtime_ns, after.st_ctime_ns, after.st_ino)
                or digest.hexdigest() != frames_sha.lower()):
            return None
        record = json.loads(first)
        frame = _object(_object(record).get("frame"))
        if (_object(record).get("job_id") != original.source_job_id
                or frame.get("timestamp_source") != "decoder_pts"
                or str(frame.get("video_sha256", "")).lower() != original.sha256
                or any(type(frame.get(key)) is not int or frame[key] < minimum for key, minimum in (
                    ("index", 0), ("processed_index", 0), ("pts", 0), ("time_base_num", 1),
                    ("time_base_den", 1), ("source_timestamp_ms", 0), ("timestamp_ms", 0)))
                or frame["processed_index"] != 0 or frame["index"] != processing.get("source_start_frame")):
            return None
        pts_ms = frame["pts"] * frame["time_base_num"] / frame["time_base_den"] * 1000
        if (not math.isfinite(pts_ms) or abs(pts_ms - frame["source_timestamp_ms"]) > .51
                or round(pts_ms - origin) != frame["timestamp_ms"]):
            return None
        return {"version": "original-video-decoder-pts-v1", "job_id": job["id"],
                "source_job_id": original.source_job_id, "video_sha256": original.sha256,
                "frames_sha256": frames_sha.lower(), "timestamp_semantics": "source_relative_decoder_pts",
                "source_pts_origin_ms": origin, "media_time_origin_ms": 0,
                "anchor": {key: frame[key] for key in ("index", "processed_index", "pts", "time_base_num",
                                                       "time_base_den", "source_timestamp_ms", "timestamp_ms")}}
    except (OSError, KeyError, ValueError, TypeError, OverflowError):
        return None
