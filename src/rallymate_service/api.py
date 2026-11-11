from __future__ import annotations

import asyncio
import errno
import hashlib
import json
import os
import re
import secrets
import stat
import sys
import time
import uuid
from collections import OrderedDict
from contextlib import asynccontextmanager, ExitStack
from importlib.resources import files
from pathlib import Path
from threading import Lock, Event, BoundedSemaphore, RLock
from typing import Any, Callable

from fastapi import (
    Depends,
    FastAPI,
    File,
    Form,
    Header,
    HTTPException,
    Query,
    Request,
    Response,
    UploadFile,
)
from starlette.concurrency import run_in_threadpool
from rallymate_service.uploads import CHUNK_BYTES, UploadStore
from rallymate_vision.postprocess_resources import (
    PostprocessBudget, PostprocessOutputLimit, PostprocessResourceLimit, encode_bounded_json,
)
from rallymate_scoring.artifact_evidence import POOL_FILENAME, ArtifactEvidenceError, verify_evidence_manifest
from fastapi.responses import FileResponse, JSONResponse
from fastapi.middleware.cors import CORSMiddleware

from rallymate_scoring.granularity import static_model_capability
from rallymate_scoring.stroke_candidates import DETECTOR_VERSION, recognize_strokes_from_artifacts
from rallymate_scoring.stroke_analysis import ANALYSIS_VERSION
from rallymate_scoring.registry_lifecycle import RegistryLifecycleError
from rallymate_scoring.technique_registry import (
    TechniqueRegistryError,
    technique_catalog,
)
from rallymate_scoring.technique_assessment import (
    TechniqueRegistrySnapshotError,
    build_technique_assessment,
)
from rallymate_service.config import ServiceConfigError, ServiceSettings, load_settings
from rallymate_service.database import JobDatabase, QueueCapacityError
from rallymate_service.segmented_report import (
    SegmentedReportStore, SegmentedReportError, build_summary as build_segmented_summary,
    MAX_SUMMARY_BYTES, MAX_DETAIL_BYTES, MAX_OVERVIEW_BYTES,
)
from rallymate_service.segmented_delivery import SegmentedDelivery
from rallymate_service.live_trajectory import LiveTrajectoryCache
from rallymate_service.shot_statistics import build_shot_statistics, validate_review
from rallymate_service.shot_review_store import (
    ShotReviewStore, ShotReviewError, MAX_SHOT_REVIEW_BYTES, parse_shot_review,
)
from rallymate_service.original_video import (
    OriginalVideoUnavailable, original_video_descriptor, verify_original_video,
)
from rallymate_service.original_video_response import OriginalVideoResponse
from rallymate_service.mesh_preview import MESH_ARTIFACTS, MESH_VIDEOS, mesh_preview_descriptor, mesh_preview_status
from rallymate_service.deployment_identity import load_deployment_identity
from rallymate_service.pose_request_diagnostics import (
    PoseRequestDiagnosticsMiddleware, bind_pose_request_diagnostics,
    pose_diagnostic_stage, pose_request_diagnostics_enabled, record_pose_phase,
)
from rallymate_service.technical_review import (
    MAX_BODY_BYTES,
    TechnicalReviewError,
    TechnicalReviewStore,
    parse_review_input,
)
from rallymate_service.footwork_review import load_footwork_review
from rallymate_service.analysis_report import build_analysis_report
from rallymate_service.process_evaluation import build_process_evaluation
from rallymate_service.swing_kinematics import build_swing_kinematics
from rallymate_service.tennis_assessment import build_tennis_assessment
from rallymate_scoring.indicator_assessment import build_indicator_assessment
from rallymate_service.card_execution import VERSION as CARD_EXECUTION_VERSION, build_card_execution_report
from rallymate_scoring.card_rule_catalog import VERSION as CARD_RULE_VERSION
from rallymate_service.report_compatibility import (
    DEFAULT_REPORT_CONTRACT,
    ReportContract,
    project_report,
)
from rallymate_service.source_assessment import load_source_aligned_assessment
from rallymate_service.foot_crossing_candidates import load_foot_crossing_candidates
from rallymate_service.report_evidence_transport import (
    ReportTransport, ReportEvidenceTransportError, ReportEvidenceLimitError, pack_report_evidence,
)
from rallymate_service.report_delivery_cache import (
    REPORT_PROCESS_VERSION, DeliveryError, ReportDelivery, ReportDeliveryCache,
    artifact_signature,
)
from rallymate_service.report_delivery_store import report_builder_fingerprint, persistent_report_key
from rallymate_service.advice_context import AdviceTechnique, AdviceContextTooLarge, project_advice_context
from rallymate_service.user_demo import (
    UserDemoResultError,
    build_user_demo_result,
    load_indicator_feature_records, open_indicator_feature_records,
)
from rallymate_vision.quality import probe_video
from rallymate_vision.pose_playback import PosePlaybackError, PosePlaybackTransport, build_pose_playback, pose_playback_transport
from rallymate_vision.trajectory import (
    TrajectoryExtractionError,
    build_trajectory_preview,
)
from rallymate_vision.pipeline import (
    load_pipeline_calibrations,
    load_trusted_runtime_binding_registry,
)


ALLOWED_EXTENSIONS = {".mp4", ".mov", ".m4v", ".avi", ".mkv"}
API_VERSION = "1.3.0"
API_COMPATIBILITY_VERSION = "1.2.0"
ALLOWED_ARTIFACTS = {
    "summary.json",
    "frames.jsonl",
    "annotated.mp4",
    "preview.jpg",
    "scoring-readiness.json",
    "analysis-report.html",
    "primary-player.jsonl",
    "primary-player-summary.json",
    "events.jsonl",
    "features.jsonl",
    "scoring-evidence.sqlite3",
    "indicator-features.jsonl",
    "scores.jsonl",
    "event-feature-errors.json",
    "scoring-loop-summary.json",
    "scoring-loop-report.html",
    "calculation-readiness.json",
    "indicator-measurement-portfolio.json",
    "scoring-cycle-measurement.json",
    "source-aligned-measurements.json",
} | MESH_ARTIFACTS
INLINE_PREVIEW_ARTIFACTS = {
    "annotated.mp4",
    "analysis-report.html",
    "scoring-loop-report.html",
} | MESH_VIDEOS
_REQUEST_ID_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")
_PATH_FIELD_PATTERN = re.compile(
    r"(?:^|_)(?:path|directory|dir|file)$|(?:_path)$", re.IGNORECASE
)
# Paths are often rendered with spaces (for example ``C:\\Program Files\\...``)
# or wrapped in quotes by ``Path.__str__``.  Stop at punctuation that normally
# separates an error clause, not at whitespace, so the tail of a local path
# cannot leak into a public response.
_PATH_TOKEN_PATTERN = re.compile(
    # Keep brackets and parentheses in the match: they are legal path
    # characters on both Windows and POSIX filesystems.  Semicolon/comma and
    # quotes remain conservative clause delimiters, so a path with spaces is
    # still removed as one token without swallowing a whole JSON response.
    r'''(?<![A-Za-z0-9])(?:[A-Za-z]:[\\/]|/|\\\\)[^,;"']*(?=$|[,;"'])'''
)


def _redact_public_value(value, key: str | None = None):
    """Remove worker-local paths and client network identifiers from public JSON.

    Local development keeps the historical full summary for compatibility.  A
    production/public-base-url response calls this function so a browser never
    receives `/app/...`, Windows drive paths, or the uploader's source IP.
    """

    if key is not None:
        lowered = key.casefold()
        if lowered in {"source_ip", "client_ip", "remote_addr"}:
            return None
        if _PATH_FIELD_PATTERN.search(key):
            return "<redacted>"
    if isinstance(value, dict):
        return {
            str(item_key): _redact_public_value(item_value, str(item_key))
            for item_key, item_value in value.items()
        }
    if isinstance(value, list):
        return [_redact_public_value(item) for item in value]
    if isinstance(value, str):
        # Also redact paths embedded in human-readable error strings nested in
        # lists or metadata objects; checking only a whole-string absolute path
        # leaves ``failed: C:\\worker\\...`` exposed.
        return _redact_readiness_reason(value)
    return value


def _public_summary(job: dict, *, redact: bool) -> dict | None:
    summary = job.get("summary")
    if not redact or summary is None:
        return summary
    return _redact_public_value(summary)


def _should_redact_public(settings: ServiceSettings) -> bool:
    return bool(settings.public_base_url) or settings.environment.strip().lower() == "production"


def _redact_readiness_reason(reason: str) -> str:
    """Keep readiness diagnostics useful without exposing worker topology."""

    return _PATH_TOKEN_PATTERN.sub("<redacted>", str(reason))


def _public_error_detail(detail, settings: ServiceSettings):
    """Redact local paths embedded in errors returned to a public client.

    Development callers retain the historical diagnostics.  Production or
    explicitly public deployments get the same actionable wording with worker
    filesystem paths removed, including paths embedded inside a JSON error
    object rather than only when the whole value is a path.
    """

    if not _should_redact_public(settings):
        return detail
    if isinstance(detail, str):
        return _redact_readiness_reason(detail)
    if isinstance(detail, dict):
        redacted = {}
        for key, value in detail.items():
            # Route every value through the key-aware walker.  In particular,
            # nested ``source_ip``/``client_ip`` fields must be removed even
            # when their value is a string (the old string fast path only
            # redacted filesystem tokens).
            redacted[key] = _redact_public_value(value, str(key))
        return redacted
    if isinstance(detail, list):
        return _redact_public_value(detail)
    if isinstance(detail, tuple):
        return [_redact_public_value(item) for item in detail]
    return _redact_public_value(detail)


_MIN_PRODUCTION_API_KEY_LENGTH = 32
_PLACEHOLDER_API_KEY_MARKERS = (
    "replace-with",
    "replace_me",
    "replace-me",
    "your-api",
    "your_api",
    "changeme",
    "change-me",
    "placeholder",
    "example-token",
    "example_token",
    "test-token",
    "test_token",
    "dummy-token",
    "dummy_token",
    "change_me",
    "sample-token",
    "sample_token",
    "dummy",
)


def _production_api_key_reasons(api_key: str | None) -> list[str]:
    """Return actionable readiness reasons for an unsafe production key.

    This check intentionally lives in the readiness endpoint rather than
    ``create_app``/``ServiceSettings.validate_license``.  Existing callers can
    still construct a test app with a short fixture token, while production
    deployment remains fail-closed and explains exactly what must change.
    """

    if not isinstance(api_key, str) or not api_key.strip():
        return ["RALLYMATE_API_KEY must be configured before production readiness"]
    normalized = re.sub(r"[^a-z0-9]+", "-", api_key.strip().casefold()).strip("-")
    normalized_markers = {
        re.sub(r"[^a-z0-9]+", "-", marker.casefold()).strip("-")
        for marker in _PLACEHOLDER_API_KEY_MARKERS
    }
    if any(marker in normalized for marker in normalized_markers) or "example" in normalized:
        return [
            "RALLYMATE_API_KEY appears to be a placeholder; use a generated secret "
            "before production readiness"
        ]
    if len(api_key.strip()) < _MIN_PRODUCTION_API_KEY_LENGTH:
        return [
            "RALLYMATE_API_KEY must be at least "
            f"{_MIN_PRODUCTION_API_KEY_LENGTH} characters before production readiness"
        ]
    return []


def _public_url(path: str, public_base_url: str | None = None) -> str:
    if not public_base_url:
        return path
    return f"{public_base_url.rstrip('/')}{path}"


def _safe_request_id(raw: str | None) -> str:
    """Keep trace IDs bounded and header-safe before echoing them to clients."""

    if raw is not None and len(raw) <= 128 and _REQUEST_ID_PATTERN.fullmatch(raw):
        return raw
    return uuid.uuid4().hex


def _public_original_filename(job: dict, *, redact: bool):
    """Project an upload label without loading or inspecting its report."""
    original = job.get("original_filename")
    if not redact:
        return original
    # Uploaded filenames can contain a person's name or local directory hints.
    # Keep the same stable opaque label in lightweight status and full job APIs.
    original = str(original or "")
    suffix = Path(original).suffix.lower() if original else ""
    return f"video-{str(job.get('id', 'job'))[:8]}{suffix}"


