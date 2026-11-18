"""Small owner-filtered history and record removal alongside existing reports."""
from fastapi import HTTPException, Query, Request
from sqlalchemy import func, select

from .models import jobs


def install_record_routes(app, database):
    def owner(request: Request) -> str:
        session = getattr(request.state, "account_session", None)
        if session is None:
            raise HTTPException(401, "请先登录。")
        return session["user"]["id"]

    @app.get("/api/records")
    def personal_history(request: Request, limit: int = Query(20, ge=1, le=100),
                         offset: int = Query(0, ge=0, le=1000000)):
        user_id = owner(request)
        records = database.list_user_jobs(user_id, limit=limit, offset=offset)
        with database.engine.connect() as connection:
            total = connection.scalar(select(func.count()).select_from(jobs).where(
                jobs.c.user_id == user_id, jobs.c.deleted_at.is_(None)))
        return {"items": records, "total": total, "limit": limit, "offset": offset}

    @app.delete("/v1/jobs/{job_id}")
    def delete_record(job_id: str, request: Request):
        return database.delete_owned_job(owner(request), job_id)
