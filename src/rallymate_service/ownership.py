"""Request ownership at storage boundaries; internal workers retain their queue API."""
from __future__ import annotations

from contextvars import ContextVar
from pathlib import Path

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.engine import Engine
from sqlalchemy.exc import IntegrityError

from .database import JobDatabase, utc_now
from .models import jobs, upload_owners
from .uploads import UploadStore


current_user_id: ContextVar[str | None] = ContextVar("rallymate_current_user_id", default=None)


def _not_found() -> HTTPException:
    return HTTPException(404, "记录不存在")


def _check_user(user_id: str) -> None:
    if not isinstance(user_id, str) or not user_id.strip():
        raise _not_found()


def _check_explicit_scope(user_id: str) -> None:
    _check_user(user_id)
    actor = current_user_id.get()
    if actor is not None and actor != user_id:
        raise _not_found()


def assert_job_owner(engine: Engine, user_id: str, job_id: str) -> None:
    """Check ownership without fetching or parsing a potentially large report."""
    _check_user(user_id)
    with engine.connect() as connection:
        found = connection.execute(select(jobs.c.id).where(
            jobs.c.id == job_id, jobs.c.user_id == user_id, jobs.c.deleted_at.is_(None),
        )).first()
    if found is None:
        raise _not_found()


def assert_upload_owner(engine: Engine, user_id: str, upload_id: str) -> None:
    _check_user(user_id)
    with engine.connect() as connection:
        owner = connection.execute(select(upload_owners.c.user_id).where(
            upload_owners.c.upload_id == upload_id,
        )).scalar_one_or_none()
        if owner != user_id:
            raise _not_found()
        job = connection.execute(select(jobs.c.user_id, jobs.c.deleted_at).where(
            jobs.c.id == upload_id,
        )).first()
        if job is not None and (job.user_id != user_id or job.deleted_at is not None):
            raise _not_found()


class UserScopedJobDatabase:
    def __init__(self, delegate: JobDatabase, engine: Engine):
        self.delegate = delegate
        self.engine = engine

    def __getattr__(self, name):
        return getattr(self.delegate, name)

    def connect(self):
        return self.delegate.connect()

    def create_job(
        self, job_id: str, original_filename: str, video_path: Path, request_path: Path,
        output_dir: Path, *, max_active_jobs: int | None = None, user_id: str | None = None,
    ) -> dict:
        actor = current_user_id.get()
        if actor is not None:
            if user_id is not None and user_id != actor:
                raise _not_found()
            user_id = actor
        return self.delegate.create_job(job_id, original_filename, video_path, request_path,
            output_dir, max_active_jobs=max_active_jobs, user_id=user_id)

    def get_job(self, job_id: str) -> dict | None:
        actor = current_user_id.get()
        if actor is None:
            return self.delegate.get_job(job_id)
        try:
            assert_job_owner(self.engine, actor, job_id)
        except HTTPException:
            # An owned upload can be between file publication and queue commit.
            # Keep the queue reader's failures visible: the original handoff code
            # retains published files when it cannot establish the commit state.
            try:
                assert_upload_owner(self.engine, actor, job_id)
            except HTTPException:
                return None
        result = self.delegate.get_job(job_id)
        if result is None or result.get("user_id") != actor or result.get("deleted_at") is not None:
            return None
        return result

    def list_jobs(self, limit: int = 20) -> list[dict]:
        actor = current_user_id.get()
        if actor is None:
            return self.delegate.list_jobs(limit=limit)
        with self.engine.connect() as connection:
            ids = connection.execute(select(jobs.c.id).where(
                jobs.c.user_id == actor, jobs.c.deleted_at.is_(None),
            ).order_by(jobs.c.created_at.desc(), jobs.c.id.desc()).limit(max(1, min(limit, 100)))).scalars().all()
        return [job for job_id in ids if (job := self.get_job(job_id)) is not None]

    def list_user_jobs(self, user_id: str, limit: int = 20, offset: int = 0) -> list[dict]:
        """History metadata excludes report JSON, progress JSON and server file paths."""
        _check_explicit_scope(user_id)
        if type(limit) is not int or not 1 <= limit <= 100 or type(offset) is not int or offset < 0:
            raise ValueError("History requires limit between 1 and 100 and nonnegative offset")
        columns = (jobs.c.id, jobs.c.status, jobs.c.original_filename, jobs.c.created_at,
            jobs.c.updated_at, jobs.c.started_at, jobs.c.completed_at, jobs.c.attempts,
            jobs.c.cancel_requested, jobs.c.cancel_requested_at, jobs.c.cancelled_at)
        with self.engine.connect() as connection:
            rows = connection.execute(select(*columns).where(
                jobs.c.user_id == user_id, jobs.c.deleted_at.is_(None),
            ).order_by(jobs.c.created_at.desc(), jobs.c.id.desc()).offset(offset).limit(limit)).mappings().all()
        return [{**dict(row), "cancel_requested": bool(row["cancel_requested"])} for row in rows]

    def delete_owned_job(self, user_id: str, job_id: str) -> dict:
        _check_explicit_scope(user_id)
        with self.delegate.connect() as connection:
            # Use the queue's transaction lock so claiming and deletion cannot race.
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute("SELECT user_id, status, deleted_at FROM jobs WHERE id = ?", (job_id,)).fetchone()
            if row is None or row["user_id"] != user_id or row["deleted_at"] is not None:
                raise _not_found()
            if row["status"] in {"queued", "running"}:
                raise HTTPException(409, "请先取消任务，等待任务停止后再删除")
            now = utc_now()
            connection.execute("UPDATE jobs SET deleted_at = ?, updated_at = ? WHERE id = ?", (now, now, job_id))
            connection.commit()
        return {"id": job_id, "deleted_at": now, "deleted": True}


