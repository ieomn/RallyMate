"""Read-only access to an uploaded original, including CPU-recovered reports.

Historical provenance paths remain untouched after a deployment migration. The
current database supplies the local location; the report supplies source identity
and the content digest. A path alone is never sufficient to expose a video.
"""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
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
        return verify_original_video(job, settings).public(job["id"])
    except OriginalVideoUnavailable:
        return None
