"""Public response redaction, production API key validation, and job projections."""
from __future__ import annotations

from pathlib import Path
import re
import uuid

from rallymate_service.config import ServiceSettings
from rallymate_service.mesh_preview import (
    MESH_ARTIFACTS,
    MESH_VIDEOS,
    mesh_preview_descriptor,
    mesh_preview_status,
)
from rallymate_service.report_compatibility import (
    DEFAULT_REPORT_CONTRACT,
    ReportContract,
    project_report,
)


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
_PATH_TOKEN_PATTERN = re.compile(
    r'''(?<![A-Za-z0-9])(?:[A-Za-z]:[\\/]|/|\\\\)[^,;"']*(?=$|[,;"'])'''
)
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


def _redact_public_value(value, key: str | None = None):
    """Remove worker-local paths and client network identifiers from public JSON."""

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
    """Redact local paths embedded in errors returned to a public client."""

    if not _should_redact_public(settings):
        return detail
    if isinstance(detail, str):
        return _redact_readiness_reason(detail)
    if isinstance(detail, dict):
        return {
            key: _redact_public_value(value, str(key))
            for key, value in detail.items()
        }
    if isinstance(detail, list):
        return _redact_public_value(detail)
    if isinstance(detail, tuple):
        return [_redact_public_value(item) for item in detail]
    return _redact_public_value(detail)


def _production_api_key_reasons(api_key: str | None) -> list[str]:
    """Return actionable readiness reasons for an unsafe production key."""

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
        public["progress"] = _redact_public_value(public.get("progress"))
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
