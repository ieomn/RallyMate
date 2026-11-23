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
from contextlib import asynccontextmanager, contextmanager, ExitStack
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
from starlette._utils import get_route_path
from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.formparsers import MultiPartException
from fastapi.routing import APIRoute
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
from rallymate_service.database import JobDatabase, QueueCapacityError, UserQueueCapacityError
from rallymate_service.database_factory import build_database
from rallymate_service.segmented_report import (
    SegmentedReportStore, SegmentedReportError, build_summary as build_segmented_summary,
    MAX_SUMMARY_BYTES, MAX_DETAIL_BYTES, MAX_OVERVIEW_BYTES,
)
from rallymate_service.segmented_delivery import SegmentedDelivery
from rallymate_service.live_trajectory import LiveTrajectoryCache
from rallymate_service.original_video import (
    OriginalVideoUnavailable, original_video_descriptor, verify_original_video,
)
from rallymate_service.original_video_response import OriginalVideoResponse
from rallymate_service.mesh_preview import MESH_ARTIFACTS, MESH_VIDEOS, mesh_preview_descriptor, mesh_preview_status
from rallymate_service.mesh_geometry import build_mesh_geometry_payload
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


from rallymate_service.api_redaction import (
    ALLOWED_ARTIFACTS,
    INLINE_PREVIEW_ARTIFACTS,
    _MIN_PRODUCTION_API_KEY_LENGTH,
    _PATH_FIELD_PATTERN,
    _PATH_TOKEN_PATTERN,
    _PLACEHOLDER_API_KEY_MARKERS,
    _REQUEST_ID_PATTERN,
    _production_api_key_reasons,
    _public_error_detail,
    _public_job,
    _public_original_filename,
    _public_summary,
    _public_url,
    _redact_public_value,
    _redact_readiness_reason,
    _safe_request_id,
    _should_redact_public,
)
from rallymate_service.routes import ApiRouteContext
from rallymate_service.routes.health import install_health_routes
from rallymate_service.routes.media import install_media_routes
from rallymate_service.routes.reviews import install_review_routes


ALLOWED_EXTENSIONS = {".mp4", ".mov", ".m4v", ".avi", ".mkv"}
API_VERSION = "1.3.0"
API_COMPATIBILITY_VERSION = "1.2.0"


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


HEAVY_REPORT_WAIT_SECONDS = 1.0


