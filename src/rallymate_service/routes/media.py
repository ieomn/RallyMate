"""Media, assessment, trajectory, pose, advice-evidence, and artifact routes."""
from __future__ import annotations

from contextlib import ExitStack
import json
from pathlib import Path

from fastapi import Depends, FastAPI, HTTPException, Query, Request, Response
from fastapi.responses import FileResponse, JSONResponse

from rallymate_scoring.technique_assessment import TechniqueRegistrySnapshotError
from rallymate_scoring.technique_registry import TechniqueRegistryError
from rallymate_service.advice_context import AdviceContextTooLarge, AdviceTechnique
from rallymate_service.api_redaction import (
    ALLOWED_ARTIFACTS,
    INLINE_PREVIEW_ARTIFACTS,
    _public_error_detail,
    _public_url,
)
from rallymate_service.mesh_geometry import (
    DEFAULT_MAX_FACES,
    DEFAULT_MAX_FRAMES,
    MAX_ALLOWED_FACES,
    MAX_ALLOWED_FRAMES,
    MeshGeometryUnavailable,
)
from rallymate_service.mesh_preview import MESH_ARTIFACTS
from rallymate_service.original_video import OriginalVideoUnavailable
from rallymate_service.original_video_response import OriginalVideoResponse
from rallymate_service.pose_request_diagnostics import pose_diagnostic_stage, record_pose_phase
from rallymate_service.report_compatibility import (
    DEFAULT_REPORT_CONTRACT,
    ReportContract,
    project_report,
)
from rallymate_service.routes import ApiRouteContext
from rallymate_service.user_demo import UserDemoResultError
from rallymate_vision.pose_playback import PosePlaybackError, PosePlaybackTransport
from rallymate_vision.postprocess_resources import PostprocessBudget, PostprocessResourceLimit
from rallymate_vision.trajectory import TrajectoryExtractionError