def _public_job(
    job: dict,
    public_base_url: str | None = None,
    *,
    redact_summary: bool = False,
    report_contract: ReportContract = DEFAULT_REPORT_CONTRACT,
) -> dict:
    public = {
        key: job.get(key)
        for key in (
            "id",
            "status",
            "original_filename",
            "created_at",
            "updated_at",
            "started_at",
            "completed_at",
            "cancel_requested",
            "cancel_requested_at",
            "cancelled_at",
            "attempts",
            "progress",
            "error",
            "summary",
        )
    }
    if redact_summary:
        public["original_filename"] = _public_original_filename(job, redact=True)
        public["summary"] = _public_summary(job, redact=True)
        if isinstance(public.get("error"), str):
            public["error"] = _redact_readiness_reason(public["error"])
    if job.get("status") == "succeeded":
        summary = job.get("summary") or {}
        public["scoring_state"] = summary.get("scoring_state")
        mesh_preview = mesh_preview_descriptor(job, public_base_url)
        public["sam3d_mesh"] = mesh_preview
        public["sam3d_mesh_status"] = mesh_preview_status(job, mesh_preview)
        public["artifact_urls"] = {
            name: _public_url(f"/v1/jobs/{job['id']}/artifacts/{name}", public_base_url)
            for name in sorted(ALLOWED_ARTIFACTS)
            if (Path(job["output_dir"]) / name).exists()
            and (name not in MESH_ARTIFACTS or mesh_preview is not None)
        }
        public["demo_result_url"] = _public_url(
            f"/v1/jobs/{job['id']}/demo-result", public_base_url
        )
        # This endpoint is derived on demand from the immutable frames artifact;
        # it is intentionally separate from formal scoring artifacts.
        public["trajectory_url"] = _public_url(
            f"/v1/jobs/{job['id']}/trajectory", public_base_url
        )
        public["technique_assessment_url"] = (
            _public_url(
                f"/v1/jobs/{job['id']}/technique-assessment", public_base_url
            )
        )
    else:
        public["artifact_urls"] = {}
        public["demo_result_url"] = None
        public["trajectory_url"] = (
            _public_url(f"/v1/jobs/{job['id']}/trajectory", public_base_url)
            if job.get("status") == "running" else None
        )
        public["technique_assessment_url"] = None
    public["pose_preview_url"] = (
        _public_url(f"/v1/jobs/{job['id']}/pose-preview", public_base_url)
        if job.get("status") in {"running", "succeeded"} else None
    )
    return project_report(public, report_contract)


def _safe_filename(value: str | None) -> str:
    original = Path(value or "upload.mp4").name
    stem = re.sub(r"[^A-Za-z0-9._-]+", "-", Path(original).stem).strip("-")
    suffix = Path(original).suffix.lower()
    return f"{stem or 'upload'}{suffix}"


def _owned_upload_source(
    source: Path, uploads_dir: Path, upload_id: str, expected_size: int, max_bytes: int,
) -> tuple[Path, os.stat_result]:
    """Validate the one temporary file that the upload store may hand off.

    This is an internal path capability, never a path accepted by an HTTP
    endpoint. The store holds the session lock and has verified every chunk.
    A same-filesystem rename preserves those bytes without another full copy.
    """
    try:
        if str(uuid.UUID(upload_id)) != upload_id:
            raise ValueError("invalid upload identity")
        root = uploads_dir.resolve(strict=True)
        expected = root / ".chunks" / upload_id / "assembled.video"
        source = Path(source)
        if not source.is_absolute() or source.resolve(strict=True) != expected:
            raise ValueError("source is not the session's assembled video")
        # A configured uploads root may itself be reached through a junction;
        # resolve it once, then reject links within the owned session subtree.
        for item in (expected.parent.parent, expected.parent, expected):
            item_stat = item.lstat()
            if (stat.S_ISLNK(item_stat.st_mode)
                    or getattr(item_stat, "st_file_attributes", 0)
                    & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0)):
                raise ValueError("linked upload paths are not owned captures")
        source_stat = expected.stat()
        if not stat.S_ISREG(source_stat.st_mode) or source_stat.st_nlink != 1:
            raise ValueError("upload source must be an exclusively owned regular file")
        if source_stat.st_dev != root.stat().st_dev:
            raise ValueError("upload handoff requires the same filesystem")
    except (OSError, ValueError, TypeError) as exc:
        raise HTTPException(409, "上传文件移交校验失败，请重试完成上传") from exc
    if type(expected_size) is not int or expected_size != source_stat.st_size:
        raise HTTPException(422, "视频合并长度不正确")
    if source_stat.st_size == 0:
        raise HTTPException(422, "uploaded video is empty")
    if source_stat.st_size > max_bytes:
        raise HTTPException(413, "video exceeds the configured size limit")
    return expected, source_stat


def _submission_file_identity(metadata: os.stat_result) -> tuple:
    return (metadata.st_dev, metadata.st_ino, metadata.st_size, metadata.st_mtime_ns)


def _sync_submission_directory(directory: Path) -> None:
    if os.name == "nt":
        return
    descriptor = os.open(directory, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _rename_submission_file(source: Path, target: Path) -> None:
    """Publish without replacing an existing name on Windows or Linux.

    Path.rename overwrites on POSIX. Linux's explicit NOREPLACE operation also
    avoids the crash window where a link/unlink fallback leaves two writable
    names for the upload store's next assembly.
    """
    if os.name == "nt":
        source.rename(target)
        return
    if not sys.platform.startswith("linux"):
        raise OSError(errno.ENOTSUP, "Atomic no-replace upload publication is unsupported")
    import ctypes
    libc = ctypes.CDLL(None, use_errno=True)
    rename = getattr(libc, "renameat2", None)
    if rename is None:
        raise OSError(errno.ENOTSUP, "Linux renameat2 is required for upload publication")
    rename.argtypes = [ctypes.c_int, ctypes.c_char_p, ctypes.c_int, ctypes.c_char_p, ctypes.c_uint]
    rename.restype = ctypes.c_int
    if rename(-100, os.fsencode(source), -100, os.fsencode(target), 1) != 0:
        code = ctypes.get_errno()
        raise OSError(code, os.strerror(code), str(target))


def _regular_submission_file(path: Path) -> os.stat_result:
    metadata = path.lstat()
    if (not stat.S_ISREG(metadata.st_mode) or metadata.st_nlink != 1
            or getattr(metadata, "st_file_attributes", 0)
            & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0)):
        raise HTTPException(409, "上传目标文件校验失败")
    return metadata


def _verify_recovered_video(source: Path, target: Path, source_stat: os.stat_result) -> None:
    """Reuse a queue-uncertain orphan only when all of its bytes still match."""
    target_stat = _regular_submission_file(target)
    if (target_stat.st_dev, target_stat.st_size) != (source_stat.st_dev, source_stat.st_size):
        raise HTTPException(409, "上传目标已存在其他文件")
    digests = []
    for path in (source, target):
        digest = hashlib.sha256()
        with path.open("rb") as handle:
            while block := handle.read(1024 * 1024):
                digest.update(block)
        digests.append(digest.digest())
    if (digests[0] != digests[1]
            or _submission_file_identity(source.stat()) != _submission_file_identity(source_stat)
            or _submission_file_identity(_regular_submission_file(target)) != _submission_file_identity(target_stat)):
        raise HTTPException(409, "上传目标已存在其他文件")


def _publish_owned_request(path: Path, payload: dict) -> bool:
    """Return whether this attempt created the request; never replace a file."""
    def comparable(value):
        if not isinstance(value, dict) or not isinstance(value.get("upstream_metadata"), dict):
            raise ValueError("invalid saved request")
        metadata = dict(value["upstream_metadata"])
        # Retries may arrive through a different connection/request identifier.
        metadata.pop("source_ip", None)
        metadata.pop("request_id", None)
        return {**value, "upstream_metadata": metadata}

    def check_existing():
        metadata = _regular_submission_file(path)
        try:
            if metadata.st_size > 256 * 1024:
                raise ValueError("saved request is too large")
            saved = json.loads(path.read_text("utf-8"))
            if (comparable(saved) != comparable(payload)
                    or _submission_file_identity(_regular_submission_file(path)) != _submission_file_identity(metadata)):
                raise ValueError("saved request differs")
        except (OSError, UnicodeError, ValueError) as exc:
            raise HTTPException(409, "上传任务配置已存在且不一致") from exc

    if path.exists() or path.is_symlink():
        check_existing()
        return False
    temporary = path.with_name(f".{path.name}.{uuid.uuid4()}.tmp")
    try:
        with temporary.open("x", encoding="utf-8") as handle:
            json.dump(payload, handle, ensure_ascii=False, indent=2)
            handle.flush()
            os.fsync(handle.fileno())
        try:
            _rename_submission_file(temporary, path)
        except FileExistsError:
            check_existing()
            return False
        return True
    finally:
        temporary.unlink(missing_ok=True)


def _parse_polygon(raw: str | None) -> list[list[float]] | None:
    if raw is None or not raw.strip():
        return None
    try:
        polygon = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise HTTPException(422, "manual_polygon_normalized is invalid JSON") from exc
    if (
        not isinstance(polygon, list)
        or len(polygon) != 4
        or any(
            not isinstance(point, list)
            or len(point) != 2
            or any(
                not isinstance(value, (int, float)) or not 0 <= value <= 1
                for value in point
            )
            for point in polygon
        )
    ):
        raise HTTPException(
            422,
            "manual_polygon_normalized must be four [x,y] points in the 0..1 range",
        )
    return polygon