class UserScopedUploadStore:
    def __init__(self, delegate: UploadStore, engine: Engine):
        self.delegate = delegate
        self.engine = engine

    def __getattr__(self, name):
        return getattr(self.delegate, name)

    def _check(self, upload_id: str) -> None:
        actor = current_user_id.get()
        if actor is not None:
            assert_upload_owner(self.engine, actor, upload_id)

    def _check_directory(self, directory: Path) -> None:
        if current_user_id.get() is None:
            return
        directory = Path(directory).resolve()
        expected = self.delegate.directory(directory.name).resolve()
        if directory.parent != self.delegate.root.resolve() or directory != expected:
            raise _not_found()
        self._check(directory.name)

    def _claim(self, upload_id: str, user_id: str) -> None:
        directory = self.delegate.directory(upload_id)
        try:
            with self.engine.begin() as connection:
                owner = connection.execute(select(upload_owners.c.user_id).where(
                    upload_owners.c.upload_id == upload_id,
                )).scalar_one_or_none()
                if owner is None:
                    existing_job = connection.execute(select(jobs.c.id).where(jobs.c.id == upload_id)).first()
                    # A filesystem session without an owner is historical data, not a claimable UUID.
                    if directory.exists() or directory.is_symlink() or existing_job is not None:
                        raise _not_found()
                    connection.execute(upload_owners.insert().values(
                        upload_id=upload_id, user_id=user_id, created_at=utc_now(),
                    ))
                elif owner != user_id:
                    raise _not_found()
        except IntegrityError:
            # Another request can win this UUID; only the same owner may retry it.
            assert_upload_owner(self.engine, user_id, upload_id)
        assert_upload_owner(self.engine, user_id, upload_id)

    def create(self, upload_id: str, filename: str, size: int, options: dict) -> dict:
        actor = current_user_id.get()
        if actor is not None:
            self._claim(upload_id, actor)
        return self.delegate.create(upload_id, filename, size, options)

    def directory(self, upload_id: str) -> Path:
        directory = self.delegate.directory(upload_id)
        self._check_directory(directory)
        return directory

    def read_metadata(self, directory: Path) -> dict:
        self._check_directory(directory)
        return self.delegate.read_metadata(directory)

    def read(self, directory: Path) -> dict:
        self._check_directory(directory)
        return self.delegate.read(directory)

    def save(self, directory: Path, data: dict):
        self._check_directory(directory)
        return self.delegate.save(directory, data)

    def status(self, upload_id: str) -> dict:
        self.directory(upload_id)
        return self.delegate.status(upload_id)

    def put(self, upload_id: str, index: int, content: bytes, digest: str, *, compact_ack: bool = False) -> dict:
        self.directory(upload_id)
        return self.delegate.put(upload_id, index, content, digest, compact_ack=compact_ack)

    def complete(self, upload_id: str, enqueue, existing_job) -> dict:
        self.directory(upload_id)
        return self.delegate.complete(upload_id, enqueue, existing_job)

    def remove_parts(self, directory: Path):
        self._check_directory(directory)
        return self.delegate.remove_parts(directory)
