"""Per-upload transactional state; a chunk receipt never reads the full history.

The source bytes are fsynced and atomically named before their row is committed.
An interrupted write can therefore leave an unregistered part, but cannot
acknowledge a part that was never written. Retrying that index repairs it.
"""
from __future__ import annotations

import json
import os
import sqlite3
from contextlib import contextmanager
from pathlib import Path


STATE_FILENAME = "state.sqlite3"
STATE_VERSION = 1


def sync_directory(directory: Path) -> None:
    """Persist published names on POSIX before acknowledging a chunk.

    Windows keeps the existing flushed-file + atomic-replace semantics; opening
    a directory with POSIX flags is unsupported there.
    """
    if os.name == "nt":
        return
    descriptor = os.open(directory, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


@contextmanager
def connection(directory: Path):
    # mode=rw prevents a missing/corrupt session from becoming an empty upload.
    database = directory / STATE_FILENAME
    conn = sqlite3.connect(database.resolve().as_uri() + "?mode=rw", uri=True, timeout=30)
    try:
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA synchronous=FULL")
        if conn.execute("PRAGMA user_version").fetchone()[0] != STATE_VERSION:
            raise ValueError("Unsupported upload state version")
        yield conn
    finally:
        conn.close()


def create(directory: Path, metadata: dict, now: float) -> None:
    """Publish a fully initialized database atomically, under the session lock."""
    path = directory / STATE_FILENAME
    if path.exists():
        raise FileExistsError(path)
    temporary = directory / (STATE_FILENAME + ".tmp")
    # A killed initializer may leave SQLite's rollback sidecar. This is an
    # unpublished database owned by this session's creation lock, never a live
    # session. Discard both before starting its initialization again.
    for suffix in ("", "-journal", "-wal", "-shm"):
        Path(str(temporary) + suffix).unlink(missing_ok=True)
    conn = sqlite3.connect(temporary)
    try:
        conn.execute("PRAGMA synchronous=FULL")
        conn.executescript("""
            CREATE TABLE session (
                singleton INTEGER PRIMARY KEY CHECK (singleton = 1),
                metadata TEXT NOT NULL,
                updated_at REAL NOT NULL,
                job_id TEXT,
                received_count INTEGER NOT NULL DEFAULT 0 CHECK (received_count >= 0),
                received_bytes INTEGER NOT NULL DEFAULT 0 CHECK (received_bytes >= 0)
            );
            CREATE TABLE chunks (
                chunk_index INTEGER PRIMARY KEY CHECK (chunk_index >= 0),
                sha256 TEXT NOT NULL CHECK (length(sha256) = 64),
                size INTEGER NOT NULL CHECK (size > 0)
            );
            PRAGMA user_version=1;
        """)
        with conn:
            conn.execute("INSERT INTO session(singleton, metadata, updated_at) VALUES(1, ?, ?)",
                         (json.dumps(metadata, ensure_ascii=False), now))
    finally:
        conn.close()
    temporary.replace(path)
    sync_directory(directory)


def header(conn: sqlite3.Connection) -> dict:
    row = conn.execute("SELECT metadata, updated_at, job_id, received_count, received_bytes FROM session WHERE singleton=1").fetchone()
    if row is None:
        raise ValueError("Upload state is incomplete")
    return {**json.loads(row["metadata"]), "updated_at": row["updated_at"], "job_id": row["job_id"],
            "received_count": row["received_count"], "received_bytes": row["received_bytes"]}


def chunks(conn: sqlite3.Connection) -> dict:
    return {str(row["chunk_index"]): {"sha256": row["sha256"], "size": row["size"]}
            for row in conn.execute("SELECT chunk_index, sha256, size FROM chunks ORDER BY chunk_index")}


def snapshot(conn: sqlite3.Connection) -> dict:
    # Explicit transaction makes header+counters and chunks one read snapshot
    # while another process may be completing a PUT.
    with conn:
        conn.execute("BEGIN")
        return {**header(conn), "chunks": chunks(conn)}


def chunk(conn: sqlite3.Connection, index: int) -> dict | None:
    row = conn.execute("SELECT sha256, size FROM chunks WHERE chunk_index=?", (index,)).fetchone()
    return {"sha256": row["sha256"], "size": row["size"]} if row is not None else None


def insert_chunk(conn: sqlite3.Connection, index: int, digest: str, size: int, now: float) -> None:
    with conn:
        conn.execute("INSERT INTO chunks(chunk_index, sha256, size) VALUES(?, ?, ?)", (index, digest, size))
        conn.execute("UPDATE session SET received_count=received_count+1, received_bytes=received_bytes+?, updated_at=? WHERE singleton=1", (size, now))


def remove_chunk(conn: sqlite3.Connection, index: int, now: float) -> None:
    with conn:
        old = chunk(conn, index)
        if old is not None:
            conn.execute("DELETE FROM chunks WHERE chunk_index=?", (index,))
            conn.execute("UPDATE session SET received_count=received_count-1, received_bytes=received_bytes-?, updated_at=? WHERE singleton=1", (old["size"], now))


def mark_complete(conn: sqlite3.Connection, upload_id: str, now: float) -> None:
    with conn:
        conn.execute("UPDATE session SET job_id=?, updated_at=? WHERE singleton=1", (upload_id, now))
