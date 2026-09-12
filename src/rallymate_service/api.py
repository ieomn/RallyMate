from __future__ import annotations

import json
import re
import secrets
import uuid
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import (
    Depends,
    FastAPI,
    File,
    Form,
    Header,
    HTTPException,
    Query,
    Request,
    UploadFile,
)
from fastapi.responses import FileResponse
from fastapi.responses import HTMLResponse
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from rallymate_scoring.granularity import static_model_capability
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
from rallymate_service.database import JobDatabase
from rallymate_service.user_demo import (
    UserDemoResultError,
    build_user_demo_result,
    load_indicator_feature_records,
)
from rallymate_vision.quality import probe_video
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
    "indicator-features.jsonl",
    "scores.jsonl",
    "event-feature-errors.json",
    "scoring-loop-summary.json",
    "scoring-loop-report.html",
    "calculation-readiness.json",
    "indicator-measurement-portfolio.json",
    "scoring-cycle-measurement.json",
}
INLINE_PREVIEW_ARTIFACTS = {
    "annotated.mp4",
    "analysis-report.html",
    "scoring-loop-report.html",
}
DEMO_ASSETS_DIR = Path(__file__).resolve().parent / "assets"
DEMO_HTML_PATH = DEMO_ASSETS_DIR / "user-demo.html"
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


