from __future__ import annotations

import json
import re
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from threading import RLock, local
from typing import Iterator


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


class QueueCapacityError(RuntimeError):
    """Admission was rejected before a job became durable."""

    def __init__(self, limit: int):
        self.limit = limit
        super().__init__("The analysis queue is full; retry after an active job completes.")


class JobExecutionReader:
    """Reuse read-only connections for one job, never its cancellation state.

    Each calling thread has its own connection. A short shared lock also keeps
    final cleanup from closing a connection during a query. The runner must
    finish/join its callbacks before closing this job-scoped reader.
    """

    def __init__(self, path: Path, job_id: str):
        self._uri = path.resolve().as_uri() + "?mode=ro"
        self._job_id = job_id
        self._local = local()
        self._lock = RLock()
        self._connections: list[sqlite3.Connection] = []
        self._closed = False
        self.read_count = 0
        self.connection_count = 0

    def read(self) -> dict | None:
        with self._lock:
            if self._closed:
                raise RuntimeError("job execution reader is closed")
            connection = getattr(self._local, "connection", None)
            if connection is None:
                # Cross-thread use remains prohibited by ownership above; this
                # setting permits the job's owner to close all joined readers.
                connection = sqlite3.connect(self._uri, uri=True, timeout=30,
                    isolation_level=None, check_same_thread=False)
                connection.row_factory = sqlite3.Row
                self._local.connection = connection
                self._connections.append(connection)
                self.connection_count += 1
            cursor = connection.execute(
                "SELECT status, cancel_requested, worker_id, attempts FROM jobs WHERE id = ?",
                (self._job_id,),
            )
            try:
                row = cursor.fetchone()
            finally:
                cursor.close()
            self.read_count += 1
            return dict(row) if row is not None else None

    def close(self) -> None:
        with self._lock:
            if self._closed:
                return
            self._closed = True
            error = None
            for connection in self._connections:
                try:
                    connection.close()
                except Exception as exc:
                    error = error or exc
            self._connections.clear()
            if error is not None:
                raise error

    def __enter__(self) -> "JobExecutionReader":
        return self

    def __exit__(self, exc_type, active_error, traceback) -> None:
        try:
            self.close()
        except BaseException as close_error:
            if active_error is not None:
                raise active_error.with_traceback(traceback) from close_error
            raise


