"""Health, discovery metadata, job listing, and maintenance routes."""
from __future__ import annotations

from fastapi import Depends, FastAPI, HTTPException, Query, Request, Response

from rallymate_scoring.registry_lifecycle import RegistryLifecycleError
from rallymate_scoring.technique_registry import TechniqueRegistryError
from rallymate_service.api_redaction import (
    _production_api_key_reasons,
    _public_error_detail,
    _public_job,
    _redact_readiness_reason,
    _should_redact_public,
)
from rallymate_service.config import ServiceConfigError
from rallymate_service.report_compatibility import DEFAULT_REPORT_CONTRACT, ReportContract
from rallymate_service.routes import ApiRouteContext
from rallymate_service.uploads import CHUNK_BYTES


def install_health_routes(app: FastAPI, ctx: ApiRouteContext) -> None:
    api = ctx.api_mod
    service_settings = ctx.service_settings
    authorize = ctx.authorize

    @app.get("/health/live")
    def liveness() -> dict:
        return {
            "status": "ok",
            "admission": {
                "max_active_jobs": service_settings.max_active_jobs,
                "accept_new_jobs": service_settings.accept_new_jobs and not ctx.maintenance.is_set(),
            },
        }

    @app.get("/v1/meta")
    def api_meta(_: None = Depends(authorize)) -> dict:
        """Stable discovery document for a separately hosted web client."""

        try:
            catalog = api.technique_catalog(service_settings.resolved_technique_registry)
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
            "api_version": api.API_VERSION,
            "compatibility_version": api.API_COMPATIBILITY_VERSION,
            "environment": service_settings.environment,
            "deployment": ctx.deployment_identity.public_status(),
            "public_base_url": service_settings.public_base_url,
            "authentication": {
                "scheme": "account_session",
                "required": True,
                "maintenance_bearer": service_settings.api_key is not None,
                "browser_secret_injection": "reverse_proxy_or_same_origin_session",
            },
            "capabilities": {
                "async_video_jobs": True,
                "resumable_uploads": {
                    "chunk_bytes": CHUNK_BYTES,
                    "default_chunk_bytes": ctx.uploads.chunk_bytes,
                    "acknowledgement": "chunk-v1",
                    "parallel_chunks": 2,
                    "expires_after_hours": 24,
                },
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
                "technique_assessment_endpoint_template": "/v1/jobs/{job_id}/technique-assessment",
                "technique_catalog_endpoint": "/v1/techniques",
            },
        }

    @app.get("/v1/techniques")
    def technique_capabilities(_: None = Depends(authorize)) -> dict:
        """Return the qualitative action catalog extracted from the five rules docs."""

        try:
            return api.technique_catalog(service_settings.resolved_technique_registry)
        except TechniqueRegistryError as exc:
            raise HTTPException(
                503,
                _public_error_detail(f"technique registry unavailable: {exc}", service_settings),
            ) from exc

    @app.get("/health/ready")
    def readiness(response: Response, _: None = Depends(authorize)) -> dict:
        reasons = []
        registry_authority = None
        if (
            isinstance(service_settings.environment, str)
            and service_settings.environment.strip().lower() == "production"
        ):
            reasons.extend(_production_api_key_reasons(service_settings.api_key))
        try:
            service_settings.validate_license()
            ctx.db.initialize()
            registry_authority = service_settings.resolved_scoring_registry_authority
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
                api.load_pipeline_calibrations(
                    service_settings.scoring_calibration_assets,
                    trusted_ledger_path=service_settings.scoring_trusted_promotion_ledger,
                    feasibility_registry=registry_authority.payload,
                )
            except Exception as exc:
                reasons.append(f"scoring calibration validation failed: {exc}")
        if service_settings.pose_config is not None and not service_settings.pose_config.exists():
            reasons.append(f"pose_config is missing: {service_settings.pose_config}")
        executor = ctx.execution_status()
        if not ctx.execution_admits(executor):
            reasons.append("Inference executor is not available; uploads and existing reports are preserved.")
            response.status_code = 503
        if ctx.maintenance.is_set():
            reasons.append("Local service maintenance is draining the executor; submissions are paused.")
            response.status_code = 503
        response.headers["Cache-Control"] = "no-store"
        return {
            "status": "ready" if not reasons else "not_ready",
            "executor": executor,
            "inference_ready": None if executor["status"] == "unmonitored" else executor["available"],
            "reasons": [_redact_readiness_reason(reason) for reason in reasons],
            "queue": ctx.db.count_by_status(),
            "admission": {
                "max_active_jobs": service_settings.max_active_jobs,
                "accept_new_jobs": service_settings.accept_new_jobs
                and not ctx.maintenance.is_set()
                and ctx.execution_admits(executor),
            },
            "maintenance": ctx.maintenance.is_set(),
            "environment": service_settings.environment,
            "model_license_ack": service_settings.model_license_ack,
            "pose_preset": service_settings.pose_preset,
            "pose_backend": service_settings.pose_backend,
            "pose_profile": service_settings.pose_profile,
            "pose_native_keypoint_format": service_settings.pose_native_keypoint_format,
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
        return {
            "service": "RallyMate Vision API",
            "frontend": "RallyMate 动作识别工作台",
            "message": "Use the web frontend on the configured public port.",
        }

    @app.get("/v1/model-capabilities")
    def model_capabilities(_: None = Depends(authorize)) -> dict:
        payload = api.static_model_capability()
        try:
            registry_authority = service_settings.resolved_scoring_registry_authority
        except (FileNotFoundError, RegistryLifecycleError, ServiceConfigError, ValueError) as exc:
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
            "indicator_ids": sorted(item["indicator_id"] for item in registry["indicators"]),
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
                service_settings.scoring_trusted_runtime_profile_bindings is not None
            ),
            "trusted_runtime_profile_bindings_path": None,
            "runtime_view_evidence_dir_configured": (
                service_settings.scoring_runtime_view_evidence_dir is not None
            ),
            "runtime_view_evidence_dir": None,
        }
        try:
            catalog = api.technique_catalog(service_settings.resolved_technique_registry)
            payload["technique_registry"] = {
                "registry_id": catalog["registry_id"],
                "registry_version": catalog["registry_version"],
                "registry_sha256": catalog["registry_sha256"],
                "technique_count": len(catalog["techniques"]),
                "families": sorted({str(item["family"]) for item in catalog["techniques"]}),
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
        jobs = ctx.db.list_jobs(limit=limit)
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

    @app.post("/internal/maintenance/stop-executor", include_in_schema=False)
    def stop_executor_for_maintenance(
        request: Request, response: Response, _: None = Depends(authorize)
    ) -> dict:
        if not service_settings.api_key:
            raise HTTPException(
                503,
                {
                    "code": "maintenance_auth_not_configured",
                    "message": "Local maintenance requires API authentication.",
                },
            )
        if request.client is None or request.client.host not in {"127.0.0.1", "::1"}:
            raise HTTPException(403, "local maintenance only")
        if ctx.executor_shutdown is None:
            raise HTTPException(404, "supervised executor not configured")
        with ctx.admission_lock:
            was_paused = ctx.maintenance.is_set()
            ctx.maintenance.set()
            try:
                queue = ctx.db.count_by_status()
                if queue.get("running", 0) or queue.get("queued", 0):
                    raise ValueError("Analysis work is active or queued")
                ctx.executor_shutdown()
            except ValueError:
                if not was_paused:
                    ctx.maintenance.clear()
                raise HTTPException(
                    409,
                    {
                        "code": "executor_busy",
                        "message": "Current work must finish before maintenance.",
                    },
                )
            except Exception:
                raise HTTPException(
                    503,
                    {
                        "code": "executor_stop_unconfirmed",
                        "message": "Executor shutdown could not be confirmed.",
                    },
                )
        executor = ctx.execution_status()
        stopped = executor["status"] == "stopped"
        response.status_code = 200 if stopped else 202
        response.headers["Cache-Control"] = "no-store"
        return {
            "status": "stopped" if stopped else "draining",
            "executor_stopped": stopped,
            "executor": executor,
        }