def _public_job(
    job: dict,
    public_base_url: str | None = None,
    *,
    redact_summary: bool = False,
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
            "attempts",
            "progress",
            "error",
            "summary",
        )
    }
    if redact_summary:
        # Uploaded filenames can contain a person's name or local directory
        # hints.  Keep a stable opaque label for public/prod job lists while
        # development retains the historical filename for compatibility.
        original = str(public.get("original_filename") or "")
        suffix = Path(original).suffix.lower() if original else ""
        public["original_filename"] = f"video-{str(job.get('id', 'job'))[:8]}{suffix}"
        public["summary"] = _public_summary(job, redact=True)
        if isinstance(public.get("error"), str):
            public["error"] = _redact_readiness_reason(public["error"])
    if job.get("status") == "succeeded":
        summary = job.get("summary") or {}
        public["scoring_state"] = summary.get("scoring_state")
        public["artifact_urls"] = {
            name: _public_url(f"/v1/jobs/{job['id']}/artifacts/{name}", public_base_url)
            for name in sorted(ALLOWED_ARTIFACTS)
            if (Path(job["output_dir"]) / name).exists()
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
        public["trajectory_url"] = None
        public["technique_assessment_url"] = None
    return public


def _safe_filename(value: str | None) -> str:
    original = Path(value or "upload.mp4").name
    stem = re.sub(r"[^A-Za-z0-9._-]+", "-", Path(original).stem).strip("-")
    suffix = Path(original).suffix.lower()
    return f"{stem or 'upload'}{suffix}"


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
    db = database or JobDatabase(service_settings.database_path)

    @asynccontextmanager
    async def lifespan(_: FastAPI):
        db.initialize()
        yield

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

    @app.middleware("http")
    async def request_id_middleware(request: Request, call_next):
        request_id = _safe_request_id(request.headers.get("X-Request-ID"))
        request.state.request_id = request_id
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
        allow_methods=["GET", "POST", "OPTIONS"],
        allow_headers=["Authorization", "Content-Type", "X-Request-ID"],
        expose_headers=["X-Request-ID"],
    )
    app.mount("/assets", StaticFiles(directory=DEMO_ASSETS_DIR), name="assets")

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
        return {"status": "ok"}

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
            "public_base_url": service_settings.public_base_url,
            "authentication": {
                "scheme": "bearer" if service_settings.api_key is not None else "none",
                "required": service_settings.api_key is not None,
                "browser_secret_injection": "reverse_proxy_or_same_origin_session",
            },
            "capabilities": {
                "async_video_jobs": True,
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
    def readiness(_: None = Depends(authorize)) -> dict:
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
        if service_settings.scoring_calibration_assets:
            ledger_path = service_settings.scoring_trusted_promotion_ledger
            if ledger_path is None:
                reasons.append("scoring_trusted_promotion_ledger is not configured")
            elif not ledger_path.exists():
                reasons.append(
                    f"scoring_trusted_promotion_ledger is missing: {ledger_path}"
                )
            binding_path = (
                service_settings.scoring_trusted_runtime_profile_bindings
            )
            if binding_path is None:
                reasons.append(
                    "scoring_trusted_runtime_profile_bindings is not configured"
                )
            elif not binding_path.exists():
                reasons.append(
                    "scoring_trusted_runtime_profile_bindings is missing: "
                    f"{binding_path}"
                )
            evidence_dir = service_settings.scoring_runtime_view_evidence_dir
            if evidence_dir is None:
                reasons.append(
                    "scoring_runtime_view_evidence_dir is not configured"
                )
            elif not evidence_dir.exists() or not evidence_dir.is_dir():
                reasons.append(
                    "scoring_runtime_view_evidence_dir is missing: "
                    f"{evidence_dir}"
                )
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
                load_trusted_runtime_binding_registry(
                    service_settings.scoring_trusted_runtime_profile_bindings
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
        return {
            "status": "ready" if not reasons else "not_ready",
            # Readiness is often queried through a public domain.  Keep config
            # names and remediation text, but never return /app, drive-letter,
            # or other worker-local paths in the JSON response.
            "reasons": [_redact_readiness_reason(reason) for reason in reasons],
            "queue": db.count_by_status(),
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

    @app.get("/", response_class=HTMLResponse, include_in_schema=False)
    def user_demo() -> str:
        return DEMO_HTML_PATH.read_text(encoding="utf-8")

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
            "grade_policy": "calibration_required_without_coach_ground_truth",
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
        _: None = Depends(authorize),
    ) -> dict:
        jobs = db.list_jobs(limit=limit)
        return {
            "items": [
                _public_job(
                    job,
                    service_settings.public_base_url,
                    redact_summary=_should_redact_public(service_settings),
                )
                for job in jobs
            ],
            "count": len(jobs),
        }

    @app.post("/v1/jobs", status_code=202)
    async def submit_job(
        request: Request,
        video: UploadFile = File(...),
        court_mode: str = Form("auto"),
        manual_polygon_normalized: str | None = Form(None),
        manual_polygon_role: str = Form("court_outer_doubles_corners"),
        write_annotated_video: bool = Form(True),
        max_players: int = Form(2),
        start_ms: int = Form(0),
        end_ms: int | None = Form(None),
        max_frames: int | None = Form(None),
        _: None = Depends(authorize),
    ) -> dict:
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
        filename = _safe_filename(video.filename)
        suffix = Path(filename).suffix.lower()
        if suffix not in ALLOWED_EXTENSIONS:
            raise HTTPException(415, f"unsupported video extension: {suffix}")

        job_id = str(uuid.uuid4())
        video_path = service_settings.uploads_dir / f"{job_id}{suffix}"
        total = 0
        try:
            with video_path.open("xb") as handle:
                while chunk := await video.read(1024 * 1024):
                    total += len(chunk)
                    if total > service_settings.max_upload_bytes:
                        raise HTTPException(413, "video exceeds the configured size limit")
                    handle.write(chunk)
            if total == 0:
                raise HTTPException(422, "uploaded video is empty")
            try:
                metadata = probe_video(video_path)
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
                    "content_type": video.content_type,
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
            request_path.write_text(
                json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
            )
            job = db.create_job(
                job_id, filename, video_path, request_path, output_dir
            )
        except Exception:
            if video_path.exists() and db.get_job(job_id) is None:
                video_path.unlink()
            raise
        finally:
            await video.close()
        return _public_job(
            job,
            service_settings.public_base_url,
            redact_summary=_should_redact_public(service_settings),
        )

    @app.get("/v1/jobs/{job_id}")
    def get_job(job_id: str, _: None = Depends(authorize)) -> dict:
        job = db.get_job(job_id)
        if job is None:
            raise HTTPException(404, "job not found")
        return _public_job(
            job,
            service_settings.public_base_url,
            redact_summary=_should_redact_public(service_settings),
        )

    @app.get("/v1/jobs/{job_id}/demo-result")
    def get_demo_result(job_id: str, _: None = Depends(authorize)) -> dict:
        job = db.get_job(job_id)
        if job is None:
            raise HTTPException(404, "job not found")
        if job["status"] != "succeeded":
            raise HTTPException(409, "job has not succeeded")
        indicator_features_path = Path(job["output_dir"]) / "indicator-features.jsonl"
        if not indicator_features_path.is_file():
            raise HTTPException(
                409,
                {
                    "code": "legacy_job_requires_reanalysis",
                    "message_zh": (
                        "这个旧任务缺少新版结果所需的动作测量文件。"
                        "请回到当前 Demo，重新选择原视频并运行分析。"
                    ),
                    "required_artifact": "indicator-features.jsonl",
                    "action": "reupload_and_reanalyze",
                },
            )
        try:
            records = load_indicator_feature_records(indicator_features_path)
            result = build_user_demo_result(
                job.get("summary") or {},
                records,
                technique_registry_path=service_settings.resolved_technique_registry,
            )
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
        result["artifact_urls"] = public_job["artifact_urls"]
        result["trajectory_url"] = public_job["trajectory_url"]
        result["technique_assessment_url"] = public_job["technique_assessment_url"]
        return result

    @app.get("/v1/jobs/{job_id}/trajectory")
    def get_trajectory(
        job_id: str,
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
        if job["status"] != "succeeded":
            raise HTTPException(409, "job has not succeeded")
        frames_path = Path(job["output_dir"]) / "frames.jsonl"
        if not frames_path.is_file():
            raise HTTPException(
                409,
                {
                    "code": "trajectory_requires_frames_artifact",
                    "message": "trajectory preview requires a successful frames.jsonl artifact",
                    "required_artifact": "frames.jsonl",
                },
            )
        try:
            payload = build_trajectory_preview(
                frames_path,
                sample_limit=sample_limit,
                prediction_horizon_ms=prediction_horizon_ms,
            )
            # Do not expose the worker's absolute filesystem path to a browser
            # or a remote client.  The artifact remains addressable through the
            # authenticated artifact route if a caller needs the raw JSONL.
            payload["job_id"] = job_id
            payload["source"]["frames_path"] = "frames.jsonl"
            return payload
        except TrajectoryExtractionError as exc:
            raise HTTPException(
                422,
                {
                    "code": "trajectory_artifact_invalid",
                    "message": _public_error_detail(str(exc), service_settings),
                },
            ) from exc

    @app.get("/v1/jobs/{job_id}/technique-assessment")
    def get_technique_assessment(
        job_id: str,
        _: None = Depends(authorize),
    ) -> dict:
        """Return the evidence-gated qualitative assessment for a completed job."""

        job = db.get_job(job_id)
        if job is None:
            raise HTTPException(404, "job not found")
        if job["status"] != "succeeded":
            raise HTTPException(409, "job has not succeeded")
        indicator_features_path = Path(job["output_dir"]) / "indicator-features.jsonl"
        records: list[dict] = []
        if indicator_features_path.is_file():
            try:
                if indicator_features_path.stat().st_size > 0:
                    records = load_indicator_feature_records(indicator_features_path)
            except UserDemoResultError as exc:
                raise HTTPException(
                    422,
                    _public_error_detail(
                        f"technique evidence is invalid: {exc}", service_settings
                    ),
                ) from exc
        try:
            result = build_technique_assessment(
                job.get("summary") or {},
                records,
                registry_path=service_settings.resolved_technique_registry,
            )
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
        return result

    @app.get("/v1/jobs/{job_id}/artifacts/{artifact_name}")
    def get_artifact(
        job_id: str,
        artifact_name: str,
        _: None = Depends(authorize),
    ) -> FileResponse:
        if artifact_name not in ALLOWED_ARTIFACTS:
            raise HTTPException(404, "artifact not found")
        job = db.get_job(job_id)
        if job is None:
            raise HTTPException(404, "job not found")
        if job["status"] != "succeeded":
            raise HTTPException(409, "job has not succeeded")
        artifact = Path(job["output_dir"]) / artifact_name
        if not artifact.exists():
            raise HTTPException(404, "artifact not found")
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
