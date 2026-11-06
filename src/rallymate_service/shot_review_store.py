"""Source-bound, revisioned human hit/bounce annotations, separate from scores."""
from __future__ import annotations

from datetime import datetime, timezone
from contextlib import contextmanager
import hashlib
import json
from pathlib import Path
import re
import sqlite3

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator
from rallymate_service.shot_statistics import MAX_REVIEWS


MAX_SHOT_REVIEW_BYTES = 8192


class ShotReviewError(ValueError):
    def __init__(self, message, status=422):
        super().__init__(message)
        self.status = status


class ShotReviewInput(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    mutation_id: str
    expected_revision: int = Field(ge=0)
    source_video_sha256: str
    event: dict

    @field_validator("mutation_id")
    @classmethod
    def mutation_uuid(cls, value):
        if not re.fullmatch(r"[0-9a-fA-F]{8}-(?:[0-9a-fA-F]{4}-){3}[0-9a-fA-F]{12}", value):
            raise ValueError("invalid mutation identity")
        return value.lower()

    @field_validator("source_video_sha256")
    @classmethod
    def source_digest(cls, value):
        if not re.fullmatch(r"[0-9a-fA-F]{64}", value):
            raise ValueError("invalid video digest")
        return value.lower()


def parse_shot_review(body: bytes) -> ShotReviewInput:
    if len(body) > MAX_SHOT_REVIEW_BYTES:
        raise ShotReviewError("本次标记内容过长。", 413)
    try:
        def pairs(items):
            result = {}
            for key, value in items:
                if key in result:
                    raise ValueError("duplicate field")
                result[key] = value
            return result
        value = json.loads(body, object_pairs_hook=pairs,
            parse_constant=lambda _: (_ for _ in ()).throw(ValueError("nonfinite number")))
        return ShotReviewInput.model_validate(value)
    except (ValueError, TypeError, ValidationError):
        raise ShotReviewError("击球标记格式无效，请刷新后重试。") from None


class ShotReviewStore:
    def __init__(self, path: Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as connection:
            connection.executescript("""
                CREATE TABLE IF NOT EXISTS shot_review_heads (
                    job_id TEXT PRIMARY KEY, source_sha TEXT NOT NULL, revision INTEGER NOT NULL);
                CREATE TABLE IF NOT EXISTS shot_review_events (
                    job_id TEXT NOT NULL, event_id TEXT NOT NULL, payload TEXT NOT NULL,
                    PRIMARY KEY (job_id,event_id));
                CREATE TABLE IF NOT EXISTS shot_review_history (
                    job_id TEXT NOT NULL, mutation_id TEXT NOT NULL, request_sha TEXT NOT NULL,
                    revision INTEGER NOT NULL, payload TEXT NOT NULL, created_at TEXT NOT NULL,
                    PRIMARY KEY (job_id,mutation_id));
            """)

    @contextmanager
    def _connect(self):
        connection = sqlite3.connect(self.path, timeout=10)
        try:
            with connection:
                yield connection
        finally:
            connection.close()

    @staticmethod
    def _state(connection, job_id, source_sha):
        head = connection.execute("SELECT source_sha,revision FROM shot_review_heads WHERE job_id=?", (job_id,)).fetchone()
        if head is not None and head[0] != source_sha.lower():
            raise ShotReviewError("视频来源已改变，旧标记不能套用到新视频。", 409)
        rows = connection.execute("SELECT payload FROM shot_review_events WHERE job_id=? ORDER BY event_id", (job_id,)).fetchall()
        return [json.loads(row[0]) for row in rows], head[1] if head else 0

    def read(self, job_id, source_sha):
        with self._connect() as connection:
            connection.execute("BEGIN")
            return self._state(connection, job_id, source_sha)

    def save(self, job_id, source_sha, submission: ShotReviewInput, validate_event):
        if submission.source_video_sha256 != source_sha.lower():
            raise ShotReviewError("标记与当前视频不匹配，请重新读取。", 409)
        canonical = json.dumps(submission.model_dump(), ensure_ascii=False, sort_keys=True,
                               separators=(",", ":"), allow_nan=False)
        request_sha = hashlib.sha256(canonical.encode()).hexdigest()
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            events, revision = self._state(connection, job_id, source_sha)
            previous = connection.execute("SELECT request_sha FROM shot_review_history WHERE job_id=? AND mutation_id=?",
                                          (job_id, submission.mutation_id)).fetchone()
            if previous:
                if previous[0] != request_sha:
                    raise ShotReviewError("同一保存请求包含不同内容，请重新操作。", 409)
                return events, revision
            if revision != submission.expected_revision:
                raise ShotReviewError("标记已在其他页面更新，请重新读取后再保存。", 409)
            # Validate only new mutations, against the state protected by this
            # transaction. An old, already accepted request must not be replayed
            # against later confirmations or silently restore withdrawn events.
            event = validate_event(events, revision)
            if len(events) >= MAX_REVIEWS and event["id"] not in {row["id"] for row in events}:
                raise ShotReviewError("本视频的人工标记数量已达上限。", 409)
            payload = json.dumps(event, ensure_ascii=False, allow_nan=False, separators=(",", ":"))
            revision += 1
            connection.execute("INSERT INTO shot_review_heads VALUES (?,?,?) ON CONFLICT(job_id) DO UPDATE SET revision=excluded.revision",
                               (job_id, source_sha.lower(), revision))
            connection.execute("INSERT INTO shot_review_events VALUES (?,?,?) ON CONFLICT(job_id,event_id) DO UPDATE SET payload=excluded.payload",
                               (job_id, event["id"], payload))
            connection.execute("INSERT INTO shot_review_history VALUES (?,?,?,?,?,?)",
                (job_id, submission.mutation_id, request_sha, revision, payload, datetime.now(timezone.utc).isoformat()))
            return self._state(connection, job_id, source_sha)
