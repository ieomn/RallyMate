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
    Request,
    UploadFile,
)
from fastapi.responses import FileResponse
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles

from rallymate_scoring.granularity import static_model_capability
from rallymate_service.config import ServiceSettings, load_settings
from rallymate_service.database import JobDatabase
from rallymate_service.user_demo import (
    UserDemoResultError,
    build_user_demo_result,
    load_indicator_feature_records,
)
from rallymate_vision.quality import probe_video
from rallymate_vision.pipeline import (
    load_pipeline_calibrations,
    load_trusted_runtime_binding_registry,
)


ALLOWED_EXTENSIONS = {".mp4", ".mov", ".m4v", ".avi", ".mkv"}
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


def _public_job(job: dict) -> dict:
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
    if job.get("status") == "succeeded":
        summary = job.get("summary") or {}
        public["scoring_state"] = summary.get("scoring_state")
        public["artifact_urls"] = {
            name: f"/v1/jobs/{job['id']}/artifacts/{name}"
            for name in sorted(ALLOWED_ARTIFACTS)
            if (Path(job["output_dir"]) / name).exists()
        }
        public["demo_result_url"] = f"/v1/jobs/{job['id']}/demo-result"
    else:
        public["artifact_urls"] = {}
        public["demo_result_url"] = None
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
    service_settings.ensure_directories()
    db = database or JobDatabase(service_settings.database_path)

    @asynccontextmanager
    async def lifespan(_: FastAPI):
        db.initialize()
        yield

    app = FastAPI(
        title="RallyMate Vision API",
        version="1.2.0",
        description=(
            "Upload tennis video, run persistent-GPU YOLO Detect + replaceable Pose inference, "
            "and return the 13-indicator Beta training evaluation while preserving the formal "
            "coach-calibration boundary."
        ),
        lifespan=lifespan,
    )
    app.state.settings = service_settings
    app.state.database = db
    app.mount("/assets", StaticFiles(directory=DEMO_ASSETS_DIR), name="assets")

    def authorize(authorization: str | None = Header(default=None)) -> None:
        if service_settings.api_key is None:
            return
        expected = f"Bearer {service_settings.api_key}"
        if authorization is None or not secrets.compare_digest(authorization, expected):
            raise HTTPException(401, "missing or invalid bearer token")

    @app.get("/health/live")
    def liveness() -> dict:
        return {"status": "ok"}

    @app.get("/health/ready")
    def readiness(_: None = Depends(authorize)) -> dict:
        reasons = []
        registry_authority = None
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
            "reasons": reasons,
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
        registry_authority = service_settings.resolved_scoring_registry_authority
        registry_path = registry_authority.path
        registry = registry_authority.payload
        payload["minimum_scoring_loop"] = {
            "registry_path": str(registry_path),
            "registry_version": registry["registry_version"],
            "registry_lifecycle": {
                "authority_version": registry_authority.authority_version,
                "manifest_path": str(registry_authority.manifest_path),
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
            "trusted_promotion_ledger_path": (
                str(service_settings.scoring_trusted_promotion_ledger)
                if service_settings.scoring_trusted_promotion_ledger is not None
                else None
            ),
            "trusted_runtime_profile_bindings_configured": (
                service_settings.scoring_trusted_runtime_profile_bindings
                is not None
            ),
            "trusted_runtime_profile_bindings_path": (
                str(service_settings.scoring_trusted_runtime_profile_bindings)
                if service_settings.scoring_trusted_runtime_profile_bindings
                is not None
                else None
            ),
            "runtime_view_evidence_dir_configured": (
                service_settings.scoring_runtime_view_evidence_dir is not None
            ),
            "runtime_view_evidence_dir": (
                str(service_settings.scoring_runtime_view_evidence_dir)
                if service_settings.scoring_runtime_view_evidence_dir is not None
                else None
            ),
        }
        return payload

    @app.get("/v1/jobs")
    def list_jobs(limit: int = 20, _: None = Depends(authorize)) -> dict:
        jobs = db.list_jobs(limit=limit)
        return {"items": [_public_job(job) for job in jobs], "count": len(jobs)}

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
        registry_authority = service_settings.resolved_scoring_registry_authority
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
                raise HTTPException(422, f"video cannot be decoded: {exc}") from exc
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
        return _public_job(job)

    @app.get("/v1/jobs/{job_id}")
    def get_job(job_id: str, _: None = Depends(authorize)) -> dict:
        job = db.get_job(job_id)
        if job is None:
            raise HTTPException(404, "job not found")
        return _public_job(job)

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
            result = build_user_demo_result(job.get("summary") or {}, records)
        except UserDemoResultError as exc:
            raise HTTPException(500, f"demo result is unavailable: {exc}") from exc
        public_job = _public_job(job)
        result["artifact_urls"] = public_job["artifact_urls"]
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
        )

    return app


app = create_app()