class JobDatabase:
    """A durable local-node queue with atomic admission and worker claims."""

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
            # SQLite cannot widen an existing CHECK constraint with ALTER.
            # Rebuild under one write transaction, preserving rows, additional
            # columns, indexes and triggers. References keep the final jobs name.
            connection.execute("PRAGMA foreign_keys=OFF")
            connection.execute("BEGIN IMMEDIATE")
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS jobs (
                    id TEXT PRIMARY KEY,
                    status TEXT NOT NULL CHECK (
                        status IN ('queued', 'running', 'succeeded', 'failed', 'cancelled')
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
                    error TEXT,
                    cancel_requested INTEGER NOT NULL DEFAULT 0,
                    cancel_requested_at TEXT,
                    cancelled_at TEXT
                )
                """
            )
            columns = {
                row["name"]
                for row in connection.execute("PRAGMA table_info(jobs)").fetchall()
            }
            if "progress_json" not in columns:
                connection.execute("ALTER TABLE jobs ADD COLUMN progress_json TEXT")
            for name, declaration in (
                ("cancel_requested", "INTEGER NOT NULL DEFAULT 0"),
                ("cancel_requested_at", "TEXT"),
                ("cancelled_at", "TEXT"),
            ):
                if name not in columns:
                    connection.execute(f"ALTER TABLE jobs ADD COLUMN {name} {declaration}")
            schema = connection.execute(
                "SELECT sql FROM sqlite_master WHERE type = 'table' AND name = 'jobs'"
            ).fetchone()["sql"]
            if not re.search(r"['\"]cancelled['\"]", schema):
                migrated, substitutions = re.subn(
                    r"(\bstatus\s+IN\s*\()([^)]*)(\))",
                    r"\1\2, 'cancelled'\3", schema, count=1, flags=re.IGNORECASE,
                )
                if substitutions != 1:
                    raise RuntimeError("Cannot migrate unknown jobs status constraint")
                migrated, substitutions = re.subn(
                    r'^(CREATE\s+TABLE\s+(?:IF\s+NOT\s+EXISTS\s+)?)(?:"jobs"|`jobs`|\[jobs\]|jobs)(?=\s|\()',
                    r"\1jobs_cancellation_migration", migrated, count=1, flags=re.IGNORECASE,
                )
                if substitutions != 1:
                    raise RuntimeError("Cannot migrate unknown jobs table declaration")
                related = connection.execute(
                    "SELECT sql FROM sqlite_master WHERE tbl_name = 'jobs' "
                    "AND type IN ('index', 'trigger') AND sql IS NOT NULL"
                ).fetchall()
                names = [row["name"] for row in connection.execute("PRAGMA table_info(jobs)")]
                column_list = ", ".join('"' + name.replace('"', '""') + '"' for name in names)
                connection.execute(migrated)
                connection.execute(f"INSERT INTO jobs_cancellation_migration ({column_list}) SELECT {column_list} FROM jobs")
                connection.execute("DROP TABLE jobs")
                connection.execute("ALTER TABLE jobs_cancellation_migration RENAME TO jobs")
                for row in related:
                    connection.execute(row["sql"])
                if connection.execute("PRAGMA foreign_key_check").fetchone() is not None:
                    raise RuntimeError("Jobs migration would invalidate a foreign key")
            connection.execute(
                "CREATE INDEX IF NOT EXISTS idx_jobs_status_created "
                "ON jobs(status, created_at)"
            )
            connection.commit()
            connection.execute("PRAGMA foreign_keys=ON")

    def create_job(
        self,
        job_id: str,
        original_filename: str,
        video_path: Path,
        request_path: Path,
        output_dir: Path,
        *,
        max_active_jobs: int | None = None,
    ) -> dict:
        if max_active_jobs is not None and (type(max_active_jobs) is not int or max_active_jobs < 1):
            raise ValueError("max_active_jobs must be a positive integer")
        now = utc_now()
        with self.connect() as connection:
            # Serialize capacity checking and insertion: simultaneous upload
            # completions must not all pass a separate read-only queue check.
            connection.execute("BEGIN IMMEDIATE")
            if max_active_jobs is not None:
                active = connection.execute(
                    "SELECT COUNT(*) FROM jobs WHERE status IN ('queued', 'running')"
                ).fetchone()[0]
                # A running cancellation still owns compute until its runner
                # acknowledges the stop; it continues to consume admission.
                if active >= max_active_jobs:
                    raise QueueCapacityError(max_active_jobs)
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
        result["cancel_requested"] = bool(result.get("cancel_requested"))
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
        *,
        expected_job_id: str | None = None,
        recover_expired: bool = True,
    ) -> dict | None:
        """Claim eligible work; managed fleets require verified recovery separately.

        An expired lease alone cannot prove that the old GPU process tree has
        exited. Managed workers leave those attempts untouched until maintenance
        confirms their exit; otherwise two attempts could write one output path.
        The fleet- owner prefix is reserved: even a legacy claimant may not
        reclaim or fail those attempts based only on their lease timestamp.
        """
        if type(recover_expired) is not bool:
            raise ValueError('recover_expired must be boolean')
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
                    AND attempts < ? AND cancel_requested = 0
                    AND (worker_id IS NULL OR worker_id NOT LIKE 'fleet-%')
                    AND (? IS NULL OR id = ?)
                    AND ?
                """,
                (now_text, now_text, max_attempts, expected_job_id, expected_job_id, recover_expired),
            )
            connection.execute(
                """
                UPDATE jobs
                SET status = 'failed', completed_at = ?, updated_at = ?,
                    error = 'worker lease expired and retry budget was exhausted'
                WHERE status = 'running' AND lease_expires_at < ?
                    AND attempts >= ? AND cancel_requested = 0
                    AND (worker_id IS NULL OR worker_id NOT LIKE 'fleet-%')
                    AND (? IS NULL OR id = ?)
                    AND ?
                """,
                (now_text, now_text, now_text, max_attempts, expected_job_id, expected_job_id, recover_expired),
            )
            row = connection.execute(
                """
                SELECT id FROM jobs
                WHERE status = 'queued' AND attempts < ? AND cancel_requested = 0
                    AND (? IS NULL OR id = ?)
                ORDER BY created_at, id
                LIMIT 1
                """,
                (max_attempts, expected_job_id, expected_job_id),
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
                WHERE id = ? AND status = 'queued' AND cancel_requested = 0
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
        *,
        worker_id: str | None = None,
        attempts: int | None = None,
    ) -> None:
        self._validate_owner(worker_id, attempts)
        now = datetime.now(timezone.utc)
        lease = (now + timedelta(seconds=lease_seconds)).isoformat()
        with self.connect() as connection:
            connection.execute(
                """
                UPDATE jobs
                SET progress_json = ?, updated_at = ?, lease_expires_at = ?
                WHERE id = ? AND status = 'running'
                    AND (? IS NULL OR (worker_id = ? AND attempts = ?))
                """,
                (
                    json.dumps(progress, ensure_ascii=False),
                    now.isoformat(),
                    lease,
                    job_id,
                    worker_id,
                    worker_id,
                    attempts,
                ),
            )
            connection.commit()

    @staticmethod
    def _validate_owner(worker_id: str | None, attempts: int | None) -> None:
        if (worker_id is None) != (attempts is None):
            raise ValueError("worker_id and attempts must be provided together")

    @staticmethod
    def _cancelled_progress(raw: str | None) -> str:
        progress = json.loads(raw) if raw else {}
        progress.update(phase="cancelled", message="任务已取消，已停止处理；原始上传及历史记录保留")
        progress.setdefault("percent", 0)
        return json.dumps(progress, ensure_ascii=False)

    def request_cancel(self, job_id: str) -> dict | None:
        """Request durable cancellation; only a worker may acknowledge running work.

        A lease expiry cannot establish that GPU execution stopped. Requested
        running jobs are therefore excluded from retries until acknowledged.
        """
        now = utc_now()
        with self.connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute("SELECT * FROM jobs WHERE id = ?", (job_id,)).fetchone()
            if row is None:
                connection.commit()
                return None
            if row["status"] in {"queued", "running"} and not row["cancel_requested"]:
                if row["status"] == "queued":
                    connection.execute(
                        "UPDATE jobs SET status = 'cancelled', cancel_requested = 1, cancel_requested_at = ?, "
                        "cancelled_at = ?, completed_at = ?, updated_at = ?, lease_expires_at = NULL, progress_json = ? WHERE id = ?",
                        (now, now, now, now, self._cancelled_progress(row["progress_json"]), job_id),
                    )
                else:
                    connection.execute(
                        "UPDATE jobs SET cancel_requested = 1, cancel_requested_at = ?, updated_at = ? WHERE id = ?",
                        (now, now, job_id),
                    )
            connection.commit()
        return self.get_job(job_id)

    def execution_state(self, job_id: str) -> dict | None:
        """Small polling projection; do not parse large summaries on every frame."""
        with self.connect() as connection:
            row = connection.execute(
                "SELECT status, cancel_requested, worker_id, attempts FROM jobs WHERE id = ?", (job_id,)
            ).fetchone()
        return dict(row) if row is not None else None

    def execution_reader(self, job_id: str) -> JobExecutionReader:
        """One attempt's polling scope; all other database access stays unchanged."""
        return JobExecutionReader(self.path, job_id)

    def running_attempts_for_worker(self, worker_id: str) -> list[dict]:
        """Snapshot one exact owner's attempts, without lease-based inference."""
        if not isinstance(worker_id, str) or not worker_id or len(worker_id) > 256:
            raise ValueError("worker_id must identify one bounded worker owner")
        with self.connect() as connection:
            rows = connection.execute(
                "SELECT id AS job_id, attempts FROM jobs WHERE status = 'running' AND worker_id = ? ORDER BY id",
                (worker_id,),
            ).fetchall()
        return [dict(row) for row in rows]

    def recover_confirmed_worker_attempts(
        self, worker_id: str, attempts: list[dict], *, max_attempts: int,
        confirmed_tree_exit: bool = False,
    ) -> list[dict]:
        """Recover fenced attempts only after their owned process tree stopped.

        The fleet owner supplies confirmation after OwnedProcess.close returns
        successfully. This flag is an internal precondition, not an API for
        asserting process death from a PID, heartbeat, or expired lease.
        """
        if confirmed_tree_exit is not True:
            raise ValueError("Worker process-tree exit must be confirmed before recovery")
        if (not isinstance(worker_id, str) or not worker_id or len(worker_id) > 256
                or type(max_attempts) is not int or max_attempts < 1):
            raise ValueError("Recovery requires an exact worker owner and positive retry budget")
        if not isinstance(attempts, list):
            raise ValueError("Recovery requires an explicit attempt snapshot")
        seen = set()
        for attempt in attempts:
            if (not isinstance(attempt, dict) or set(attempt) != {"job_id", "attempts"}
                    or not isinstance(attempt["job_id"], str) or not attempt["job_id"]
                    or type(attempt["attempts"]) is not int or attempt["attempts"] < 1
                    or attempt["job_id"] in seen):
                raise ValueError("Invalid or duplicate recovery attempt snapshot")
            seen.add(attempt["job_id"])
        recovered, now = [], utc_now()
        with self.connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            for attempt in attempts:
                row = connection.execute(
                    "SELECT cancel_requested, progress_json FROM jobs "
                    "WHERE id = ? AND status = 'running' AND worker_id = ? AND attempts = ?",
                    (attempt["job_id"], worker_id, attempt["attempts"]),
                ).fetchone()
                if row is None:
                    continue  # A completed job, changed owner, or newer attempt is fenced out.
                if row["cancel_requested"]:
                    status, failure = "cancelled", None
                    progress = self._cancelled_progress(row["progress_json"])
                elif attempt["attempts"] < max_attempts:
                    status = "queued"
                    failure = "worker process tree stopped; retrying after confirmed exit"
                    progress = json.dumps({"phase": "queued", "percent": 0,
                        "message": "处理进程已停止，任务已重新排队"}, ensure_ascii=False)
                else:
                    status = "failed"
                    failure = "worker process tree stopped; retry budget exhausted after confirmed exit"
                    progress = json.dumps({"phase": "failed", "percent": 0,
                        "message": "处理进程已停止，重试次数已用尽"}, ensure_ascii=False)
                connection.execute(
                    "UPDATE jobs SET status = ?, worker_id = ?, lease_expires_at = NULL, "
                    "updated_at = ?, completed_at = ?, cancelled_at = ?, error = ?, progress_json = ? "
                    "WHERE id = ? AND status = 'running' AND worker_id = ? AND attempts = ?",
                    (status, None if status == "queued" else worker_id, now,
                     None if status == "queued" else now, now if status == "cancelled" else None,
                     failure, progress, attempt["job_id"], worker_id, attempt["attempts"]),
                )
                recovered.append({**attempt, "worker_id": worker_id, "status": status})
            connection.commit()
        return recovered

    def _finish(
        self, job_id: str, status: str, *, summary: dict | None = None,
        error: str | None = None, worker_id: str | None = None, attempts: int | None = None,
    ) -> str | None:
        self._validate_owner(worker_id, attempts)
        now = utc_now()
        with self.connect() as connection:
            # Serialize with request_cancel: whichever commits first wins. If
            # cancellation was already requested, an exiting runner confirms
            # cancellation even when it returned a summary or raised an error.
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute("SELECT * FROM jobs WHERE id = ?", (job_id,)).fetchone()
            if (row is None or row["status"] != "running"
                    or worker_id is not None and (row["worker_id"] != worker_id or row["attempts"] != attempts)
                    or status == "cancelled" and not row["cancel_requested"]):
                connection.commit()
                return None
            if row["cancel_requested"]:
                status = "cancelled"
                progress = self._cancelled_progress(row["progress_json"])
                summary_json, failure = row["summary_json"], row["error"]
            elif status == "succeeded":
                summary_json, failure = json.dumps(summary, ensure_ascii=False), None
                frames = (summary or {}).get("processing", {}).get("processed_frames")
                progress = json.dumps({"phase": "completed", "percent": 100,
                    "processed_frames": frames, "total_frames": frames, "message": "推理与产物校验完成"}, ensure_ascii=False)
            else:
                summary_json, failure = row["summary_json"], (error or "")[:4000]
                progress = json.dumps({"phase": "failed", "percent": 0, "message": failure[:500]}, ensure_ascii=False)
            connection.execute(
                "UPDATE jobs SET status = ?, summary_json = ?, error = ?, completed_at = ?, updated_at = ?, "
                "lease_expires_at = NULL, progress_json = ?, cancelled_at = ? WHERE id = ?",
                (status, summary_json, failure, now, now, progress, now if status == "cancelled" else None, job_id),
            )
            connection.commit()
        return status

    def mark_succeeded(self, job_id: str, summary: dict, *, worker_id: str | None = None,
                       attempts: int | None = None) -> str | None:
        return self._finish(job_id, "succeeded", summary=summary, worker_id=worker_id, attempts=attempts)

    def mark_failed(self, job_id: str, error: str, *, worker_id: str | None = None,
                    attempts: int | None = None) -> str | None:
        return self._finish(job_id, "failed", error=error, worker_id=worker_id, attempts=attempts)

    def mark_cancelled(self, job_id: str, *, worker_id: str, attempts: int) -> bool:
        """Called only after runner cleanup/return, by the owning attempt."""
        return self._finish(job_id, "cancelled", worker_id=worker_id, attempts=attempts) == "cancelled"

    def count_by_status(self) -> dict[str, int]:
        with self.connect() as connection:
            rows = connection.execute(
                "SELECT status, COUNT(*) AS count FROM jobs GROUP BY status"
            ).fetchall()
        return {row["status"]: row["count"] for row in rows}