def create_app(
    settings: ServiceSettings | None = None,
    database: JobDatabase | None = None,
    *,
    executor_status: Callable[[], dict] | None = None,
    executor_shutdown: Callable[[], dict] | None = None,
    auth_config=None,
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
    db = database or build_database(service_settings)
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
    # Inline report builders re-read multi-hundred-megabyte artifacts and run
    # the scoring rubrics synchronously on the request threadpool. Bound them
    # so parallel requests cannot starve every other API route.
    heavy_report_slots = BoundedSemaphore(getattr(service_settings, "heavy_report_concurrency", 4))

    @contextmanager
    def heavy_report_slot():
        if not heavy_report_slots.acquire(timeout=HEAVY_REPORT_WAIT_SECONDS):
            raise HTTPException(429, {"code": "report_builder_busy",
                                      "message": "报告生成繁忙，请稍后重试。"},
                                headers={"Retry-After": "5", "Cache-Control": "no-store"})
        try:
            yield
        finally:
            heavy_report_slots.release()

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
        if isinstance(exc, UserQueueCapacityError):
            return JSONResponse(status_code=429, content={"detail": {"code": "analysis_user_queue_full",
                "message": f"您已有 {exc.limit} 个分析任务在排队或运行，请等待完成后再提交。",
                "max_active_jobs_per_user": exc.limit}},
                headers={"Retry-After": "30", "Cache-Control": "no-store"})
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

    if pose_request_diagnostics_enabled():
        app.add_middleware(PoseRequestDiagnosticsMiddleware)
    def authorize(
        request: Request,
        authorization: str | None = Header(default=None),
    ) -> None:
        route_path = get_route_path(request.scope)
        if route_path.startswith("/v1/"):
            if getattr(request.state, "account_session", None) is None:
                raise HTTPException(401, "请先登录。")
            return
        if service_settings.api_key is None:
            # Keep liveness/readiness and the two non-sensitive discovery
            # documents queryable during bootstrap, but fail closed for jobs,
            # artifacts and model metadata whenever the service is public or
            # marked production.  Otherwise a deployment could ignore a
            # not_ready response and expose uploaded data anonymously.
            public_discovery_paths = {"/health/live", "/health/ready", "/v1/meta", "/v1/techniques"}
            if _should_redact_public(service_settings) and route_path not in public_discovery_paths:
                raise HTTPException(
                    503,
                    {
                        "code": "authentication_not_configured",
                        "message": "RALLYMATE_API_KEY is required for public API access",
                    },
                )
            return
        expected = f"Bearer {service_settings.api_key}"
        if (authorization is None or not authorization.isascii()
                or not expected.isascii() or not secrets.compare_digest(authorization, expected)):
            raise HTTPException(
                401,
                "missing or invalid bearer token",
                headers={"WWW-Authenticate": "Bearer"},
            )

    def require_submission_open():
        if not service_settings.accept_new_jobs or maintenance.is_set():
            raise HTTPException(503, {"code": "submissions_paused",
                "message": "视频分析提交暂时暂停；已有上传和报告仍保留，请稍后重试。"},
                headers={"Retry-After": "30", "Cache-Control": "no-store"})
        if not execution_admits(execution_status()):
            raise HTTPException(503, {"code": "executor_unavailable",
                "message": "分析执行器暂不可用；已上传的视频仍保留，请稍后重试提交。"},
                headers={"Retry-After": "30", "Cache-Control": "no-store"})

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


    class BoundedUploadRoute(APIRoute):
        def get_route_handler(self):
            handler = super().get_route_handler()

            async def bounded(request: Request):
                # FastAPI parses declared bodies before dependency resolution.
                # Authenticate before it buffers JSON or spools uploaded bytes.
                authorize(request, request.headers.get("Authorization"))
                # Match the parser's 1 MiB field allowance for JSON metadata;
                # multipart also needs room for its permitted video bytes.
                limit = 1024 * 1024
                if self.path == "/v1/jobs":
                    limit += service_settings.max_upload_bytes
                content_length = request.headers.get("Content-Length")
                if content_length is not None:
                    try:
                        length = int(content_length)
                    except ValueError:
                        raise HTTPException(400, "invalid Content-Length") from None
                    if length < 0:
                        raise HTTPException(400, "invalid Content-Length")
                    if length > limit:
                        raise HTTPException(413, "upload request exceeds the configured size limit")
                received = 0
                oversized = False

                async def receive():
                    nonlocal received, oversized
                    message = await request.receive()
                    if message["type"] == "http.request":
                        received += len(message.get("body", b""))
                        if received > limit:
                            oversized = True
                            # The multipart parser closes its temporary files
                            # on this exception, including an incomplete upload.
                            raise MultiPartException("upload request exceeds the configured size limit")
                    return message

                try:
                    return await handler(Request(request.scope, receive=receive))
                except StarletteHTTPException:
                    if oversized:
                        raise HTTPException(413, "upload request exceeds the configured size limit") from None
                    raise

            return bounded

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

    app.router.add_api_route("/v1/jobs", submit_job, methods=["POST"], status_code=202,
                             route_class_override=BoundedUploadRoute)

    uploads = UploadStore(service_settings.uploads_dir / ".chunks", service_settings.max_upload_bytes)

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

    app.router.add_api_route("/v1/uploads", create_upload, methods=["POST"], status_code=201,
                             route_class_override=BoundedUploadRoute)

    @app.get("/v1/uploads/{upload_id}")
    def upload_status(upload_id: str, _: None = Depends(authorize)) -> dict:
        return uploads.status(upload_id)

    @app.delete("/v1/uploads/{upload_id}")
    def cancel_upload(upload_id: str, _: None = Depends(authorize)) -> dict:
        # Owner-scoped by the account boundary and the scoped store. Releases
        # parts and the admission slot; a submitted upload is never touched.
        return uploads.cancel(upload_id)

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
        if _should_redact_public(service_settings):
            result["progress"] = _redact_public_value(result["progress"])
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
            with heavy_report_slot():
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
            # Runs on the delivery thread without the request's user context;
            # a job soft-deleted meanwhile must never be served from cache.
            current = db.get_job(job_id)
            return (current is not None and current["status"] == "succeeded"
                and current.get("deleted_at") is None
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

    route_ctx = ApiRouteContext(
        api_mod=sys.modules[__name__],
        service_settings=service_settings,
        db=db,
        uploads=uploads,
        deployment_identity=deployment_identity,
        admission_lock=admission_lock,
        maintenance=maintenance,
        authorize=authorize,
        execution_status=execution_status,
        execution_admits=execution_admits,
        executor_shutdown=executor_shutdown,
        heavy_report_slot=heavy_report_slot,
        summary_with_actions=summary_with_actions,
        report_records=report_records,
        report_delivery_cache=report_delivery_cache,
        technical_reviews=technical_reviews,
        trajectory_cache=trajectory_cache,
        trajectory_cache_lock=trajectory_cache_lock,
        live_trajectory_cache=live_trajectory_cache,
        pose_cache=pose_cache,
        pose_cache_lock=pose_cache_lock,
    )
    install_health_routes(app, route_ctx)
    install_review_routes(app, route_ctx)
    install_media_routes(app, route_ctx)

    from rallymate_service.auth_runtime import install_account_system
    from rallymate_service.records_api import install_record_routes
    db, uploads = install_account_system(app, service_settings, db, uploads, config=auth_config)
    route_ctx.db = db
    route_ctx.uploads = uploads
    app.state.database = db
    # Segmented report delivery was constructed before the account boundary;
    # give it the user-scoped database so ownership and soft deletion apply.
    segmented_delivery.db = db
    install_record_routes(app, db)
    # Keep CORS outside the account boundary so allowed clients can read 401/403 responses.
    app.add_middleware(
        CORSMiddleware,
        allow_origins=list(service_settings.cors_origins),
        allow_credentials=True,
        allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
        allow_headers=["Authorization", "Content-Type", "X-CSRF-Token", "X-Request-ID", "X-Chunk-SHA256", "X-Upload-Acknowledgement", "Idempotency-Key"],
        expose_headers=["X-Request-ID"],
    )
    from rallymate_service.observability import install_api_observability
    install_api_observability(app, service_settings)
    return app


_default_app: FastAPI | None = None
_default_app_lock = Lock()


def __getattr__(name: str):
    """Build ``rallymate_service.api:app`` lazily (PEP 562).

    Importing this module (tests, ``create_app`` callers, ``rallymate-dev``)
    must not read deployment secrets or create a second application with its
    own database engine and report threads. ASGI servers that import
    ``rallymate_service.api:app`` still receive one process-wide instance.
    """
    if name == "app":
        global _default_app
        with _default_app_lock:
            if _default_app is None:
                _default_app = create_app()
            return _default_app
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