def install_media_routes(app: FastAPI, ctx: ApiRouteContext) -> None:
    api = ctx.api_mod
    service_settings = ctx.service_settings
    authorize = ctx.authorize

    @app.get("/v1/jobs/{job_id}/mesh-preview")
    def get_mesh_preview(job_id: str, response: Response, _: None = Depends(authorize)) -> dict:
        job = ctx.db.get_job(job_id)
        if job is None:
            raise HTTPException(404, "job not found")
        media = api.mesh_preview_descriptor(job, service_settings.public_base_url)
        response.headers["Cache-Control"] = "private, no-store"
        return {"job_id": job_id, "status": api.mesh_preview_status(job, media), "sam3d_mesh": media}

    @app.get("/v1/jobs/{job_id}/mesh-geometry")
    def get_mesh_geometry(
        job_id: str,
        response: Response,
        start_ms: int | None = Query(None, ge=0),
        end_ms: int | None = Query(None, ge=0),
        max_frames: int = Query(DEFAULT_MAX_FRAMES, ge=1, le=MAX_ALLOWED_FRAMES),
        max_faces: int = Query(DEFAULT_MAX_FACES, ge=100, le=MAX_ALLOWED_FACES),
        _: None = Depends(authorize),
    ) -> dict:
        job = ctx.db.get_job(job_id)
        if job is None:
            raise HTTPException(404, "job not found")
        try:
            payload = api.build_mesh_geometry_payload(
                job,
                start_ms=start_ms,
                end_ms=end_ms,
                max_frames=max_frames,
                max_faces=max_faces,
            )
        except MeshGeometryUnavailable as exc:
            raise HTTPException(
                exc.status_code,
                detail={"code": exc.code, "message": str(exc)},
            ) from None
        response.headers["Cache-Control"] = "private, no-store"
        return payload

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
        job = ctx.db.get_job(job_id)
        if job is None:
            raise HTTPException(404, "job not found")
        if job["status"] not in {"running", "succeeded"}:
            ctx.live_trajectory_cache.discard(job_id)
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
                payload = ctx.live_trajectory_cache.preview(
                    job,
                    frames_path,
                    sample_limit=sample_limit,
                    prediction_horizon_ms=prediction_horizon_ms,
                )
                response.headers["Cache-Control"] = "no-store"
                return payload
            ctx.live_trajectory_cache.discard(job_id)
            stat = frames_path.stat()
            cache_key = (
                str(frames_path),
                stat.st_mtime_ns,
                stat.st_size,
                job["status"],
                sample_limit,
                prediction_horizon_ms,
            )
            with ctx.trajectory_cache_lock:
                payload = ctx.trajectory_cache.get(cache_key)
                if payload is None:
                    payload = api.build_trajectory_preview(
                        frames_path,
                        sample_limit=sample_limit,
                        prediction_horizon_ms=prediction_horizon_ms,
                        allow_partial=job["status"] == "running",
                    )
                    payload["job_id"] = job_id
                    payload["source"]["frames_path"] = "frames.jsonl"
                    ctx.trajectory_cache[cache_key] = payload
                    while len(ctx.trajectory_cache) > 6:
                        ctx.trajectory_cache.popitem(last=False)
                ctx.trajectory_cache.move_to_end(cache_key)
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
        job = ctx.db.get_job(job_id)
        if job is None:
            raise HTTPException(404, "job not found")
        if job["status"] != "succeeded":
            raise HTTPException(409, "tennis assessment requires a succeeded job")
        response.headers["Cache-Control"] = "no-store"
        with ctx.heavy_report_slot():
            return api.build_tennis_assessment(
                ctx.summary_with_actions(job), Path(job["output_dir"]), job_id=job_id
            )

    @app.get("/v1/jobs/{job_id}/swing-kinematics")
    def get_swing_kinematics(job_id: str, response: Response, _: None = Depends(authorize)) -> dict:
        job = ctx.db.get_job(job_id)
        if job is None:
            raise HTTPException(404, "job not found")
        if job["status"] != "succeeded":
            raise HTTPException(409, "swing measurements require a succeeded job")
        response.headers["Cache-Control"] = "no-store"
        with ctx.heavy_report_slot():
            return api.build_swing_kinematics(Path(job["output_dir"]), job.get("summary") or {})

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
        with pose_diagnostic_stage(request, "db_get_job"):
            job = ctx.db.get_job(job_id)
        if job is None:
            raise HTTPException(404, "job not found")
        if job["status"] not in {"running", "succeeded"}:
            raise HTTPException(409, "pose playback requires a running or succeeded job")
        frames_path = Path(job["output_dir"]) / "frames.jsonl"
        primary_path = Path(job["output_dir"]) / "primary-player.jsonl"
        if not frames_path.is_file():
            raise HTTPException(
                409,
                {
                    "code": "pose_playback_requires_frames_artifact",
                    "required_artifact": "frames.jsonl",
                },
            )
        try:
            def signature(path: Path) -> tuple:
                stat = path.stat() if path.is_file() else None
                return (str(path), stat.st_mtime_ns, stat.st_size) if stat else (str(path), None, None)

            cache_key = (
                signature(frames_path),
                signature(primary_path),
                signature(frames_path.with_name("summary.json")),
                job["status"],
                start_ms,
                duration_ms,
                sample_limit,
            )
            record_pose_phase(request, "pose_lock_wait")
            with ctx.pose_cache_lock:
                payload = ctx.pose_cache.get(cache_key)
                record_pose_phase(request, "pose_lock_acquired", cache_hit=payload is not None)
                if payload is None:
                    with pose_diagnostic_stage(request, "build"):
                        payload = api.build_pose_playback(
                            frames_path,
                            primary_timeline_path=primary_path,
                            start_ms=start_ms,
                            duration_ms=duration_ms,
                            sample_limit=sample_limit,
                            allow_partial=job["status"] == "running",
                        )
                    payload["job_id"] = job_id
                    ctx.pose_cache[cache_key] = payload
                    while len(ctx.pose_cache) > 16:
                        ctx.pose_cache.popitem(last=False)
                ctx.pose_cache.move_to_end(cache_key)
            response.headers["Cache-Control"] = "no-store"
            record_pose_phase(request, "route_return")
            return api.pose_playback_transport(payload, pose_transport)
        except (PosePlaybackError, OSError) as exc:
            raise HTTPException(
                422,
                {
                    "code": "pose_playback_artifact_invalid",
                    "message": "Pose playback source could not be verified.",
                },
            ) from exc

    @app.get("/v1/jobs/{job_id}/technique-assessment")
    def get_technique_assessment(
        job_id: str,
        report_contract: ReportContract = Query(DEFAULT_REPORT_CONTRACT),
        _: None = Depends(authorize),
    ) -> dict:
        report_budget = PostprocessBudget(
            max_seconds=ctx.report_delivery_cache.preparation_timeout_seconds
        )

        def check_report_budget(*, force=False):
            try:
                report_budget.check(force=force)
            except PostprocessResourceLimit as exc:
                raise HTTPException(
                    503, {"code": "report_resource_limit", "message": str(exc)}
                ) from exc

        with ExitStack() as evidence_context:
            job = ctx.db.get_job(job_id)
            if job is None:
                raise HTTPException(404, "job not found")
            if job["status"] != "succeeded":
                raise HTTPException(409, "job has not succeeded")
            indicator_features_path = Path(job["output_dir"]) / "indicator-features.jsonl"
            check_report_budget(force=True)
            summary = ctx.summary_with_actions(job)
            check_report_budget(force=True)
            records: list[dict] = []
            if indicator_features_path.is_file():
                try:
                    if indicator_features_path.stat().st_size > 0:
                        records = evidence_context.enter_context(
                            ctx.report_records(
                                indicator_features_path, summary, check_report_budget
                            )
                        )
                except UserDemoResultError as exc:
                    raise HTTPException(
                        422,
                        _public_error_detail(
                            f"technique evidence is invalid: {exc}", service_settings
                        ),
                    ) from exc
            try:
                result = api.build_technique_assessment(
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
        expected_artifact_sha256: str | None = Query(
            default=None, pattern=r"^[0-9a-fA-F]{64}$"
        ),
        _: None = Depends(authorize),
    ) -> JSONResponse:
        headers = {"Cache-Control": "private, no-store"}
        job = ctx.db.get_job(job_id)
        if job is None:
            raise HTTPException(404, "job not found", headers=headers)
        if job["status"] != "succeeded" or job.get("cancel_requested"):
            raise HTTPException(409, "job has not succeeded or was cancelled", headers=headers)
        return JSONResponse(
            api.load_foot_crossing_candidates(
                service_settings.data_root,
                job,
                expected_artifact_sha256=expected_artifact_sha256,
            ),
            headers=headers,
        )

    @app.get("/v1/jobs/{job_id}/advice-evidence")
    def get_advice_evidence(
        job_id: str,
        technique: AdviceTechnique = Query(...),
        _: None = Depends(authorize),
    ) -> JSONResponse:
        assessment = get_technique_assessment(job_id, report_contract="current")
        try:
            return JSONResponse(api.project_advice_context(job_id, assessment, technique))
        except AdviceContextTooLarge as exc:
            raise HTTPException(
                422,
                {
                    "code": "advice_context_limit",
                    "message": "Complete advice context exceeds the response limit.",
                },
            ) from exc

    @app.get("/v1/jobs/{job_id}/original-video")
    def get_original_video(job_id: str, _: None = Depends(authorize)) -> Response:
        job = ctx.db.get_job(job_id)
        if job is None:
            raise HTTPException(404, "job not found")
        try:
            original = api.verify_original_video(job, service_settings)
        except OriginalVideoUnavailable as exc:
            raise HTTPException(409, str(exc)) from exc
        return OriginalVideoResponse(
            original.path,
            filename=f"{job_id}-original{original.path.suffix.lower()}",
            content_disposition_type="inline",
            headers={
                "Cache-Control": "private, no-store",
                "X-Content-Type-Options": "nosniff",
            },
        )

    @app.get("/v1/jobs/{job_id}/artifacts/{artifact_name}")
    def get_artifact(
        job_id: str,
        artifact_name: str,
        report_contract: ReportContract = Query("current"),
        _: None = Depends(authorize),
    ) -> Response:
        if artifact_name not in ALLOWED_ARTIFACTS:
            raise HTTPException(404, "artifact not found")
        job = ctx.db.get_job(job_id)
        if job is None:
            raise HTTPException(404, "job not found")
        if job["status"] != "succeeded":
            raise HTTPException(409, "job has not succeeded")
        if artifact_name in MESH_ARTIFACTS and api.mesh_preview_descriptor(job) is None:
            raise HTTPException(404, "mesh preview is not ready")
        artifact = Path(job["output_dir"]) / artifact_name
        if not artifact.exists():
            raise HTTPException(404, "artifact not found")
        if artifact_name == "summary.json" and report_contract == "legacy-v1":
            try:
                summary = json.loads(artifact.read_text(encoding="utf-8"))
            except (OSError, ValueError) as exc:
                raise HTTPException(422, "summary artifact is invalid") from exc
            return JSONResponse(
                project_report(summary, report_contract),
                headers={
                    "Cache-Control": "private, no-store",
                    "X-RallyMate-Report-Contract": report_contract,
                    "Content-Disposition": f'attachment; filename="{job_id}-summary-legacy-v1.json"',
                },
            )
        return FileResponse(
            artifact,
            filename=f"{job_id}-{artifact_name}",
            content_disposition_type=(
                "inline" if artifact_name in INLINE_PREVIEW_ARTIFACTS else "attachment"
            ),
            headers={"Cache-Control": "private, no-store"},
        )
