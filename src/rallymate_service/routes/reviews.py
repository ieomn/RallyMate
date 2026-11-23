"""Human technical-review endpoints separated from automated scoring."""
from __future__ import annotations

from fastapi import Depends, FastAPI, HTTPException, Request, Response
from starlette.concurrency import run_in_threadpool

from rallymate_service.routes import ApiRouteContext
from rallymate_service.technical_review import (
    MAX_BODY_BYTES,
    TechnicalReviewError,
    parse_review_input,
)


def install_review_routes(app: FastAPI, ctx: ApiRouteContext) -> None:
    authorize = ctx.authorize

    def technical_review_job(job_id: str) -> dict:
        job = ctx.db.get_job(job_id)
        if job is None:
            raise HTTPException(404, "job not found")
        return job

    @app.get("/v1/jobs/{job_id}/technical-review")
    def get_technical_review(
        job_id: str, response: Response, _: None = Depends(authorize)
    ) -> dict:
        response.headers["Cache-Control"] = "no-store"
        try:
            return ctx.technical_reviews.get(technical_review_job(job_id))
        except TechnicalReviewError as exc:
            raise HTTPException(exc.status, exc.detail()) from exc

    @app.post("/v1/jobs/{job_id}/technical-review")
    async def save_technical_review(
        job_id: str,
        request: Request,
        response: Response,
        _: None = Depends(authorize),
    ) -> dict:
        response.headers["Cache-Control"] = "no-store"
        try:
            body = bytearray()
            async for chunk in request.stream():
                body.extend(chunk)
                if len(body) > MAX_BODY_BYTES:
                    raise TechnicalReviewError("review_too_large", "评审内容过长。", 413)
            submission = parse_review_input(bytes(body))
            job = await run_in_threadpool(technical_review_job, job_id)
            return await run_in_threadpool(ctx.technical_reviews.save, job, submission)
        except TechnicalReviewError as exc:
            raise HTTPException(exc.status, exc.detail()) from exc

    @app.get("/v1/jobs/{job_id}/technical-review/export")
    def export_technical_review(
        job_id: str, response: Response, _: None = Depends(authorize)
    ) -> dict:
        response.headers["Cache-Control"] = "no-store"
        try:
            result = ctx.technical_reviews.get(technical_review_job(job_id), export=True)
            response.headers["Content-Disposition"] = 'attachment; filename="technical-reviews.json"'
            return result
        except TechnicalReviewError as exc:
            raise HTTPException(exc.status, exc.detail()) from exc