def create_app(
    settings: ServiceSettings | None = None,
    database: JobDatabase | None = None,
    *,
    executor_status: Callable[[], dict] | None = None,
    executor_shutdown: Callable[[], dict] | None = None,
) -> FastAPI:
    service_settings = settings or load_settings()
    # ``create_app`` is also a public factory used by tests, worker wrappers
    # and deployment entrypoints.  Validate injected settings here so an
    # operator cannot accidentally bypass CORS/public-URL/technique-registry
    # checks by constructing the app directly.  Scoring registry authority is
    # intentionally deferred to readiness to preserve its actionable
    # ``not_ready`` diagnostic for unapproved historical overrides.
    service_settings.validate_license(check_registry_authority=False)
    service_settings.ensure_directories()
    deployment_identity = load_deployment_identity(Path(__file__).resolve().parents[2])
    db = database or JobDatabase(service_settings.database_path)
    admission_lock = RLock()
    maintenance = Event()

    def execution_status() -> dict:
        # A report-only API can use an independently managed worker. In that
        # mode readiness must not pretend that an unobserved executor is alive.
        if executor_status is None:
            return {"status": "unmonitored", "available": False, "heartbeat_at": None}
        try:
            snapshot = executor_status()
            state = snapshot.get("status")
            if state not in {"starting", "ready", "busy", "restarting", "unavailable", "stopped", "unmonitored"}:
                raise ValueError("invalid executor status")
            heartbeat = snapshot.get("heartbeat_at")
            if heartbeat is not None:
                from datetime import datetime
                datetime.fromisoformat(heartbeat.replace("Z", "+00:00"))
            # Never expose worker ids, hostnames, child exceptions or paths.
            result = {"status": state,
                      "available": snapshot.get("available") is True and state in {"ready", "busy"},
                      "heartbeat_at": heartbeat}
            kind = snapshot.get("work_kind")
            if kind in {"analysis", "mesh", "idle", "loading"}:
                result["work_kind"] = kind
            return result
        except Exception:
            return {"status": "unavailable", "available": False, "heartbeat_at": None}

    def execution_admits(snapshot: dict) -> bool:
        # Preserve externally managed/API-only deployments without claiming
        # their executor was verified. The supervised local service fails closed.
        return snapshot["status"] == "unmonitored" or snapshot["available"]
    technical_reviews = TechnicalReviewStore(service_settings.data_root / "technical-reviews.sqlite3")
    shot_reviews = ShotReviewStore(service_settings.data_root / "shot-reviews.sqlite3")
    shot_statistics_cache: OrderedDict[tuple, dict] = OrderedDict()
    shot_statistics_lock = Lock()
    # Cache bounded previews only while the underlying frame snapshot matches.
    # A progressing job or a transition to succeeded always invalidates it.
    trajectory_cache: OrderedDict[tuple, dict] = OrderedDict()
    trajectory_cache_lock = Lock()
    live_trajectory_cache = LiveTrajectoryCache()
    pose_cache: OrderedDict[tuple, dict] = OrderedDict()
    pose_cache_lock = Lock()
    action_cache: OrderedDict[tuple, dict] = OrderedDict()
    action_cache_lock = Lock()
    report_builder_version = report_builder_fingerprint(Path(__file__).resolve().parents[1])
    report_process_at_start = REPORT_PROCESS_VERSION
    report_delivery_cache = ReportDeliveryCache(storage_dir=service_settings.data_root / "report-delivery-cache")
    segmented_reports = SegmentedReportStore()
    segmented_summary_slots = BoundedSemaphore(4)

    def packaged_report_rules_identity() -> tuple:
        resources = sorted((item for item in files("rallymate_scoring").joinpath("data").iterdir()
                            if item.is_file()), key=lambda item: item.name)
        return tuple((str(item), hashlib.sha256(item.read_bytes()).hexdigest()) for item in resources)

    packaged_rules_at_start = packaged_report_rules_identity()

    def current_packaged_rules_identity() -> tuple:
        current = packaged_report_rules_identity()
        if current != packaged_rules_at_start:
            # Some scoring catalogues deliberately use process-local no-arg
            # caches. Rebuilding a report after a live package edit could mix
            # versions; fail closed until an ordinary service restart reloads
            # all catalogues together. External registries remain dynamic.
            raise DeliveryError(503, "report_rules_changed_restart_required",
                                "Packaged report rules changed; restart the service before preparing reports.")
        return current

    def segmented_rules_identity():
        registry = service_settings.resolved_technique_registry
        return (current_packaged_rules_identity(),
                artifact_signature(Path(registry), content_hash=True) if registry is not None else None,
                _should_redact_public(service_settings), service_settings.public_base_url)

    segmented_delivery = SegmentedDelivery(service_settings, db, segmented_rules_identity)

    def report_source_key(job: dict, contract: ReportContract, transport: ReportTransport) -> tuple:
        # Numerical report media links are fixed to its own artifact snapshot.
        # Background mesh files live behind the independent /mesh-preview
        # resource and must not invalidate a report while it is being built.
        # Include every other public artifact, even missing ones, so newly
        # published evidence still invalidates a previous response.
        # Stat-only identities cannot detect same-size rewrites with restored
        # mtime (Windows ctime is creation time). Hash every body dependency.
        body_dependencies = {"summary.json", "frames.jsonl", "primary-player.jsonl", "events.jsonl",
                             "indicator-features.jsonl", "scoring-evidence.sqlite3", "source-aligned-measurements.json", "sam3d_frames.jsonl", "action-context.json", "tennis-subject-selection.json"}
        artifacts = tuple(artifact_signature(Path(job["output_dir"]) / name,
                                            content_hash=name in body_dependencies)
                          for name in sorted((ALLOWED_ARTIFACTS - MESH_ARTIFACTS) | {"scoring-evidence.sqlite3", "sam3d_frames.jsonl", "action-context.json", "tennis-subject-selection.json"}))
        registry = service_settings.resolved_technique_registry
        # These small resources are content-addressed, including an external
        # registry whose operator might preserve file size/modification time.
        registry_identity = list(current_packaged_rules_identity())
        resources = []
        if registry is not None:
            resources.append(registry)
        for resource in resources:
            try:
                digest = hashlib.sha256(resource.read_bytes()).hexdigest()
            except FileNotFoundError:
                digest = None
            registry_identity.append((str(resource), digest))
        summary_digest = hashlib.sha256(json.dumps(job.get("summary"), ensure_ascii=False,
            sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")).hexdigest()
        return (REPORT_PROCESS_VERSION, API_VERSION, DETECTOR_VERSION, ANALYSIS_VERSION,
                job["id"], str(Path(job["output_dir"]).resolve()), job["status"],
                bool(job.get("cancel_requested")), job.get("updated_at"), summary_digest,
                artifacts, tuple(registry_identity), contract, transport,
                json.dumps(original_video_descriptor(job, service_settings), sort_keys=True),
                service_settings.public_base_url, _should_redact_public(service_settings))

    def summary_with_actions(job: dict) -> dict:
        summary = dict(job.get("summary") or {})
        recognition = summary.get("action_recognition") or {}
        motion = recognition.get("motion_analysis") or {}
        if job["status"] != "succeeded" or (recognition.get("detector_version") == DETECTOR_VERSION
                                            and motion.get("analysis_version") == ANALYSIS_VERSION):
            return summary
        paths = [Path(job["output_dir"]) / name for name in ("frames.jsonl", "primary-player.jsonl")]
        key = (DETECTOR_VERSION, ANALYSIS_VERSION,
               *(artifact_signature(path, content_hash=True) for path in paths))
        with action_cache_lock:
            if key not in action_cache:
                action_cache[key] = recognize_strokes_from_artifacts(*paths)
                while len(action_cache) > 12:
                    action_cache.popitem(last=False)
            action_cache.move_to_end(key)
            summary["action_recognition"] = action_cache[key]
        return summary

    @asynccontextmanager
    async def lifespan(_: FastAPI):
        db.initialize()
        technical_reviews.initialize()
        # Generated mannequin replay is retired from the product. Do not
        # instantiate its runtime: legacy flags must not restart paid work.
        try:
            yield
        finally:
            live_trajectory_cache.clear()
            await run_in_threadpool(segmented_delivery.close)
            await run_in_threadpool(report_delivery_cache.close)

    app = FastAPI(
        title="RallyMate Vision API",
        # Keep the OpenAPI info version stable for existing 1.2 clients.  The
        # richer surface advertises API_VERSION through /v1/meta and response
        # payloads while compatibility_version remains explicit there.
        version=API_COMPATIBILITY_VERSION,
        description=(
            "Upload tennis video, run persistent-GPU YOLO Detect + replaceable Pose inference, "
            "and expose a versioned 24-technique qualitative catalog, bounded ball-trajectory "
            "and racket observability previews, and evidence-gated technique assessments. "
            "Formal coach-calibrated scoring remains disabled until its calibration boundary "
            "is explicitly authorized."
        ),
        lifespan=lifespan,
    )
    app.state.settings = service_settings
    app.state.database = db
    app.state.technical_reviews = technical_reviews
    app.state.report_delivery_cache = report_delivery_cache
    app.state.segmented_delivery = segmented_delivery

    @app.exception_handler(QueueCapacityError)
    async def queue_capacity_handler(request: Request, exc: QueueCapacityError):
        return JSONResponse(status_code=429, content={"detail": {"code": "analysis_queue_full",
            "message": "分析队列已满，请稍后重试。", "max_active_jobs": exc.limit}},
            headers={"Retry-After": "30", "Cache-Control": "no-store"})

    @app.middleware("http")
    async def request_id_middleware(request: Request, call_next):
        request_id = _safe_request_id(request.headers.get("X-Request-ID"))
        request.state.request_id = request_id
        bind_pose_request_diagnostics(request)
        response = await call_next(request)
        response.headers["X-Request-ID"] = request_id
        return response

    # The browser client can be hosted on a separate domain (or behind a CDN)
    # while the GPU worker remains on AutoDL.  Origins are explicit and
    # operator-configured; credentials are not enabled for this token-based API.
    app.add_middleware(
        CORSMiddleware,
        allow_origins=list(service_settings.cors_origins),
        allow_credentials=False,
        allow_methods=["GET", "POST", "PUT", "OPTIONS"],
        allow_headers=["Authorization", "Content-Type", "X-Request-ID", "X-Chunk-SHA256", "X-Upload-Acknowledgement", "Idempotency-Key"],
        expose_headers=["X-Request-ID"],
    )
    if pose_request_diagnostics_enabled():
        app.add_middleware(PoseRequestDiagnosticsMiddleware)
    def authorize(
        request: Request,
        authorization: str | None = Header(default=None),
    ) -> None:
        if service_settings.api_key is None:
            # Keep liveness/readiness and the two non-sensitive discovery
            # documents queryable during bootstrap, but fail closed for jobs,
            # artifacts and model metadata whenever the service is public or
            # marked production.  Otherwise a deployment could ignore a
            # not_ready response and expose uploaded data anonymously.
            public_discovery_paths = {"/health/live", "/health/ready", "/v1/meta", "/v1/techniques"}
            if _should_redact_public(service_settings) and request.url.path not in public_discovery_paths:
                raise HTTPException(
                    503,
                    {
                        "code": "authentication_not_configured",
                        "message": "RALLYMATE_API_KEY is required for public API access",
                    },
                )
            return
        expected = f"Bearer {service_settings.api_key}"
        if authorization is None or not secrets.compare_digest(authorization, expected):
            raise HTTPException(
                401,
                "missing or invalid bearer token",
                headers={"WWW-Authenticate": "Bearer"},
            )

    @app.get("/health/live")
    def liveness() -> dict:
        return {"status": "ok", "admission": {"max_active_jobs": service_settings.max_active_jobs,
            "accept_new_jobs": service_settings.accept_new_jobs and not maintenance.is_set()}}

    @app.get("/v1/meta")
    def api_meta(_: None = Depends(authorize)) -> dict:
        """Stable discovery document for a separately hosted web client."""

        try:
            catalog = technique_catalog(service_settings.resolved_technique_registry)
            technique_registry_info = {
                "available": True,
                "registry_version": catalog["registry_version"],
                "registry_sha256": catalog["registry_sha256"],
            }
        except TechniqueRegistryError as exc:
            technique_registry_info = {
                "available": False,
                "error": _public_error_detail(str(exc), service_settings),
            }
        return {
            "service": "RallyMate Vision API",
            "api_version": API_VERSION,
            "compatibility_version": API_COMPATIBILITY_VERSION,
            "environment": service_settings.environment,
            "deployment": deployment_identity.public_status(),
            "public_base_url": service_settings.public_base_url,
            "authentication": {
                "scheme": "bearer" if service_settings.api_key is not None else "none",
                "required": service_settings.api_key is not None,
                "browser_secret_injection": "reverse_proxy_or_same_origin_session",
            },
            "capabilities": {
                "async_video_jobs": True,
                "resumable_uploads": {"chunk_bytes": CHUNK_BYTES, "default_chunk_bytes": uploads.chunk_bytes,
                                      "acknowledgement": "chunk-v1", "parallel_chunks": 2, "expires_after_hours": 24},
                "trajectory_preview": True,
                "racket_bbox_observability": True,
                "technique_registry": technique_registry_info["available"],
                "formal_coach_score": False,
            },
            "technique_registry": technique_registry_info,
            "client_configuration": {
                "upload_endpoint": "/v1/jobs",
                "job_endpoint_template": "/v1/jobs/{job_id}",
                "result_endpoint_template": "/v1/jobs/{job_id}/demo-result",
                "trajectory_endpoint_template": "/v1/jobs/{job_id}/trajectory",
                "technique_assessment_endpoint_template": (
                    "/v1/jobs/{job_id}/technique-assessment"
                ),
                "technique_catalog_endpoint": "/v1/techniques",
            },
        }

    @app.get("/v1/techniques")
    def technique_capabilities(_: None = Depends(authorize)) -> dict:
        """Return the qualitative action catalog extracted from the five rules docs."""

        try:
            catalog = technique_catalog(service_settings.resolved_technique_registry)
        except TechniqueRegistryError as exc:
            raise HTTPException(
                503,
                _public_error_detail(
                    f"technique registry unavailable: {exc}", service_settings
                ),
            ) from exc
        return catalog

    @app.get("/health/ready")
    def readiness(response: Response, _: None = Depends(authorize)) -> dict:
        reasons = []
        registry_authority = None
        # Report the credential problem independently of the other startup
        # checks.  Operators should see it even when (for example) a model
        # license setting is also invalid; readiness remains a useful
        # actionable diagnostic rather than short-circuiting on the first error.
        if (
            isinstance(service_settings.environment, str)
            and service_settings.environment.strip().lower() == "production"
        ):
            reasons.extend(_production_api_key_reasons(service_settings.api_key))
        try:
            service_settings.validate_license()
            db.initialize()
            registry_authority = (
                service_settings.resolved_scoring_registry_authority
            )
        except Exception as exc:
            reasons.append(str(exc))
        for name, path in (
            ("detect_model", service_settings.detect_model),
            ("pose_model", service_settings.pose_model),
        ):
            if not path.exists():
                reasons.append(f"{name} is missing: {path}")
        for index, path in enumerate(service_settings.scoring_calibration_assets):
            if not path.exists():
                reasons.append(f"scoring_calibration_asset[{index}] is missing: {path}")
        if not reasons and service_settings.scoring_calibration_assets:
            assert registry_authority is not None
            try:
                load_pipeline_calibrations(
                    service_settings.scoring_calibration_assets,
                    trusted_ledger_path=(
                        service_settings.scoring_trusted_promotion_ledger
                    ),
                    feasibility_registry=registry_authority.payload,
                )
            except Exception as exc:
                reasons.append(f"scoring calibration validation failed: {exc}")
        if (
            service_settings.pose_config is not None
            and not service_settings.pose_config.exists()
        ):
            reasons.append(
                f"pose_config is missing: {service_settings.pose_config}"
            )
        executor = execution_status()
        if not execution_admits(executor):
            reasons.append("Inference executor is not available; uploads and existing reports are preserved.")
            response.status_code = 503
        if maintenance.is_set():
            reasons.append("Local service maintenance is draining the executor; submissions are paused.")
            response.status_code = 503
        response.headers["Cache-Control"] = "no-store"
        return {
            "status": "ready" if not reasons else "not_ready",
            "executor": executor,
            "inference_ready": None if executor["status"] == "unmonitored" else executor["available"],
            # Readiness is often queried through a public domain.  Keep config
            # names and remediation text, but never return /app, drive-letter,
            # or other worker-local paths in the JSON response.
            "reasons": [_redact_readiness_reason(reason) for reason in reasons],
            "queue": db.count_by_status(),
            "admission": {"max_active_jobs": service_settings.max_active_jobs,
                          "accept_new_jobs": service_settings.accept_new_jobs and not maintenance.is_set()
                                             and execution_admits(executor)},
            "maintenance": maintenance.is_set(),
            "environment": service_settings.environment,
            "model_license_ack": service_settings.model_license_ack,
            "pose_preset": service_settings.pose_preset,
            "pose_backend": service_settings.pose_backend,
            "pose_profile": service_settings.pose_profile,
            "pose_native_keypoint_format": (
                service_settings.pose_native_keypoint_format
            ),
            "registry_lifecycle": (
                {
                    "authority_version": registry_authority.authority_version,
                    "manifest_sha256": registry_authority.manifest_sha256,
                    "role": registry_authority.role,
                    "artifact_sha256": registry_authority.file_sha256,
                }
                if registry_authority is not None
                else None
            ),
        }

    @app.get("/", include_in_schema=False)
    def api_root() -> dict[str, str]:
        """Do not serve the retired Demo UI from the analysis API."""

        return {
            "service": "RallyMate Vision API",
            "frontend": "RallyMate 动作识别工作台",
            "message": "Use the web frontend on the configured public port.",
        }

    @app.get("/v1/model-capabilities")
    def model_capabilities(_: None = Depends(authorize)) -> dict:
        payload = static_model_capability()
        try:
            registry_authority = service_settings.resolved_scoring_registry_authority
        except (FileNotFoundError, RegistryLifecycleError, ServiceConfigError, ValueError) as exc:
            # Keep the historical 500 status for old clients while returning a
            # structured, topology-safe diagnostic instead of a framework
            # traceback when the immutable authority is unavailable.
            raise HTTPException(
                500,
                {
                    "code": "scoring_registry_unavailable",
                    "message": _public_error_detail(
                        f"scoring registry unavailable: {exc}", service_settings
                    ),
                },
            ) from exc
        registry = registry_authority.payload
        payload["minimum_scoring_loop"] = {
            # Do not disclose the worker's filesystem topology to a browser.
            # The lifecycle hashes below are sufficient for provenance checks.
            "registry_path": None,
            "registry_version": registry["registry_version"],
            "registry_lifecycle": {
                "authority_version": registry_authority.authority_version,
                "manifest_path": None,
                "manifest_sha256": registry_authority.manifest_sha256,
                "role": registry_authority.role,
                "lifecycle": registry_authority.lifecycle,
                "artifact_sha256": registry_authority.file_sha256,
            },
            "indicator_count": len(registry["indicators"]),
            "indicator_ids": sorted(
                item["indicator_id"] for item in registry["indicators"]
            ),
            "feasibility_levels": {
                item["indicator_id"]: item["feasibility_level"]
                for item in registry["indicators"]
            },
            "grade_policy": "evaluate_explicit_mathematical_rules_without_release_approval",
            "configured_production_calibration_asset_count": len(
                service_settings.scoring_calibration_assets
            ),
            "trusted_promotion_ledger_configured": (
                service_settings.scoring_trusted_promotion_ledger is not None
            ),
            "trusted_promotion_ledger_path": None,
            "trusted_runtime_profile_bindings_configured": (
                service_settings.scoring_trusted_runtime_profile_bindings
                is not None
            ),
            "trusted_runtime_profile_bindings_path": None,
            "runtime_view_evidence_dir_configured": (
                service_settings.scoring_runtime_view_evidence_dir is not None
            ),
            "runtime_view_evidence_dir": None,
        }
        try:
            catalog = technique_catalog(service_settings.resolved_technique_registry)
            payload["technique_registry"] = {
                "registry_id": catalog["registry_id"],
                "registry_version": catalog["registry_version"],
                "registry_sha256": catalog["registry_sha256"],
                "technique_count": len(catalog["techniques"]),
                "families": sorted(
                    {str(item["family"]) for item in catalog["techniques"]}
                ),
                "formal_score_available": False,
            }
        except TechniqueRegistryError as exc:
            payload["technique_registry"] = {
                "status": "unavailable",
                "error": _public_error_detail(str(exc), service_settings),
            }
        payload["trajectory_preview"] = {
            "endpoint": "/v1/jobs/{job_id}/trajectory",
            "prediction": "constant_velocity_extrapolation",
            "prediction_status": "heuristic_preview",
            "racket_geometry": "bbox_only",
        }
        return payload

    @app.get("/v1/jobs")
    def list_jobs(
        limit: int = Query(
            20,
            ge=1,
            le=100,
            description="Maximum number of recent jobs to return (bounded for public use).",
        ),
        report_contract: ReportContract = Query(DEFAULT_REPORT_CONTRACT),
        _: None = Depends(authorize),
    ) -> dict:
        jobs = db.list_jobs(limit=limit)
        return {
            "items": [
                _public_job(
                    job,
                    service_settings.public_base_url,
                    redact_summary=_should_redact_public(service_settings),
                    report_contract=report_contract,
                )
                for job in jobs
            ],
            "count": len(jobs),
        }

    def require_submission_open():
        if not service_settings.accept_new_jobs or maintenance.is_set():
            raise HTTPException(503, {"code": "submissions_paused",
                "message": "视频分析提交暂时暂停；已有上传和报告仍保留，请稍后重试。"},
                headers={"Retry-After": "30", "Cache-Control": "no-store"})
        if not execution_admits(execution_status()):
            raise HTTPException(503, {"code": "executor_unavailable",
                "message": "分析执行器暂不可用；已上传的视频仍保留，请稍后重试提交。"},
                headers={"Retry-After": "30", "Cache-Control": "no-store"})

    @app.post("/internal/maintenance/stop-executor", include_in_schema=False)
    def stop_executor_for_maintenance(request: Request, response: Response,
                                      _: None = Depends(authorize)) -> dict:
        # This route is not in the public browser gateway allowlist. It is only
        # for the authenticated, loopback maintenance launcher.
        if not service_settings.api_key:
            raise HTTPException(503, {"code": "maintenance_auth_not_configured",
                                      "message": "Local maintenance requires API authentication."})
        if request.client is None or request.client.host not in {"127.0.0.1", "::1"}:
            raise HTTPException(403, "local maintenance only")
        if executor_shutdown is None:
            raise HTTPException(404, "supervised executor not configured")
        with admission_lock:
            was_paused = maintenance.is_set()
            maintenance.set()
            try:
                queue = db.count_by_status()
                if queue.get("running", 0) or queue.get("queued", 0):
                    raise ValueError("Analysis work is active or queued")
                executor_shutdown()
            except ValueError:
                if not was_paused:
                    maintenance.clear()
                raise HTTPException(409, {"code": "executor_busy", "message": "Current work must finish before maintenance."})
            except Exception:
                # An uncertain stop request must never lead to new work being
                # admitted or to an unsafe forced replacement by the launcher.
                raise HTTPException(503, {"code": "executor_stop_unconfirmed", "message": "Executor shutdown could not be confirmed."})
        executor = execution_status()
        stopped = executor["status"] == "stopped"
        response.status_code = 200 if stopped else 202
        response.headers["Cache-Control"] = "no-store"
        return {"status": "stopped" if stopped else "draining", "executor_stopped": stopped, "executor": executor}
    async def _submit_video(
        request: Request, video: UploadFile | None, court_mode: str = "disabled",
        manual_polygon_normalized: str | None = None,
        manual_polygon_role: str = "court_outer_doubles_corners",
        write_annotated_video: bool = True, max_players: int = 2,
        start_ms: int = 0, end_ms: int | None = None,
        max_frames: int | None = None, job_id: str | None = None,
        *, _owned_upload: tuple[Path, dict] | None = None,
    ) -> dict:
        require_submission_open()
        if court_mode not in {"auto", "manual", "disabled"}:
            raise HTTPException(422, "court_mode must be auto, manual or disabled")
        if manual_polygon_role not in {
            "visible_region",
            "court_outer_doubles_corners",
        }:
            raise HTTPException(422, "invalid manual_polygon_role")
        if not 1 <= max_players <= 4:
            raise HTTPException(422, "max_players must be in the 1..4 range")
        if start_ms < 0:
            raise HTTPException(422, "start_ms must be >= 0")
        if end_ms is not None and end_ms <= start_ms:
            raise HTTPException(422, "end_ms must be greater than start_ms")
        if max_frames is not None and max_frames < 1:
            raise HTTPException(422, "max_frames must be >= 1")
        try:
            registry_authority = service_settings.resolved_scoring_registry_authority
        except (FileNotFoundError, RegistryLifecycleError, ServiceConfigError, ValueError) as exc:
            raise HTTPException(
                503,
                {
                    "code": "scoring_registry_unavailable",
                    "message": _public_error_detail(
                        f"scoring registry unavailable: {exc}", service_settings
                    ),
                },
            ) from exc
        try:
            # Pin the qualitative catalog at submission time.  A later operator
            # replacement must never silently reinterpret an already queued job.
            technique_registry_snapshot = technique_catalog(
                service_settings.resolved_technique_registry
            )
        except TechniqueRegistryError as exc:
            raise HTTPException(
                503,
                {
                    "code": "technique_registry_unavailable",
                    "message": _public_error_detail(str(exc), service_settings),
                },
            ) from exc
        polygon = _parse_polygon(manual_polygon_normalized)
        if court_mode == "manual" and polygon is None:
            raise HTTPException(
                422, "manual_polygon_normalized is required for manual court mode"
            )
        if (video is None) != (_owned_upload is not None):
            raise ValueError("submission must own either a multipart stream or an upload-store file")
        filename = _safe_filename(_owned_upload[1]["filename"] if _owned_upload else video.filename)
        suffix = Path(filename).suffix.lower()
        if suffix not in ALLOWED_EXTENSIONS:
            raise HTTPException(415, f"unsupported video extension: {suffix}")

        job_id = job_id or str(uuid.uuid4())
        video_path = service_settings.uploads_dir / f"{job_id}{suffix}"
        total = 0
        owned_source = None
        handed_off = False
        request_created = False
        request_path = None
        request_identity = None
        try:
            if _owned_upload is not None:
                source, manifest = _owned_upload
                if manifest.get("upload_id") != job_id:
                    raise HTTPException(409, "上传文件与会话不一致")
                owned_source, owned_stat = _owned_upload_source(
                    source, service_settings.uploads_dir, job_id,
                    manifest["size"], service_settings.max_upload_bytes,
                )
                total = owned_stat.st_size
                probe_path = owned_source
            else:
                if video_path.exists() and db.get_job(job_id) is None:
                    video_path.unlink()
                with video_path.open("xb") as handle:
                    while chunk := await video.read(1024 * 1024):
                        total += len(chunk)
                        if total > service_settings.max_upload_bytes:
                            raise HTTPException(413, "video exceeds the configured size limit")
                        handle.write(chunk)
                probe_path = video_path
            if total == 0:
                raise HTTPException(422, "uploaded video is empty")
            try:
                metadata = probe_video(probe_path)
            except (ValueError, RuntimeError) as exc:
                raise HTTPException(
                    422,
                    _public_error_detail(
                        f"video cannot be decoded: {exc}", service_settings
                    ),
                ) from exc
            if (
                metadata.duration_ms
                > service_settings.max_video_duration_seconds * 1000
            ):
                raise HTTPException(413, "video exceeds the configured duration limit")

            output_dir = service_settings.runs_dir / job_id
            request_path = service_settings.requests_dir / f"{job_id}.json"
            payload = {
                "schema_version": "1.0.0",
                "job_id": job_id,
                "source": {"video_path": str(video_path)},
                "output": {"directory": str(output_dir)},
                "models": {
                    "detect": str(service_settings.detect_model),
                    "pose": str(service_settings.pose_model),
                    "device": service_settings.device,
                    "detect_imgsz": 960,
                    "pose_imgsz": 640,
                    "detect_confidence": 0.15,
                    "pose_confidence": 0.25,
                    "pose_backend": service_settings.pose_backend,
                    "pose_runtime": service_settings.pose_runtime,
                    "pose_profile": service_settings.pose_profile,
                    "pose_config": (
                        str(service_settings.pose_config)
                        if service_settings.pose_config is not None
                        else None
                    ),
                    "pose_preset": service_settings.pose_preset,
                    "pose_native_keypoint_format": (
                        service_settings.pose_native_keypoint_format
                    ),
                },
                "processing": {
                    "start_ms": start_ms,
                    "end_ms": end_ms,
                    "frame_stride": 1,
                    "max_frames": max_frames,
                    "max_players": max_players,
                    "court_every_n_frames": 25,
                    "write_annotated_video": write_annotated_video,
                },
                "court": {
                    "mode": court_mode,
                    "manual_polygon_normalized": polygon,
                    "manual_polygon_role": manual_polygon_role,
                    "refresh_policy": "once" if court_mode == "manual" else "until_usable",
                },
                "scoring": {
                    "feasibility_registry": str(registry_authority.path),
                    "calibration_assets": [
                        str(path)
                        for path in service_settings.scoring_calibration_assets
                    ],
                },
                "upstream_metadata": {
                    "original_filename": filename,
                    "content_type": video.content_type if video is not None else None,
                    "upload_bytes": total,
                    "source_ip": request.client.host if request.client else None,
                    "request_id": getattr(request.state, "request_id", None),
                    "technique_registry": {
                        "registry_id": technique_registry_snapshot["registry_id"],
                        "registry_version": technique_registry_snapshot[
                            "registry_version"
                        ],
                        "registry_sha256": technique_registry_snapshot[
                            "registry_sha256"
                        ],
                        "source": (
                            "operator_override"
                            if service_settings.resolved_technique_registry is not None
                            else "packaged"
                        ),
                    },
                },
            }
            if owned_source is not None:
                # Recheck captured identity and metadata after probing, while
                # the store still holds the upload session's exclusive lock.
                owned_source, current_stat = _owned_upload_source(
                    owned_source, service_settings.uploads_dir, job_id,
                    total, service_settings.max_upload_bytes,
                )
                if _submission_file_identity(owned_stat) != _submission_file_identity(current_stat):
                    raise HTTPException(409, "上传文件在校验期间发生变化，请重试")
                if db.get_job(job_id) is not None:
                    raise HTTPException(409, "视频已提交推理")
                request_created = _publish_owned_request(request_path, payload)
                request_identity = _submission_file_identity(request_path.stat())
                _sync_submission_directory(request_path.parent)
                try:
                    _rename_submission_file(owned_source, video_path)
                    handed_off = True
                except FileExistsError:
                    # An uncertain database commit may leave a durable input.
                    # Recover matching bytes without ever replacing its name.
                    _verify_recovered_video(owned_source, video_path, current_stat)
                _sync_submission_directory(video_path.parent)
                _sync_submission_directory(owned_source.parent)
            else:
                request_path.write_text(
                    json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
                )
            with admission_lock:
                # A submission may have spent time receiving/probing the
                # video since its first admission check. Fence its actual
                # enqueue against maintenance, restoring handed-off uploads
                # through the existing exception path when admission closes.
                require_submission_open()
                job = db.create_job(
                    job_id, filename, video_path, request_path, output_dir,
                    max_active_jobs=service_settings.max_active_jobs,
                )
        except Exception:
            # A database error may occur after commit. Never remove a durable
            # input if queue ownership cannot be determined reliably.
            try:
                committed = db.get_job(job_id) is not None
            except Exception:
                committed = True
            if not committed and video_path.exists():
                if handed_off:
                    # The store retains chunks until enqueue succeeds. Restore
                    # its assembled source as well when the queue rejected it.
                    if not owned_source.exists():
                        _rename_submission_file(video_path, owned_source)
                        _sync_submission_directory(video_path.parent)
                        _sync_submission_directory(owned_source.parent)
                elif _owned_upload is None:
                    video_path.unlink()
            if (not committed and request_created and request_path.exists()
                    and _submission_file_identity(request_path.stat()) == request_identity):
                request_path.unlink()
                _sync_submission_directory(request_path.parent)
            raise
        finally:
            if video is not None:
                await video.close()
        return _public_job(
            job,
            service_settings.public_base_url,
            redact_summary=_should_redact_public(service_settings),
        )


    @app.post("/v1/jobs", status_code=202)
    async def submit_job(
        request: Request,
        video: UploadFile = File(...),
        # Court localization is opt-in.  The default analysis output focuses
        # on RTMPose joints plus YOLO ball/racket evidence.
        court_mode: str = Form("disabled"),
        manual_polygon_normalized: str | None = Form(None),
        manual_polygon_role: str = Form("court_outer_doubles_corners"),
        write_annotated_video: bool = Form(True),
        max_players: int = Form(2),
        start_ms: int = Form(0),
        end_ms: int | None = Form(None),
        max_frames: int | None = Form(None),
        _: None = Depends(authorize),
    ) -> dict:
        return await _submit_video(
            request, video, court_mode, manual_polygon_normalized,
            manual_polygon_role, write_annotated_video, max_players,
            start_ms, end_ms, max_frames,
        )

    uploads = UploadStore(service_settings.uploads_dir / ".chunks", service_settings.max_upload_bytes)

    @app.post("/v1/uploads", status_code=201)
    def create_upload(payload: dict, _: None = Depends(authorize)) -> dict:
        require_submission_open()
        filename = _safe_filename(str(payload.get("filename", "")))
        if Path(filename).suffix.lower() not in ALLOWED_EXTENSIONS:
            raise HTTPException(415, "不支持的视频格式")
        options = payload.get("options", {})
        allowed = {"court_mode", "write_annotated_video", "max_players", "start_ms", "end_ms", "max_frames", "manual_polygon_normalized", "manual_polygon_role"}
        if not isinstance(options, dict) or set(options) - allowed:
            raise HTTPException(422, "无效的分析选项")
        for key in ("max_players", "start_ms", "end_ms", "max_frames"):
            if key in options and options[key] is not None and (isinstance(options[key], bool) or not isinstance(options[key], int)):
                raise HTTPException(422, "分析参数必须为整数")
        if "write_annotated_video" in options and not isinstance(options["write_annotated_video"], bool):
            raise HTTPException(422, "write_annotated_video必须为布尔值")
        for key in ("court_mode", "manual_polygon_normalized", "manual_polygon_role"):
            if key in options and options[key] is not None and not isinstance(options[key], str):
                raise HTTPException(422, "分析参数格式不正确")
        if options.get("court_mode", "disabled") not in {"auto", "manual", "disabled"}:
            raise HTTPException(422, "无效的场地模式")
        if options.get("manual_polygon_role", "court_outer_doubles_corners") not in {"visible_region", "court_outer_doubles_corners"}:
            raise HTTPException(422, "无效的场地标定类型")
        players, start = options.get("max_players", 2), options.get("start_ms", 0)
        end, frames = options.get("end_ms"), options.get("max_frames")
        if players is None or start is None or not 1 <= players <= 4 or start < 0:
            raise HTTPException(422, "无效的人数或起始时间")
        if (end is not None and end <= start) or (frames is not None and frames < 1):
            raise HTTPException(422, "无效的视频分析范围")
        polygon = _parse_polygon(options.get("manual_polygon_normalized"))
        if options.get("court_mode") == "manual" and polygon is None:
            raise HTTPException(422, "手工场地模式需要标定点")
        return uploads.create(str(payload.get("upload_id", "")), filename, payload.get("size"), options)

    @app.get("/v1/uploads/{upload_id}")
    def upload_status(upload_id: str, _: None = Depends(authorize)) -> dict:
        return uploads.status(upload_id)

    @app.put("/v1/uploads/{upload_id}/chunks/{index}")
    async def upload_chunk(upload_id: str, index: int, request: Request, _: None = Depends(authorize)) -> dict:
        # Bound each request even when Content-Length is absent or forged.
        await run_in_threadpool(uploads.read_metadata, uploads.directory(upload_id))
        content = bytearray()
        async for block in request.stream():
            if len(content) + len(block) > CHUNK_BYTES:
                raise HTTPException(413, "分块过大")
            content.extend(block)
        return await run_in_threadpool(
            uploads.put, upload_id, index, bytes(content), request.headers.get("X-Chunk-SHA256", ""),
            compact_ack=request.headers.get("X-Upload-Acknowledgement") == "chunk-v1",
        )

    @app.post("/v1/uploads/{upload_id}/complete", status_code=202)
    def complete_upload(upload_id: str, request: Request, _: None = Depends(authorize)) -> dict:
        def existing(job_id):
            job = db.get_job(job_id)
            return _public_job(job, service_settings.public_base_url, redact_summary=_should_redact_public(service_settings)) if job else None

        def enqueue(path, manifest):
            return asyncio.run(_submit_video(
                request, None, job_id=upload_id, _owned_upload=(path, manifest), **manifest["options"],
            ))

        return uploads.complete(upload_id, enqueue, existing)

    @app.get("/v1/jobs/{job_id}")
    def get_job(job_id: str, report_contract: ReportContract = Query(DEFAULT_REPORT_CONTRACT),
                _: None = Depends(authorize)) -> dict:
        job = db.get_job(job_id)
        if job is None:
            raise HTTPException(404, "job not found")
        if job["status"] != "running":
            live_trajectory_cache.discard(job_id)
        job["summary"] = summary_with_actions(job)
        result = _public_job(
            job,
            service_settings.public_base_url,
            redact_summary=_should_redact_public(service_settings),
            report_contract=report_contract,
        )
        result["original_video"] = original_video_descriptor(job, service_settings)
        return result

    @app.get("/v1/jobs/{job_id}/status")
    def get_job_status(job_id: str, response: Response, _: None = Depends(authorize)) -> dict:
        # Polling must not deserialize or transfer the potentially very large
        # completed summary, nor recalculate action recognition.
        with db.connect() as connection:
            row = connection.execute("SELECT id,status,original_filename,progress_json,cancel_requested,error,"
                "created_at,updated_at,started_at,completed_at,cancelled_at,cancel_requested_at "
                "FROM jobs WHERE id=?", (job_id,)).fetchone()
            queue_position = None
            if row is not None and row["status"] == "queued" and not row["cancel_requested"]:
                # Same eligible FIFO population as claim_next; never read summaries.
                queue_position = connection.execute(
                    "SELECT COUNT(*) FROM jobs WHERE status='queued' AND cancel_requested=0 "
                    "AND attempts < ? AND (created_at < ? OR (created_at = ? AND id <= ?))",
                    (service_settings.max_attempts, row["created_at"], row["created_at"], row["id"]),
                ).fetchone()[0]
        if row is None:
            raise HTTPException(404, "job not found")
        result = dict(row)
        result["queue_position"] = queue_position
        result["executor"] = execution_status()
        raw = result.pop("progress_json", None)
        result["progress"] = json.loads(raw) if raw else {"phase": result["status"],
            "percent": 100 if result["status"] == "succeeded" else 0}
        result["cancel_requested"] = bool(result["cancel_requested"])
        result["original_filename"] = _public_original_filename(
            result, redact=_should_redact_public(service_settings))
        result["error"] = _public_error_detail(result.get("error"), service_settings)
        response.headers["Cache-Control"] = "no-store"
        return result

    @app.post("/v1/jobs/{job_id}/cancel")
    def cancel_job(job_id: str, response: Response,
                   report_contract: ReportContract = Query(DEFAULT_REPORT_CONTRACT),
                   _: None = Depends(authorize)) -> dict:
        job = db.request_cancel(job_id)
        if job is None:
            raise HTTPException(404, "job not found")
        live_trajectory_cache.discard(job_id)
        response.headers["Cache-Control"] = "no-store"
        job["summary"] = summary_with_actions(job)
        result = _public_job(job, service_settings.public_base_url,
                             redact_summary=_should_redact_public(service_settings),
                             report_contract=report_contract)
        result["original_video"] = original_video_descriptor(job, service_settings)
        return result

    def technical_review_job(job_id: str) -> dict:
        job = db.get_job(job_id)
        if job is None:
            raise HTTPException(404, "job not found")
        return job

    @app.get("/v1/jobs/{job_id}/technical-review")
    def get_technical_review(job_id: str, response: Response, _: None = Depends(authorize)) -> dict:
        response.headers["Cache-Control"] = "no-store"
        try:
            return technical_reviews.get(technical_review_job(job_id))
        except TechnicalReviewError as exc:
            raise HTTPException(exc.status, exc.detail()) from exc

    @app.post("/v1/jobs/{job_id}/technical-review")
    async def save_technical_review(job_id: str, request: Request, response: Response,
                                    _: None = Depends(authorize)) -> dict:
        response.headers["Cache-Control"] = "no-store"
        try:
            body = bytearray()
            async for chunk in request.stream():
                body.extend(chunk)
                if len(body) > MAX_BODY_BYTES:
                    raise TechnicalReviewError("review_too_large", "评审内容过长。", 413)
            submission = parse_review_input(bytes(body))
            job = await run_in_threadpool(technical_review_job, job_id)
            return await run_in_threadpool(technical_reviews.save, job, submission)
        except TechnicalReviewError as exc:
            raise HTTPException(exc.status, exc.detail()) from exc

    @app.get("/v1/jobs/{job_id}/technical-review/export")
    def export_technical_review(job_id: str, response: Response, _: None = Depends(authorize)) -> dict:
        response.headers["Cache-Control"] = "no-store"
        try:
            result = technical_reviews.get(technical_review_job(job_id), export=True)
            # Keep this filename independent of client-supplied paths and IDs.
            response.headers["Content-Disposition"] = 'attachment; filename="technical-reviews.json"'
            return result
        except TechnicalReviewError as exc:
            raise HTTPException(exc.status, exc.detail()) from exc

    async def segmented_response(request, job_id, kind, offset=0, limit=20, event_id=None,
                                 report_delivery: ReportDelivery = "inline"):
        if kind != "summary":
            waiting_since = time.monotonic()
            def build_section(job, check):
                try:
                    if kind == "overview":
                        payload = segmented_reports.overview(job, original_video_descriptor(job, service_settings),
                            check, registry_path=service_settings.resolved_technique_registry)
                    elif kind == "motion":
                        payload = segmented_reports.motion(job, event_id, check)
                    elif kind == "actions":
                        payload = segmented_reports.actions(job, offset, limit, check)
                    else:
                        payload = segmented_reports.detail(job, event_id, offset, limit, check)
                    if _should_redact_public(service_settings):
                        payload = _redact_public_value(payload)
                    return encode_bounded_json(payload, max_bytes=MAX_DETAIL_BYTES if kind in {"detail", "motion"}
                        else MAX_OVERVIEW_BYTES if kind == "overview" else MAX_SUMMARY_BYTES,
                        cancellation_callback=check)
                except (ReportEvidenceLimitError, PostprocessOutputLimit, PostprocessResourceLimit):
                    raise
                except (ArtifactEvidenceError, ValueError, OSError, KeyError, TypeError) as exc:
                    if isinstance(exc, SegmentedReportError):
                        raise
                    raise SegmentedReportError() from exc

            try:
                delivery = await run_in_threadpool(segmented_delivery.request,
                    job_id, kind, offset, limit, event_id, build_section)
                # Old, already-open clients expect a 200 section. They wait on
                # exactly the same shared build; only explicitly opted-in
                # clients receive the new 202 polling envelope.
                while delivery.state == "preparing" and report_delivery == "inline":
                    if await request.is_disconnected():
                        raise HTTPException(409, {"code": "report_read_cancelled", "message": "报告读取已取消。"})
                    if time.monotonic() - waiting_since >= 60:
                        raise HTTPException(503, {"code": "report_segments_busy", "message": "报告仍在后台准备，请稍后重试。"},
                            headers={"Retry-After": "2", "Cache-Control": "no-store"})
                    await asyncio.sleep(.2)
                    delivery = await run_in_threadpool(segmented_delivery.request,
                        job_id, kind, offset, limit, event_id, build_section)
            except DeliveryError as exc:
                raise HTTPException(exc.status, exc.detail()) from exc
            if delivery.state == "ready":
                return Response(delivery.body, media_type="application/json", headers={"Cache-Control": "no-store"})
            if delivery.state == "preparing":
                return JSONResponse(status_code=202, content={"schema_version": "segmented-report-v1",
                    "job_id": job_id, "status": "preparing", "retry_after_ms": 1000,
                    "delivery": {"state": "preparing"}}, headers={"Retry-After": "1", "Cache-Control": "no-store"})
            error = delivery.error
            if error.code in {"report_preparation_queue_full", "report_preparation_queue_timeout", "report_segments_busy"}:
                raise HTTPException(429, {"code": "report_segments_busy", "message": "动作证据正在准备，请稍后重试。"},
                    headers={"Retry-After": "2", "Cache-Control": "no-store"})
            if error.code == "report_preparation_timeout":
                raise HTTPException(503, {"code": "report_segment_resource_limit",
                    "message": "报告后处理超过时间预算（60 秒），已停止；原始上传和已生成产物保留"})
            raise HTTPException(error.status, error.detail(), headers={"Cache-Control": "no-store"})
        # Heavy evidence reads must not starve the independent first-screen
        # summary in another tab. Both lanes remain bounded.
        slots = segmented_summary_slots
        if not slots.acquire(blocking=False):
            raise HTTPException(429, {"code": "report_segments_busy", "message": "动作证据正在读取，请稍后重试。"},
                                headers={"Retry-After": "2", "Cache-Control": "no-store"})
        disconnected = Event()

        async def watch_disconnect():
            while not disconnected.is_set():
                if await request.is_disconnected():
                    disconnected.set()
                    return
                await asyncio.sleep(.1)

        def prepare():
            initial_state = db.execution_state(job_id)
            if initial_state is None:
                raise HTTPException(404, "job not found")
            if initial_state["status"] != "succeeded" or initial_state.get("cancel_requested"):
                raise HTTPException(409, "job has not succeeded or was cancelled")

            def revision():
                # Completed summaries are normally immutable. Hash the stored
                # text at the two delivery boundaries as well, so an external
                # same-status edit cannot serve bytes from a stale snapshot.
                with db.connect() as connection:
                    row = connection.execute("SELECT updated_at,summary_json FROM jobs WHERE id=?", (job_id,)).fetchone()
                return None if row is None else (row["updated_at"],
                    hashlib.sha256((row["summary_json"] or "").encode("utf-8")).hexdigest())

            initial_revision = revision()

            def current():
                if disconnected.is_set():
                    raise SegmentedReportError("report_read_cancelled", "报告读取已取消。", 409)
                state = db.execution_state(job_id)
                if state != initial_state:
                    raise SegmentedReportError("report_source_changed", "分析任务已变化，请重新读取报告。", 409)

            budget = PostprocessBudget(max_seconds=60, cancellation_callback=current)
            budget.check(force=True)
            job = db.get_job(job_id)
            if job is None or Path(job["output_dir"]).resolve() != (service_settings.runs_dir / job_id).resolve():
                raise SegmentedReportError()
            try:
                payload = build_segmented_summary(job, original_video_descriptor(job, service_settings))
                if _should_redact_public(service_settings):
                    payload = _redact_public_value(payload)
                body = encode_bounded_json(payload, max_bytes=MAX_SUMMARY_BYTES,
                                           cancellation_callback=budget.check)
                if revision() != initial_revision:
                    raise SegmentedReportError("report_source_changed", "分析摘要已变化，请重新读取报告。", 409)
                budget.check(force=True)
                return body
            except SegmentedReportError:
                raise
            except (PostprocessResourceLimit, ReportEvidenceLimitError):
                raise
            except (ArtifactEvidenceError, ValueError, OSError, KeyError, TypeError) as exc:
                raise SegmentedReportError() from exc

        watcher = asyncio.create_task(watch_disconnect())
        try:
            body = await run_in_threadpool(prepare)
            return Response(body, media_type="application/json", headers={"Cache-Control": "no-store"})
        except SegmentedReportError as exc:
            raise HTTPException(exc.status, {"code": exc.code, "message": exc.message}) from exc
        except (PostprocessOutputLimit, ReportEvidenceLimitError) as exc:
            raise HTTPException(413, {"code": "report_segment_limit",
                "message": "本页证据超过读取上限，请减少每页条数；原始产物已保留。"}) from exc
        except PostprocessResourceLimit as exc:
            raise HTTPException(503, {"code": "report_segment_resource_limit", "message": str(exc)}) from exc
        finally:
            disconnected.set()
            watcher.cancel()
            slots.release()

    @app.get("/v1/jobs/{job_id}/report-summary")
    async def get_report_summary(job_id: str, request: Request, _: None = Depends(authorize)):
        return await segmented_response(request, job_id, "summary")

    @app.get("/v1/jobs/{job_id}/report-overview")
    async def get_report_overview(job_id: str, request: Request, report_delivery: ReportDelivery = Query("inline"),
                                  _: None = Depends(authorize)):
        return await segmented_response(request, job_id, "overview", report_delivery=report_delivery)

    @app.get("/v1/jobs/{job_id}/report-motion/{episode_id}")
    async def get_report_motion(job_id: str, episode_id: str, request: Request, report_delivery: ReportDelivery = Query("inline"),
                                _: None = Depends(authorize)):
        return await segmented_response(request, job_id, "motion", event_id=episode_id, report_delivery=report_delivery)

    @app.get("/v1/jobs/{job_id}/report-actions")
    async def get_report_actions(job_id: str, request: Request, offset: int = Query(0, ge=0, le=65000),
                                 limit: int = Query(20, ge=1, le=50), report_delivery: ReportDelivery = Query("inline"),
                                 _: None = Depends(authorize)):
        return await segmented_response(request, job_id, "actions", offset, limit, report_delivery=report_delivery)

    @app.get("/v1/jobs/{job_id}/report-actions/{event_id}")
    async def get_report_action(job_id: str, event_id: str, request: Request, offset: int = Query(0, ge=0, le=65000),
                                limit: int = Query(4, ge=1, le=8), report_delivery: ReportDelivery = Query("inline"),
                                _: None = Depends(authorize)):
        return await segmented_response(request, job_id, "detail", offset, limit, event_id, report_delivery=report_delivery)

    def report_records(path, summary, check):
        loop = summary.get("minimum_scoring_loop") or {}
        manifest = loop.get("evidence_storage")
        if manifest is not None or (path.parent / POOL_FILENAME).exists():
            provenance = loop.get("provenance") or {}
            try:
                verify_evidence_manifest(path.parent, manifest,
                    expected_video_id=loop.get("video_id"),
                    expected_source_id=provenance.get("pipeline_job_id"),
                    cancellation_callback=check)
                video_sha = provenance.get("video_sha256")
                if isinstance(video_sha, str) and manifest.get("video_sha256") != video_sha.lower():
                    raise ArtifactEvidenceError("evidence video hash differs from report provenance")
            except ArtifactEvidenceError as exc:
                raise UserDemoResultError(f"报告证据校验失败：{exc}") from exc
        return open_indicator_feature_records(path, cancellation_callback=check)

    def build_demo_payload(job: dict, report_contract: ReportContract,
                           report_transport: ReportTransport, *, report_budget=None) -> dict:
        report_budget = report_budget or PostprocessBudget(max_seconds=report_delivery_cache.preparation_timeout_seconds)
        def check_report_budget(*, force=False):
            try:
                report_budget.check(force=force)
            except PostprocessResourceLimit as exc:
                raise HTTPException(503, {"code": "report_resource_limit", "message": str(exc)}) from exc
        with ExitStack() as evidence_context:
            check_report_budget(force=True)
            job_id = job["id"]
            indicator_features_path = Path(job["output_dir"]) / "indicator-features.jsonl"
            # A successful run can legitimately contain no measured indicator
            # records (for example a short clip with no detectable motion).  Keep
            # the result contract available in that case so the UI can show a
            # truthful "not observed" report instead of falling back to synthetic
            # scores or forcing a re-upload.  Non-empty artifacts remain strictly
            # validated by ``load_indicator_feature_records`` below.
            records: list[dict[str, Any]] = []
            summary = summary_with_actions(job)
            check_report_budget(force=True)
            try:
                if indicator_features_path.is_file() and indicator_features_path.stat().st_size > 0:
                    records = evidence_context.enter_context(report_records(indicator_features_path, summary, check_report_budget))
                result = build_user_demo_result(
                    summary,
                    records,
                    technique_registry_path=service_settings.resolved_technique_registry,
                    include_derived_assessments=False,
                )
                check_report_budget(force=True)
            except TechniqueRegistrySnapshotError as exc:
                raise HTTPException(
                    409, _public_error_detail(exc.as_detail(), service_settings)
                ) from exc
            except (UserDemoResultError, TechniqueRegistryError, OSError) as exc:
                raise HTTPException(
                    500,
                    _public_error_detail(
                        f"demo result is unavailable: {exc}", service_settings
                    ),
                ) from exc
            public_job = _public_job(
                job,
                service_settings.public_base_url,
                redact_summary=_should_redact_public(service_settings),
            )
            result["artifact_urls"] = {
                name: url for name, url in public_job["artifact_urls"].items()
                if name not in MESH_ARTIFACTS
            }
            # Keep cached report bytes independent of concurrent renderer output.
            # This is only a bootstrap hint: the browser immediately resolves a
            # declared mesh task via the small, uncached /mesh-preview endpoint.
            # A ready preview also uses that route, rather than baking mutable
            # descriptors or URLs into the numerical report's content identity.
            declared_artifacts = (job.get("summary") or {}).get("artifacts")
            declared_mesh = declared_artifacts.get("sam3d_mesh") if isinstance(declared_artifacts, dict) else None
            declared_mesh_status = declared_mesh.get("status") if isinstance(declared_mesh, dict) else None
            result["sam3d_mesh"] = None
            result["sam3d_mesh_status"] = (
                "pending" if declared_mesh_status in {"pending", "ready"}
                else "failed" if declared_mesh_status == "failed" else "unavailable"
            )
            result["original_video"] = original_video_descriptor(job, service_settings)
            result["trajectory_url"] = public_job["trajectory_url"]
            result["technique_assessment_url"] = public_job["technique_assessment_url"]
            loop_summary = summary.get("minimum_scoring_loop")
            review_video_id = loop_summary.get("video_id") if isinstance(loop_summary, dict) else None
            result["footwork_review"] = load_footwork_review(
                Path(job["output_dir"]) / "events.jsonl",
                records,
                video_id=review_video_id or job_id,
            )
            check_report_budget(force=True)
            loop_provenance = (loop_summary or {}).get("provenance", {})
            result["source_aligned_assessment"] = load_source_aligned_assessment(
                Path(job["output_dir"]),
                video_id=review_video_id or job_id,
                duration_ms=summary.get("input", {}).get("video", {}).get("duration_ms"),
                video_sha256=loop_provenance.get("video_sha256"),
                frames_sha256=loop_provenance.get("frames_sha256"),
            )
            check_report_budget(force=True)
            result["process_evaluation"] = build_process_evaluation(
                summary, records, footwork_review=result["footwork_review"],
            )
            check_report_budget(force=True)
            result["swing_kinematics"] = build_swing_kinematics(Path(job["output_dir"]), summary)
            check_report_budget(force=True)
            result["tennis_assessment"] = build_tennis_assessment(
                summary, Path(job["output_dir"]), job_id=job_id,
            )
            check_report_budget(force=True)
            result["indicator_assessment"] = build_indicator_assessment(
                summary, records, source_assessment=result["source_aligned_assessment"],
                process_evaluation=result["process_evaluation"],
            )
            check_report_budget(force=True)
            try:
                card_execution = build_card_execution_report(Path(job["output_dir"]))
                if card_execution.get("source_run_id") != job_id:
                    raise ValueError("card_report_job_binding_mismatch")
                result["card_execution"] = card_execution
            except (OSError, ValueError, TypeError, KeyError, OverflowError):
                result["card_execution"] = {
                    "version": CARD_EXECUTION_VERSION, "rule_version": CARD_RULE_VERSION,
                    "status": "unavailable", "source_run_id": job_id,
                    "reason_codes": ["raw_card_execution_evidence_unavailable"],
                    "cards": [], "overall_score": None,
                    "limitations": ["本次逐卡计算所需的原始帧证据缺失、身份不一致或校验未通过，暂不能生成逐卡结果。"],
                }
            check_report_budget(force=True)
            result["analysis_report"] = build_analysis_report(result, summary=summary)
            check_report_budget(force=True)
            projected = project_report(result, report_contract)
            check_report_budget(force=True)
            if report_transport == 'model-proof-pool-v1':
                try:
                    projected = pack_report_evidence(projected, cancellation_callback=check_report_budget)
                except ReportEvidenceLimitError as exc:
                    raise HTTPException(413, {"code": "report_delivery_limit",
                        "message": "报告证据超过传输上限，分析产物已保留；当前页面暂不能读取这份完整报告。"}) from exc
                except ReportEvidenceTransportError as exc:
                    raise HTTPException(500, 'report evidence transport is unavailable') from exc
            check_report_budget(force=True)
            return projected

    @app.get("/v1/jobs/{job_id}/demo-result")
    def get_demo_result(job_id: str, report_contract: ReportContract = Query(DEFAULT_REPORT_CONTRACT),
                        report_transport: ReportTransport = Query('native'),
                        report_delivery: ReportDelivery = Query('inline'),
                        _: None = Depends(authorize)) -> dict:
        scope = (job_id, report_contract, report_transport)

        def preparing_response(pending=None):
            if pending is not None and pending.error is not None:
                raise HTTPException(pending.error.status, pending.error.detail(),
                                    headers={"Retry-After": "5", "Cache-Control": "no-store"})
            return JSONResponse(status_code=202, content={"job_id": job_id, "status": "preparing",
                "report_delivery": "deferred-v1", "retry_after_ms": 1000},
                headers={"Retry-After": "1", "Cache-Control": "no-store"})

        if report_delivery == "deferred-v1" and report_transport == "model-proof-pool-v1":
            pending = report_delivery_cache.pending_result(scope)
            if pending is not None:
                # Poll only terminal/cancellation state, not the multi-megabyte
                # summary JSON. No response bytes are delivered by this path.
                state = db.execution_state(job_id)
                if state is None:
                    raise HTTPException(404, "job not found")
                if state["status"] != "succeeded" or state.get("cancel_requested"):
                    raise HTTPException(409, "job has not succeeded or was cancelled")
                return preparing_response(pending)
        job = db.get_job(job_id)
        if job is None:
            raise HTTPException(404, "job not found")
        if job["status"] != "succeeded" or job.get("cancel_requested"):
            raise HTTPException(409, "job has not succeeded or was cancelled")
        if report_delivery == "inline":
            try:
                current_packaged_rules_identity()
            except DeliveryError as exc:
                raise HTTPException(exc.status, exc.detail()) from exc
            return build_demo_payload(job, report_contract, report_transport)
        if report_transport != "model-proof-pool-v1":
            raise HTTPException(422, {"code": "report_delivery_requires_pool",
                "message": "Deferred delivery requires model-proof-pool-v1 transport."})
        pending = report_delivery_cache.pending_result(scope)
        if pending is not None:
            return preparing_response(pending)
        try:
            key = report_source_key(job, report_contract, report_transport)
        except DeliveryError as exc:
            raise HTTPException(exc.status, exc.detail()) from exc
        except (OSError, ValueError) as exc:
            raise HTTPException(500, {"code": "report_source_unavailable",
                "message": "Report source identity is unavailable."}) from exc

        def source_is_current():
            current = db.get_job(job_id)
            return (current is not None and current["status"] == "succeeded"
                and not current.get("cancel_requested")
                and report_source_key(current, report_contract, report_transport) == key)

        def build_bytes():
            try:
                budget = PostprocessBudget(max_seconds=report_delivery_cache.preparation_timeout_seconds)
                payload = build_demo_payload(job, report_contract, report_transport, report_budget=budget)
                body = encode_bounded_json(payload, max_bytes=report_delivery_cache.max_bytes,
                                           cancellation_callback=budget.check)
                budget.check(force=True)
                return body
            except PostprocessOutputLimit as exc:
                raise DeliveryError(413, "report_delivery_limit", str(exc)) from exc
            except PostprocessResourceLimit as exc:
                raise DeliveryError(503, "report_resource_limit", str(exc)) from exc
            except HTTPException as exc:
                # Preserve the known, public resource diagnosis across deferred
                # delivery. Otherwise the same limit is specific inline but
                # becomes an unexplained generic failure in the normal UI path.
                if (isinstance(exc.detail, dict)
                        and (exc.status_code, exc.detail.get("code")) in {
                            (503, "report_resource_limit"), (413, "report_delivery_limit")
                        }):
                    raise DeliveryError(exc.status_code, exc.detail["code"], exc.detail["message"]) from exc
                # Keep the original HTTP status but do not leak arbitrary
                # nested exceptions into the asynchronous transport envelope.
                raise DeliveryError(exc.status_code, "report_preparation_failed",
                                    "Report preparation failed.") from exc

        # An explicit in-process invalidation must also bypass disk entries.
        # Ordinary restart nonces do not affect the stable builder fingerprint.
        persistent_version = (report_builder_version if REPORT_PROCESS_VERSION == report_process_at_start
                              else report_builder_version + ":" + REPORT_PROCESS_VERSION)
        delivery = report_delivery_cache.request(key, build_bytes, source_is_current, scope=scope,
            persistent_key=persistent_report_key(persistent_version, key))
        if delivery.state == "error":
            raise HTTPException(delivery.error.status, delivery.error.detail(),
                                headers={"Retry-After": "5", "Cache-Control": "no-store"})
        if delivery.state == "ready":
            # The cache rechecks this request's source snapshot outside its
            # lock immediately before handing out a ready body.
            return Response(content=delivery.body, media_type="application/json",
                            headers={"Cache-Control": "no-store"})
        return preparing_response()

    def shot_statistics_base(job_id: str) -> dict:
        job = db.get_job(job_id)
        if job is None:
            raise HTTPException(404, "job not found")
        if job["status"] != "succeeded" or job.get("cancel_requested"):
            raise HTTPException(409, "shot statistics require a completed analysis")
        output = Path(job["output_dir"]).resolve()
        if output != (service_settings.runs_dir / job_id).resolve():
            raise HTTPException(409, "shot statistics source is unavailable")
        frames = output / "frames.jsonl"
        try:
            if frames.is_symlink() or not frames.is_file():
                raise ValueError("missing frames")
            frame_stat, video_stat = frames.stat(), Path(job["video_path"]).stat()
            provenance = (job.get("summary") or {}).get("minimum_scoring_loop", {}).get("provenance", {})
            key = (job_id, frame_stat.st_size, frame_stat.st_mtime_ns,
                   video_stat.st_size, video_stat.st_mtime_ns, provenance.get("video_sha256"))
            with shot_statistics_lock:
                base = shot_statistics_cache.get(key)
                if base is None:
                    original = verify_original_video(job, service_settings)
                    base = build_shot_statistics(frames, job_id=job_id,
                                                 expected_video_sha256=original.sha256)
                    shot_statistics_cache[key] = base
                    while len(shot_statistics_cache) > 4:
                        shot_statistics_cache.popitem(last=False)
                shot_statistics_cache.move_to_end(key)
                return base
        except (OSError, ValueError, TypeError, KeyError):
            raise HTTPException(409, "击球统计的原视频或帧数据缺失、来源不匹配，请重新读取。") from None

    @app.get("/v1/jobs/{job_id}/shot-statistics")
    def get_shot_statistics(job_id: str, response: Response, _: None = Depends(authorize)) -> dict:
        response.headers["Cache-Control"] = "private, no-store"
        base = shot_statistics_base(job_id)
        try:
            reviews, revision = shot_reviews.read(job_id, base["source"]["video_sha256"])
            return build_shot_statistics(service_settings.runs_dir / job_id / "frames.jsonl",
                job_id=job_id, reviews=reviews, revision=revision,
                expected_video_sha256=base["source"]["video_sha256"], trusted_reviews=True)
        except ShotReviewError as exc:
            raise HTTPException(exc.status, str(exc)) from exc

        except (ValueError, OSError):
            raise HTTPException(409, "击球统计数据已改变或暂不可读取，请重新读取。") from None

    @app.post("/v1/jobs/{job_id}/shot-statistics")
    async def save_shot_statistics(job_id: str, request: Request, response: Response,
                                   _: None = Depends(authorize)) -> dict:
        response.headers["Cache-Control"] = "private, no-store"
        try:
            body = bytearray()
            async for chunk in request.stream():
                body.extend(chunk)
                if len(body) > MAX_SHOT_REVIEW_BYTES:
                    raise ShotReviewError("本次标记内容过长。", 413)
            submission = parse_shot_review(bytes(body))

            def save():
                base = shot_statistics_base(job_id)
                digest = base["source"]["video_sha256"]
                def validate_event(reviews, revision):
                    current = build_shot_statistics(service_settings.runs_dir / job_id / "frames.jsonl",
                        job_id=job_id, reviews=reviews, revision=revision, expected_video_sha256=digest, trusted_reviews=True)
                    return validate_review(submission.event, current)

                reviews, revision = shot_reviews.save(job_id, digest, submission, validate_event)
                return build_shot_statistics(service_settings.runs_dir / job_id / "frames.jsonl",
                    job_id=job_id, reviews=reviews, revision=revision, expected_video_sha256=digest, trusted_reviews=True)

            return await run_in_threadpool(save)
        except ShotReviewError as exc:
            raise HTTPException(exc.status, str(exc)) from exc
        except ValueError:
            raise HTTPException(422, "标记时间、事件或球场位置无效，请重新选择。") from None

    @app.get("/v1/jobs/{job_id}/mesh-preview")
    def get_mesh_preview(job_id: str, response: Response, _: None = Depends(authorize)) -> dict:
        job = db.get_job(job_id)
        if job is None:
            raise HTTPException(404, "job not found")
        media = mesh_preview_descriptor(job, service_settings.public_base_url)
        response.headers["Cache-Control"] = "private, no-store"
        return {"job_id": job_id, "status": mesh_preview_status(job, media), "sam3d_mesh": media}

    @app.get("/v1/jobs/{job_id}/trajectory")
    def get_trajectory(
        job_id: str,
        response: Response,
        sample_limit: int = Query(
            240,
            ge=1,
            le=1000,
            description="Maximum observed points returned per selected track.",
        ),
        prediction_horizon_ms: int = Query(
            400,
            ge=0,
            le=5000,
            description=(
                "Short preview horizon for the ball constant-velocity heuristic; "
                "this is not a physics prediction."
            ),
        ),
        _: None = Depends(authorize),
    ) -> dict:
        """Return bounded ball/racket observability derived from ``frames.jsonl``.

        The route never claims a detector accuracy result.  Ball extrapolation is
        explicitly labelled ``heuristic_preview`` and racket output remains
        bounding-box-only until a dedicated keypoint model is deployed.
        """

        job = db.get_job(job_id)
        if job is None:
            raise HTTPException(404, "job not found")
        if job["status"] not in {"running", "succeeded"}:
            live_trajectory_cache.discard(job_id)
            raise HTTPException(409, "trajectory requires a running or succeeded job")
        frames_path = Path(job["output_dir"]) / "frames.jsonl"
        if not frames_path.is_file():
            raise HTTPException(
                409,
                {
                    "code": "trajectory_requires_frames_artifact",
                    "message": "trajectory preview is waiting for frames.jsonl observations",
                    "required_artifact": "frames.jsonl",
                },
            )
        try:
            if job["status"] == "running":
                payload = live_trajectory_cache.preview(job, frames_path, sample_limit=sample_limit,
                    prediction_horizon_ms=prediction_horizon_ms)
                response.headers["Cache-Control"] = "no-store"
                return payload
            live_trajectory_cache.discard(job_id)
            stat = frames_path.stat()
            cache_key = (str(frames_path), stat.st_mtime_ns, stat.st_size,
                         job["status"], sample_limit, prediction_horizon_ms)
            with trajectory_cache_lock:
                payload = trajectory_cache.get(cache_key)
                if payload is None:
                    payload = build_trajectory_preview(
                        frames_path,
                        sample_limit=sample_limit,
                        prediction_horizon_ms=prediction_horizon_ms,
                        allow_partial=job["status"] == "running",
                    )
                    payload["job_id"] = job_id
                    payload["source"]["frames_path"] = "frames.jsonl"
                    trajectory_cache[cache_key] = payload
                    while len(trajectory_cache) > 6:
                        trajectory_cache.popitem(last=False)
                trajectory_cache.move_to_end(cache_key)
            # Do not expose the worker's absolute filesystem path to a browser
            # or a remote client.  The artifact remains addressable through the
            # authenticated artifact route if a caller needs the raw JSONL.
            response.headers["Cache-Control"] = "no-store"
            return payload
        except TrajectoryExtractionError as exc:
            raise HTTPException(
                422,
                {
                    "code": "trajectory_artifact_invalid",
                    "message": _public_error_detail(str(exc), service_settings),
                },
            ) from exc

    @app.get("/v1/jobs/{job_id}/tennis-assessment")
    def get_tennis_assessment(job_id: str, response: Response, _: None = Depends(authorize)) -> dict:
        job = db.get_job(job_id)
        if job is None:
            raise HTTPException(404, "job not found")
        if job["status"] != "succeeded":
            raise HTTPException(409, "tennis assessment requires a succeeded job")
        response.headers["Cache-Control"] = "no-store"
        # Re-evaluate saved motion with the current rubric. Historical videos do
        # not need another GPU run or the large per-card evidence report.
        return build_tennis_assessment(
            summary_with_actions(job), Path(job["output_dir"]), job_id=job_id,
        )

    @app.get("/v1/jobs/{job_id}/swing-kinematics")
    def get_swing_kinematics(job_id: str, response: Response, _: None = Depends(authorize)) -> dict:
        job = db.get_job(job_id)
        if job is None:
            raise HTTPException(404, "job not found")
        if job["status"] != "succeeded":
            raise HTTPException(409, "swing measurements require a succeeded job")
        response.headers["Cache-Control"] = "no-store"
        return build_swing_kinematics(Path(job["output_dir"]), job.get("summary") or {})

    @app.get("/v1/jobs/{job_id}/pose-preview")
    def get_pose_preview(
        job_id: str,
        response: Response,
        request: Request,
        start_ms: int = Query(0, ge=0, description="Start of the source-video playback window."),
        duration_ms: int = Query(10_000, ge=1, le=10_000),
        sample_limit: int = Query(600, ge=1, le=600),
        pose_transport: PosePlaybackTransport = Query("full"),
        _: None = Depends(authorize),
    ) -> dict:
        """Read a bounded skeleton window without rerunning pose inference."""
        with pose_diagnostic_stage(request, "db_get_job"):
            job = db.get_job(job_id)
        if job is None:
            raise HTTPException(404, "job not found")
        if job["status"] not in {"running", "succeeded"}:
            raise HTTPException(409, "pose playback requires a running or succeeded job")
        frames_path = Path(job["output_dir"]) / "frames.jsonl"
        primary_path = Path(job["output_dir"]) / "primary-player.jsonl"
        if not frames_path.is_file():
            raise HTTPException(409, {"code": "pose_playback_requires_frames_artifact", "required_artifact": "frames.jsonl"})
        try:
            def signature(path: Path) -> tuple:
                stat = path.stat() if path.is_file() else None
                return (str(path), stat.st_mtime_ns, stat.st_size) if stat else (str(path), None, None)
            cache_key = (signature(frames_path), signature(primary_path), signature(frames_path.with_name("summary.json")),
                         job["status"], start_ms, duration_ms, sample_limit)
            record_pose_phase(request, "pose_lock_wait")
            with pose_cache_lock:
                payload = pose_cache.get(cache_key)
                record_pose_phase(request, "pose_lock_acquired", cache_hit=payload is not None)
                if payload is None:
                    with pose_diagnostic_stage(request, "build"):
                        payload = build_pose_playback(
                            frames_path, primary_timeline_path=primary_path,
                            start_ms=start_ms, duration_ms=duration_ms, sample_limit=sample_limit,
                            allow_partial=job["status"] == "running",
                        )
                    payload["job_id"] = job_id
                    pose_cache[cache_key] = payload
                    while len(pose_cache) > 16:
                        pose_cache.popitem(last=False)
                pose_cache.move_to_end(cache_key)
            response.headers["Cache-Control"] = "no-store"
            record_pose_phase(request, "route_return")
            return pose_playback_transport(payload, pose_transport)
        except (PosePlaybackError, OSError) as exc:
            raise HTTPException(422, {"code": "pose_playback_artifact_invalid",
                                      "message": "Pose playback source could not be verified."}) from exc

    @app.get("/v1/jobs/{job_id}/technique-assessment")
    def get_technique_assessment(
        job_id: str,
        report_contract: ReportContract = Query(DEFAULT_REPORT_CONTRACT),
        _: None = Depends(authorize),
    ) -> dict:
        report_budget = PostprocessBudget(max_seconds=report_delivery_cache.preparation_timeout_seconds)
        def check_report_budget(*, force=False):
            try:
                report_budget.check(force=force)
            except PostprocessResourceLimit as exc:
                raise HTTPException(503, {"code": "report_resource_limit", "message": str(exc)}) from exc
        with ExitStack() as evidence_context:
            """Return the evidence-gated qualitative assessment for a completed job."""

            job = db.get_job(job_id)
            if job is None:
                raise HTTPException(404, "job not found")
            if job["status"] != "succeeded":
                raise HTTPException(409, "job has not succeeded")
            indicator_features_path = Path(job["output_dir"]) / "indicator-features.jsonl"
            check_report_budget(force=True)
            summary = summary_with_actions(job)
            check_report_budget(force=True)
            records: list[dict] = []
            if indicator_features_path.is_file():
                try:
                    if indicator_features_path.stat().st_size > 0:
                        records = evidence_context.enter_context(report_records(indicator_features_path, summary, check_report_budget))
                except UserDemoResultError as exc:
                    raise HTTPException(
                        422,
                        _public_error_detail(
                            f"technique evidence is invalid: {exc}", service_settings
                        ),
                    ) from exc
            try:
                result = build_technique_assessment(
                    summary,
                    records,
                    registry_path=service_settings.resolved_technique_registry,
                )
                check_report_budget(force=True)
            except TechniqueRegistrySnapshotError as exc:
                raise HTTPException(
                    409, _public_error_detail(exc.as_detail(), service_settings)
                ) from exc
            except (ValueError, TechniqueRegistryError) as exc:
                raise HTTPException(
                    503,
                    _public_error_detail(
                        f"technique assessment is unavailable: {exc}", service_settings
                    ),
                ) from exc
            result["catalog_url"] = _public_url(
                "/v1/techniques", service_settings.public_base_url
            )
            result["trajectory_url"] = _public_url(
                f"/v1/jobs/{job_id}/trajectory", service_settings.public_base_url
            )
            projected = project_report(result, report_contract)
            check_report_budget(force=True)
            return projected

    @app.get("/v1/jobs/{job_id}/foot-crossing-candidates")
    def get_foot_crossing_candidates(
        job_id: str,
        expected_artifact_sha256: str | None = Query(default=None, pattern=r"^[0-9a-fA-F]{64}$"),
        _: None = Depends(authorize),
    ) -> JSONResponse:
        # A separately published, read-only snapshot; never compute or backfill here.
        headers = {"Cache-Control": "private, no-store"}
        job = db.get_job(job_id)
        if job is None:
            raise HTTPException(404, "job not found", headers=headers)
        if job["status"] != "succeeded" or job.get("cancel_requested"):
            raise HTTPException(409, "job has not succeeded or was cancelled", headers=headers)
        return JSONResponse(load_foot_crossing_candidates(
            service_settings.data_root, job, expected_artifact_sha256=expected_artifact_sha256,
        ), headers=headers)

    @app.get("/v1/jobs/{job_id}/advice-evidence")
    def get_advice_evidence(job_id: str, technique: AdviceTechnique = Query(...),
                            _: None = Depends(authorize)) -> JSONResponse:
        assessment = get_technique_assessment(job_id, report_contract='current')
        try:
            return JSONResponse(project_advice_context(job_id, assessment, technique))
        except AdviceContextTooLarge as exc:
            raise HTTPException(422, {'code': 'advice_context_limit',
                                      'message': 'Complete advice context exceeds the response limit.'}) from exc

    @app.get("/v1/jobs/{job_id}/original-video")
    def get_original_video(job_id: str, _: None = Depends(authorize)) -> Response:
        job = db.get_job(job_id)
        if job is None:
            raise HTTPException(404, "job not found")
        try:
            original = verify_original_video(job, service_settings)
        except OriginalVideoUnavailable as exc:
            raise HTTPException(409, str(exc)) from exc
        # Retain FileResponse Range/If-Range handling with a compliant 416 unit. Never put
        # bearer credentials, database paths or uploaded filenames in this URL.
        return OriginalVideoResponse(original.path, filename=f"{job_id}-original{original.path.suffix.lower()}",
                            content_disposition_type="inline",
                            headers={"Cache-Control": "private, no-store",
                                     "X-Content-Type-Options": "nosniff"})

    @app.get("/v1/jobs/{job_id}/artifacts/{artifact_name}")
    def get_artifact(
        job_id: str,
        artifact_name: str,
        report_contract: ReportContract = Query("current"),
        _: None = Depends(authorize),
    ) -> Response:
        if artifact_name not in ALLOWED_ARTIFACTS:
            raise HTTPException(404, "artifact not found")
        job = db.get_job(job_id)
        if job is None:
            raise HTTPException(404, "job not found")
        if job["status"] != "succeeded":
            raise HTTPException(409, "job has not succeeded")
        if artifact_name in MESH_ARTIFACTS and mesh_preview_descriptor(job) is None:
            raise HTTPException(404, "mesh preview is not ready")
        artifact = Path(job["output_dir"]) / artifact_name
        if not artifact.exists():
            raise HTTPException(404, "artifact not found")
        # Artifact downloads stay byte-for-byte native by default. An explicit
        # legacy view supports old offline summary importers without rewriting
        # provenance-bound files or presenting a projection as a raw artifact.
        if artifact_name == "summary.json" and report_contract == "legacy-v1":
            try:
                summary = json.loads(artifact.read_text(encoding="utf-8"))
            except (OSError, ValueError) as exc:
                raise HTTPException(422, "summary artifact is invalid") from exc
            return JSONResponse(project_report(summary, report_contract), headers={
                "Cache-Control": "private, no-store",
                "X-RallyMate-Report-Contract": report_contract,
                "Content-Disposition": f'attachment; filename="{job_id}-summary-legacy-v1.json"',
            })
        return FileResponse(
            artifact,
            filename=f"{job_id}-{artifact_name}",
            content_disposition_type=(
                "inline" if artifact_name in INLINE_PREVIEW_ARTIFACTS else "attachment"
            ),
            # Artifacts can contain frame coordinates and operator-local
            # provenance; never let a browser/CDN cache a privileged response.
            headers={"Cache-Control": "private, no-store"},
        )

    return app


app = create_app()
