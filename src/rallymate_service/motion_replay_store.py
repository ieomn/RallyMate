"""Durable, fenced motion-replay queue, separate from measured vision results."""
from __future__ import annotations

import hashlib
import json
import sqlite3
import time
import uuid
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator


REPLAY_VERSION = "motion-replay-v1"
PROMPT_VERSION = "white-mannequin-motion-v2"
MAX_CONSECUTIVE_REMOTE_FAILURES = 3
ACTIVE_STATUSES = ("queued", "rendering", "submitting", "provider_processing", "downloading")
TERMINAL_STATUSES = ("completed", "failed", "submit_unknown")
MEDIA_FILES = {
    "reference_video": "reference.mp4", "first_frame": "first.png",
    "middle_frame": "middle.png", "last_frame": "last.png",
    "generated_video": "generated.mp4", "trajectory": "trajectory.json",
    "manifest": "manifest.json",
}


@dataclass(frozen=True)
class MotionReplaySettings:
    data_root: Path
    enabled: bool = False
    model: str = "MiniMax-H3"
    resolution: str = "768P"
    duration_seconds: int = 6
    max_attempts: int = 3
    lease_seconds: int = 180
    poll_seconds: float = 15.0
    max_pending_seconds: int = 3600
    public_base_url: str | None = None

    def __post_init__(self) -> None:
        if self.model != "MiniMax-H3" or self.resolution != "768P" or self.duration_seconds != 6:
            raise ValueError("motion replay currently supports MiniMax-H3 / 768P / 6 seconds")
        if not 1 <= self.max_attempts <= 5 or self.lease_seconds < 15:
            raise ValueError("invalid motion replay retry or lease bounds")
        if not 0 < self.poll_seconds <= 300 or self.max_pending_seconds < 60:
            raise ValueError("invalid motion replay polling bounds")

    @property
    def output_root(self) -> Path:
        return Path(self.data_root).resolve() / "motion-replays"

    @property
    def database_path(self) -> Path:
        return Path(self.data_root).resolve() / "motion-replays.sqlite3"


class ReplayConflict(ValueError):
    pass


class LeaseLost(RuntimeError):
    pass


def timestamp_iso(value: float | None) -> str | None:
    return datetime.fromtimestamp(value, timezone.utc).isoformat() if value is not None else None


def sha256_path(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)


