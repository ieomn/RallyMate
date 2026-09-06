from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Iterator


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


class JobDatabase:
    """A durable SQLite queue suitable for one GPU node and one worker."""

    def __init__(self, path: str | Path):
        self.path = Path(path).resolve()
        self.path.parent.mkdir(parents=True, exist_ok=True)

    @contextmanager
    def connect(self) -> Iterator[sqlite3.Connection]:
        connection = sqlite3.connect(str(self.path), timeout=30)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA journal_mode=WAL")
        connection.execute("PRAGMA foreign_keys=ON")
        try:
            yield connection
        finally:
            connection.close()

    def initialize(self) -> None:
        with self.connect() as connection:
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS jobs (
                    id TEXT PRIMARY KEY,
                    status TEXT NOT NULL CHECK (
                        status IN ('queued', 'running', 'succeeded', 'failed')
                    ),
                    original_filename TEXT NOT NULL,
                    video_path TEXT NOT NULL,
                    request_path TEXT NOT NULL,
                    output_dir TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    started_at TEXT,
                    completed_at TEXT,
                    lease_expires_at TEXT,
                    attempts INTEGER NOT NULL DEFAULT 0,
                    worker_id TEXT,
                    progress_json TEXT,
                    summary_json TEXT,
                    error TEXT
                )
                """
            )
            columns = {
                row["name"]
                for row in connection.execute("PRAGMA table_info(jobs)").fetchall()
            }
            if "progress_json" not in columns:
                connection.execute("ALTER TABLE jobs ADD COLUMN progress_json TEXT")
            connection.execute(
                "CREATE INDEX IF NOT EXISTS idx_jobs_status_created "
                "ON jobs(status, created_at)"
            )
            connection.commit()

    def create_job(
        self,
        job_id: str,
        original_filename: str,
        video_path: Path,
        request_path: Path,
        output_dir: Path,
    ) -> dict:
        now = utc_now()
        with self.connect() as connection:
            connection.execute(
                """
                INSERT INTO jobs (
                    id, status, original_filename, video_path, request_path,
                    output_dir, created_at, updated_at
                ) VALUES (?, 'queued', ?, ?, ?, ?, ?, ?)
                """,
                (
                    job_id,
                    original_filename,
                    str(video_path),
                    str(request_path),
                    str(output_dir),
                    now,
                    now,
                ),
            )
            connection.commit()
        return self.get_job(job_id)

    def get_job(self, job_id: str) -> dict | None:
        with self.connect() as connection:
            row = connection.execute(
                "SELECT * FROM jobs WHERE id = ?", (job_id,)
            ).fetchone()
        if row is None:
            return None
        result = dict(row)
        if result.get("progress_json"):
            result["progress"] = json.loads(result.pop("progress_json"))
        else:
            result.pop("progress_json", None)
            result["progress"] = {
                "phase": result["status"],
                "percent": 100 if result["status"] == "succeeded" else 0,
            }
        if result.get("summary_json"):
            result["summary"] = json.loads(result.pop("summary_json"))
        else:
            result.pop("summary_json", None)
            result["summary"] = None
        return result

    def list_jobs(self, limit: int = 20) -> list[dict]:
        with self.connect() as connection:
            rows = connection.execute(
                "SELECT id FROM jobs ORDER BY created_at DESC LIMIT ?",
                (max(1, min(limit, 100)),),
            ).fetchall()
        return [job for row in rows if (job := self.get_job(row["id"])) is not None]

    def claim_next(
        self,
        worker_id: str,
        lease_seconds: int,
        max_attempts: int,
    ) -> dict | None:
        now = datetime.now(timezone.utc)
        lease = (now + timedelta(seconds=lease_seconds)).isoformat()
        now_text = now.isoformat()
        with self.connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            connection.execute(
                """
                UPDATE jobs
                SET status = 'queued', worker_id = NULL, lease_expires_at = NULL,
                    updated_at = ?, error = 'worker lease expired; retrying'
                WHERE status = 'running' AND lease_expires_at < ?
                    AND attempts < ?
                """,
                (now_text, now_text, max_attempts),
            )
            connection.execute(
                """
                UPDATE jobs
                SET status = 'failed', completed_at = ?, updated_at = ?,
                    error = 'worker lease expired and retry budget was exhausted'
                WHERE status = 'running' AND lease_expires_at < ?
                    AND attempts >= ?
                """,
                (now_text, now_text, now_text, max_attempts),
            )
            row = connection.execute(
                """
                SELECT id FROM jobs
                WHERE status = 'queued' AND attempts < ?
                ORDER BY created_at
                LIMIT 1
                """,
                (max_attempts,),
            ).fetchone()
            if row is None:
                connection.commit()
                return None
            connection.execute(
                """
                UPDATE jobs
                SET status = 'running', worker_id = ?, started_at = COALESCE(started_at, ?),
                    updated_at = ?, lease_expires_at = ?, attempts = attempts + 1,
                    progress_json = ?, error = NULL
                WHERE id = ? AND status = 'queued'
                """,
                (
                    worker_id,
                    now_text,
                    now_text,
                    lease,
                    json.dumps(
                        {
                            "phase": "inference",
                            "percent": 0,
                            "processed_frames": 0,
                            "total_frames": None,
                            "message": "推理任务已开始",
                        },
                        ensure_ascii=False,
                    ),
                    row["id"],
                ),
            )
            connection.commit()
        return self.get_job(row["id"])

    def update_progress(
        self,
        job_id: str,
        progress: dict,
        lease_seconds: int,
    ) -> None:
        now = datetime.now(timezone.utc)
        lease = (now + timedelta(seconds=lease_seconds)).isoformat()
        with self.connect() as connection:
            connection.execute(
                """
                UPDATE jobs
                SET progress_json = ?, updated_at = ?, lease_expires_at = ?
                WHERE id = ? AND status = 'running'
                """,
                (
                    json.dumps(progress, ensure_ascii=False),
                    now.isoformat(),
                    lease,
                    job_id,
                ),
            )
            connection.commit()

    def mark_succeeded(self, job_id: str, summary: dict) -> None:
        now = utc_now()
        with self.connect() as connection:
            connection.execute(
                """
                UPDATE jobs
                SET status = 'succeeded', summary_json = ?, completed_at = ?,
                    updated_at = ?, lease_expires_at = NULL, error = NULL,
                    progress_json = ?
                WHERE id = ? AND status = 'running'
                """,
                (
                    json.dumps(summary, ensure_ascii=False),
                    now,
                    now,
                    json.dumps(
                        {
                            "phase": "completed",
                            "percent": 100,
                            "processed_frames": summary.get("processing", {}).get(
                                "processed_frames"
                            ),
                            "total_frames": summary.get("processing", {}).get(
                                "processed_frames"
                            ),
                            "message": "推理与产物校验完成",
                        },
                        ensure_ascii=False,
                    ),
                    job_id,
                ),
            )
            connection.commit()

    def mark_failed(self, job_id: str, error: str) -> None:
        now = utc_now()
        with self.connect() as connection:
            connection.execute(
                """
                UPDATE jobs
                SET status = 'failed', error = ?, completed_at = ?, updated_at = ?,
                    lease_expires_at = NULL, progress_json = ?
                WHERE id = ? AND status = 'running'
                """,
                (
                    error[:4000],
                    now,
                    now,
                    json.dumps(
                        {
                            "phase": "failed",
                            "percent": 0,
                            "message": error[:500],
                        },
                        ensure_ascii=False,
                    ),
                    job_id,
                ),
            )
            connection.commit()

    def count_by_status(self) -> dict[str, int]:
        with self.connect() as connection:
            rows = connection.execute(
                "SELECT status, COUNT(*) AS count FROM jobs GROUP BY status"
            ).fetchall()
        return {row["status"]: row["count"] for row in rows}