class MotionReplayStore:
    def __init__(self, path: str | Path):
        self.path = Path(path).resolve()

    @contextmanager
    def connect(self) -> Iterator[sqlite3.Connection]:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        connection = sqlite3.connect(str(self.path), timeout=30)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA journal_mode=WAL")
        try:
            yield connection
        finally:
            connection.close()

    def initialize(self) -> None:
        with self.connect() as connection:
            connection.executescript("""
                CREATE TABLE IF NOT EXISTS motion_replays (
                    id TEXT PRIMARY KEY, job_id TEXT NOT NULL,
                    fingerprint TEXT NOT NULL UNIQUE, request_json TEXT NOT NULL,
                    generation_status TEXT NOT NULL, quality_status TEXT NOT NULL DEFAULT 'pending_review',
                    created_at REAL NOT NULL, updated_at REAL NOT NULL, completed_at REAL,
                    attempts INTEGER NOT NULL DEFAULT 1, max_attempts INTEGER NOT NULL,
                    query_failures INTEGER NOT NULL DEFAULT 0, download_failures INTEGER NOT NULL DEFAULT 0,
                    next_attempt_at REAL, lease_token TEXT, lease_expires_at REAL, worker_id TEXT,
                    provider_task_id TEXT, provider_submitted_at REAL, poll_deadline_at REAL, provider_history_json TEXT NOT NULL DEFAULT '[]',
                    download_url TEXT, artifacts_json TEXT NOT NULL DEFAULT '{}',
                    render_manifest_json TEXT, generated_metadata_json TEXT, error_code TEXT, error_message TEXT,
                    error_retryable INTEGER NOT NULL DEFAULT 0, retry_stage TEXT
                );
                CREATE INDEX IF NOT EXISTS idx_motion_replays_queue
                    ON motion_replays(generation_status, next_attempt_at, created_at);
                CREATE INDEX IF NOT EXISTS idx_motion_replays_jobs ON motion_replays(job_id, created_at);
                CREATE TABLE IF NOT EXISTS motion_replay_idempotency (
                    job_id TEXT NOT NULL, request_key TEXT NOT NULL,
                    fingerprint TEXT NOT NULL, replay_id TEXT NOT NULL, client_request_json TEXT,
                    PRIMARY KEY(job_id, request_key)
                );
            """)
            connection.execute("BEGIN IMMEDIATE")
            columns = {row["name"] for row in connection.execute("PRAGMA table_info(motion_replays)")}
            for name, kind in (("poll_deadline_at", "REAL"), ("generated_metadata_json", "TEXT"),
                               ("query_failures", "INTEGER NOT NULL DEFAULT 0"),
                               ("download_failures", "INTEGER NOT NULL DEFAULT 0")):
                if name not in columns:
                    connection.execute(f"ALTER TABLE motion_replays ADD COLUMN {name} {kind}")
            key_columns = {row["name"] for row in connection.execute("PRAGMA table_info(motion_replay_idempotency)")}
            if "client_request_json" not in key_columns:
                connection.execute("ALTER TABLE motion_replay_idempotency ADD COLUMN client_request_json TEXT")
            connection.commit()

    @staticmethod
    def _decode(row: sqlite3.Row | None) -> dict | None:
        if row is None:
            return None
        result = dict(row)
        for name in ("request", "artifacts", "render_manifest", "provider_history", "generated_metadata"):
            raw = result.pop(f"{name}_json")
            result[name] = json.loads(raw) if raw else None
        result["error_retryable"] = bool(result["error_retryable"])
        return result

    def get(self, replay_id: str) -> dict | None:
        with self.connect() as connection:
            return self._decode(connection.execute("SELECT * FROM motion_replays WHERE id=?", (replay_id,)).fetchone())

    def list_for_job(self, job_id: str, limit: int = 30) -> list[dict]:
        with self.connect() as connection:
            return [self._decode(row) for row in connection.execute(
                "SELECT * FROM motion_replays WHERE job_id=? ORDER BY created_at DESC LIMIT ?",
                (job_id, max(1, min(limit, 100))),
            ).fetchall()]

    @staticmethod
    def _same_client_request(stored_request: dict, client_request: dict) -> bool:
        if "client_request" in stored_request:
            return _json(stored_request["client_request"]) == _json(client_request)
        # v1 records predate client-intent snapshots. They still retain the
        # selected window and episode. An automatic retry safely returns its
        # original operation instead of selecting/paying for another window.
        if stored_request.get("duration_ms") != client_request.get("duration_ms"):
            return False
        if "episode_id" in client_request:
            return stored_request.get("episode_id") == client_request["episode_id"]
        if "start_ms" in client_request:
            return stored_request.get("start_ms") == client_request["start_ms"] and not stored_request.get("episode_id")
        return client_request.get("selection") == "automatic" and not stored_request.get("episode_id")

    def find_idempotent(self, job_id: str, key: str, client_request: dict) -> dict | None:
        with self.connect() as connection:
            binding = connection.execute("SELECT * FROM motion_replay_idempotency WHERE job_id=? AND request_key=?",
                                         (job_id, key)).fetchone()
            row = (self._decode(connection.execute("SELECT * FROM motion_replays WHERE id=?", (binding["replay_id"],)).fetchone())
                   if binding else None)
        matches = (_json(json.loads(binding["client_request_json"])) == _json(client_request)
                   if binding and binding["client_request_json"] else self._same_client_request(row["request"], client_request) if row else True)
        if row and not matches:
            raise ReplayConflict("Idempotency-Key was already used for another replay request")
        return row

    def create(self, job_id: str, request: dict, *, max_attempts: int = 3,
               idempotency_key: str | None = None) -> dict:
        # Paths are private operational inputs. Content and window determine deduplication.
        identity = {key: value for key, value in request.items()
                    if not key.endswith("_path") and key not in {"episode_id", "max_pending_seconds", "client_request"}}
        fingerprint = hashlib.sha256(_json({"job_id": job_id, **identity}).encode()).hexdigest()
        now, replay_id = time.time(), uuid.uuid4().hex
        with self.connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            if idempotency_key:
                existing = connection.execute("SELECT * FROM motion_replay_idempotency WHERE job_id=? AND request_key=?",
                                              (job_id, idempotency_key)).fetchone()
                if existing:
                    original = self._decode(connection.execute("SELECT * FROM motion_replays WHERE id=?",
                                                               (existing["replay_id"],)).fetchone())
                    if isinstance(request.get("client_request"), dict) and original is not None:
                        same_intent = (_json(json.loads(existing["client_request_json"])) == _json(request["client_request"])
                                       if existing["client_request_json"] else self._same_client_request(original["request"], request["client_request"]))
                    else:
                        same_intent = existing["fingerprint"] == fingerprint
                    if not same_intent:
                        raise ReplayConflict("Idempotency-Key was already used for another replay request")
                    if original is not None:
                        connection.commit()
                        return original
            existing = connection.execute("SELECT id FROM motion_replays WHERE fingerprint=?", (fingerprint,)).fetchone()
            if existing:
                replay_id = existing["id"]
            else:
                connection.execute("""INSERT INTO motion_replays
                    (id,job_id,fingerprint,request_json,generation_status,created_at,updated_at,max_attempts,next_attempt_at)
                    VALUES (?,?,?,?,'queued',?,?,?,?)""",
                    (replay_id, job_id, fingerprint, _json(request), now, now, max_attempts, now))
            if idempotency_key:
                connection.execute("""INSERT OR IGNORE INTO motion_replay_idempotency
                    (job_id,request_key,fingerprint,replay_id,client_request_json) VALUES (?,?,?,?,?)""",
                                   (job_id, idempotency_key, fingerprint, replay_id,
                                    _json(request["client_request"]) if "client_request" in request else None))
            connection.commit()
        return self.get(replay_id)

    def claim_next(self, worker_id: str, lease_seconds: int) -> dict | None:
        now, token = time.time(), uuid.uuid4().hex
        with self.connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            # A crashed paid POST cannot safely be recreated without a server task id.
            connection.execute("""UPDATE motion_replays SET generation_status='submit_unknown',
                error_code='submission_outcome_unknown',error_message='提交结果尚未确认，需先核对服务商任务；不会自动重复提交。',
                error_retryable=0,updated_at=?,lease_token=NULL,lease_expires_at=NULL,worker_id=NULL,next_attempt_at=NULL
                WHERE generation_status='submitting' AND provider_task_id IS NULL AND lease_expires_at<=?""", (now, now))
            expired = connection.execute("""SELECT * FROM motion_replays
                WHERE generation_status IN ('queued','rendering','provider_processing','downloading')
                AND lease_expires_at<=?""", (now,)).fetchall()
            for row in expired:
                remote_stage = row["provider_task_id"] and row["generation_status"] in {"provider_processing", "downloading"}
                counter = "download_failures" if row["generation_status"] == "downloading" else "query_failures"
                failures = row[counter] + 1 if remote_stage else row[counter]
                exhausted = failures >= MAX_CONSECUTIVE_REMOTE_FAILURES if remote_stage else row["attempts"] >= row["max_attempts"]
                retry_stage = "provider_processing" if remote_stage else "queued"
                connection.execute("""UPDATE motion_replays SET lease_token=NULL,lease_expires_at=NULL,worker_id=NULL,
                    attempts=attempts+?,generation_status=CASE WHEN ? THEN 'failed' ELSE generation_status END,
                    next_attempt_at=?,updated_at=?,error_code='worker_interrupted',
                    error_message='工作进程中断，已按有限重试策略恢复。',error_retryable=?,retry_stage=?,
                    query_failures=?,download_failures=? WHERE id=?""",
                    (0 if remote_stage or exhausted else 1, exhausted, None if exhausted else now, now,
                     bool(remote_stage) or not exhausted, retry_stage,
                     failures if counter == "query_failures" else row["query_failures"],
                     failures if counter == "download_failures" else row["download_failures"], row["id"]))
            row = connection.execute("""SELECT id FROM motion_replays
                WHERE generation_status IN ('queued','rendering','provider_processing','downloading')
                AND lease_token IS NULL AND (next_attempt_at IS NULL OR next_attempt_at<=?)
                ORDER BY COALESCE(next_attempt_at,created_at),created_at LIMIT 1""", (now,)).fetchone()
            if row is None:
                connection.commit()
                return None
            connection.execute("UPDATE motion_replays SET lease_token=?,lease_expires_at=?,worker_id=? WHERE id=?",
                               (token, now + lease_seconds, worker_id, row["id"]))
            connection.commit()
        return self.get(row["id"])

    def renew(self, replay_id: str, token: str, lease_seconds: int) -> bool:
        now = time.time()
        with self.connect() as connection:
            count = connection.execute("""UPDATE motion_replays SET lease_expires_at=?
                WHERE id=? AND lease_token=? AND lease_expires_at>?""", (now + lease_seconds, replay_id, token, now)).rowcount
            connection.commit()
        return count == 1

    def transition(self, replay_id: str, token: str, status: str, *, release: bool = False,
                   delay: float = 0, **updates: Any) -> dict:
        if status not in ACTIVE_STATUSES + TERMINAL_STATUSES:
            raise ValueError("invalid replay status")
        allowed = {"provider_task_id", "provider_submitted_at", "poll_deadline_at", "provider_history", "download_url", "artifacts", "render_manifest", "generated_metadata",
                   "error_code", "error_message", "error_retryable", "retry_stage", "attempts", "query_failures", "download_failures"}
        if not set(updates) <= allowed:
            raise ValueError("unsupported replay update")
        now = time.time()
        values = {"generation_status": status, "updated_at": now, "next_attempt_at": now + delay}
        for key, value in updates.items():
            values[f"{key}_json" if key in {"artifacts", "render_manifest", "provider_history", "generated_metadata"} else key] = (
                _json(value) if key in {"artifacts", "render_manifest", "provider_history", "generated_metadata"} else value)
        if status in TERMINAL_STATUSES:
            release = True
            values.update(completed_at=now, next_attempt_at=None)
        if release:
            values.update(lease_token=None, lease_expires_at=None, worker_id=None)
        with self.connect() as connection:
            count = connection.execute(
                "UPDATE motion_replays SET " + ",".join(f"{key}=?" for key in values) +
                " WHERE id=? AND lease_token=? AND lease_expires_at>?",
                (*values.values(), replay_id, token, now),
            ).rowcount
            connection.commit()
        if count != 1:
            raise LeaseLost("motion replay lease was lost")
        return self.get(replay_id)

    @staticmethod
    def can_retry(row: dict) -> bool:
        if row["generation_status"] != "failed" or not row["error_retryable"]:
            return False
        if row["retry_stage"] in {"provider_processing", "downloading"}:
            # Continuing a known remote task cannot create another paid generation.
            return bool(row["provider_task_id"])
        return row["retry_stage"] == "queued" and row["attempts"] < row["max_attempts"]

    def retry(self, replay_id: str) -> dict:
        now = time.time()
        with self.connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = self._decode(connection.execute("SELECT * FROM motion_replays WHERE id=?", (replay_id,)).fetchone())
            if row is None:
                raise KeyError(replay_id)
            if not self.can_retry(row):
                raise ReplayConflict("this task cannot be safely retried")
            clear_provider = row["retry_stage"] == "queued"
            connection.execute("""UPDATE motion_replays SET generation_status=?,attempts=attempts+?,
                updated_at=?,next_attempt_at=?,completed_at=NULL,error_code=NULL,error_message=NULL,error_retryable=0,
                query_failures=0,download_failures=0,
                lease_token=NULL,lease_expires_at=NULL,worker_id=NULL,
                provider_task_id=CASE WHEN ? THEN NULL ELSE provider_task_id END,
                provider_submitted_at=CASE WHEN ? THEN NULL ELSE provider_submitted_at END,poll_deadline_at=?,
                download_url=CASE WHEN ? THEN NULL ELSE download_url END WHERE id=?""",
                (row["retry_stage"], int(clear_provider), now, now, clear_provider, clear_provider,
                 now + int(row["request"].get("max_pending_seconds", 3600)), clear_provider, replay_id))
            connection.commit()
        return self.get(replay_id)
