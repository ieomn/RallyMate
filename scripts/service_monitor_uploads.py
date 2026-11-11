"""Bounded, read-only upload progress for SQLite and legacy upload sessions.

SQLite counters describe durably acknowledged chunks, not raw socket traffic.
The monitor never reads the growing chunks table or repairs upload state.
"""
from __future__ import annotations

from contextlib import closing
import json
import math
import os
from pathlib import Path
import re
import sqlite3
import stat
import time
import unicodedata

RECENT_SECONDS = 24 * 60 * 60
STATE_FILENAME = "state.sqlite3"
STATE_VERSION = 1
MAX_METADATA_BYTES = 64 * 1024
MAX_MANIFEST_BYTES = 8 * 1024 * 1024
MAX_SESSIONS = 4096
MAX_SCAN_SECONDS = 0.75
MAX_INTEGER = 2**63 - 1
SQLITE_TIMEOUT = 0.05


def _safe_name(value: str) -> str:
    value = "".join(" " if unicodedata.category(char).startswith("C") else char for char in value)
    value = re.sub(r"\bsk-[A-Za-z0-9_-]{12,}", "[已隐藏密钥]", value)
    value = re.sub(r"(?i)\bbearer\s+[A-Za-z0-9._~+/-]+=*", "Bearer [已隐藏]", value)
    value = re.sub(r"(?i)(authorization|api[_-]?key|token)(\s*[:=]\s*)\S+", r"\1\2[已隐藏]", value)
    return value if len(value) <= 65 else value[:64] + "…"


def _integer(value, minimum=0, maximum=MAX_INTEGER):
    if type(value) is not int or not minimum <= value <= maximum:
        raise ValueError("invalid upload counter")
    return value


def _time(value):
    if type(value) not in (int, float) or not math.isfinite(value) or not 0 <= value <= 253402300799:
        raise ValueError("invalid upload timestamp")
    return float(value)


def _json(raw):
    def pairs(items):
        value = {}
        for key, item in items:
            if key in value:
                raise ValueError("duplicate upload field")
            value[key] = item
        return value

    def nonfinite(_):
        raise ValueError("non-finite upload metadata")

    result = json.loads(raw, object_pairs_hook=pairs, parse_constant=nonfinite)
    if not isinstance(result, dict):
        raise ValueError("upload metadata must be an object")
    return result


def _checked_path(path: Path, parent: Path, *, directory=False):
    metadata = path.lstat()
    if (stat.S_ISLNK(metadata.st_mode)
            or getattr(metadata, "st_file_attributes", 0) & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0)
            or path.resolve(strict=True).parent != parent):
        raise ValueError("linked upload path")
    expected = stat.S_ISDIR if directory else stat.S_ISREG
    if not expected(metadata.st_mode):
        raise ValueError("unexpected upload file type")
    return metadata


def _common(item, identifier):
    if not isinstance(item, dict) or item.get("upload_id") != identifier:
        raise ValueError("upload identity differs from its directory")
    if not 0 < len(identifier) <= 128 or any(unicodedata.category(c).startswith("C") for c in identifier):
        raise ValueError("invalid upload identity")
    filename = item.get("filename")
    if not isinstance(filename, str) or not 0 < len(filename) <= 4096:
        raise ValueError("invalid upload filename")
    job_id = item.get("job_id")
    if job_id is not None and (not isinstance(job_id, str) or not 0 < len(job_id) <= 128):
        raise ValueError("invalid submitted job identity")
    return {"id": identifier, "name": _safe_name(filename), "total": _integer(item.get("size"), 1),
            "updated": _time(item.get("updated_at")), "job_id": job_id}


def _sqlite(path: Path, directory: Path):
    before = _checked_path(path, directory)
    # Read-only SQLite may consult existing recovery/WAL files. Do not follow
    # a linked sidecar into some other data directory either.
    for suffix in ("-journal", "-wal", "-shm"):
        sidecar = path.with_name(path.name + suffix)
        if os.path.lexists(sidecar):
            _checked_path(sidecar, directory)
    with closing(sqlite3.connect(path.as_uri() + "?mode=ro", uri=True, timeout=SQLITE_TIMEOUT)) as connection:
        connection.execute("PRAGMA query_only=ON")
        connection.execute("PRAGMA trusted_schema=OFF")
        connection.execute("PRAGMA cache_size=-64")
        remaining = 20

        def budget():
            nonlocal remaining
            remaining -= 1
            return int(remaining <= 0)

        connection.set_progress_handler(budget, 1000)
        if connection.execute("PRAGMA user_version").fetchone()[0] != STATE_VERSION:
            raise ValueError("unsupported upload state version")
        # Bound values before bringing them into Python. Counters and metadata
        # come from the same SELECT snapshot; no chunks/history query is needed.
        rows = connection.execute("""SELECT
            CASE WHEN typeof(metadata)='text' AND length(CAST(metadata AS BLOB)) <= ? THEN metadata END,
            CASE WHEN typeof(updated_at) IN ('integer','real') THEN updated_at END,
            CASE WHEN typeof(job_id)='null' THEN NULL
                 WHEN typeof(job_id)='text' AND length(CAST(job_id AS BLOB)) <= 512 THEN job_id ELSE 0 END,
            CASE WHEN typeof(received_count)='integer' THEN received_count END,
            CASE WHEN typeof(received_bytes)='integer' THEN received_bytes END
            FROM session WHERE singleton=1 LIMIT 2""", (MAX_METADATA_BYTES,)).fetchall()
        if len(rows) != 1 or rows[0][0] is None:
            raise ValueError("missing or oversized upload state")
        raw, updated, job_id, received_count, received_bytes = rows[0]
    after = _checked_path(path, directory)
    if (before.st_dev, before.st_ino) != (after.st_dev, after.st_ino):
        raise ValueError("upload state replaced during reading")
    item = {**_json(raw), "updated_at": updated, "job_id": job_id}
    result = _common(item, directory.name)
    chunk_size = _integer(item.get("chunk_size"), 1, 4 * 1024 * 1024)
    count = _integer(item.get("chunk_count"), 1)
    if count != (result["total"] + chunk_size - 1) // chunk_size:
        raise ValueError("inconsistent upload chunk geometry")
    received_count = _integer(received_count, maximum=count)
    received = _integer(received_bytes, maximum=result["total"])
    tail_size = result["total"] - (count - 1) * chunk_size
    possible_bytes = {received_count * chunk_size}
    if received_count:
        possible_bytes.add((received_count - 1) * chunk_size + tail_size)
    if received not in possible_bytes or (received_count == count and received != result["total"]):
        raise ValueError("inconsistent received upload counters")
    return {**result, "received": received, "chunk_count": count, "received_count": received_count}


def _legacy(path: Path, directory: Path):
    before = _checked_path(path, directory)
    if before.st_size > MAX_MANIFEST_BYTES:
        raise ValueError("oversized upload manifest")
    with path.open("rb") as handle:
        raw = handle.read(MAX_MANIFEST_BYTES + 1)
    after = _checked_path(path, directory)
    if (len(raw) > MAX_MANIFEST_BYTES
            or (before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns)
            != (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns)):
        raise ValueError("upload manifest changed during reading")
    item = _json(raw)
    result = _common(item, directory.name)
    chunks = item.get("chunks")
    if not isinstance(chunks, dict):
        raise ValueError("invalid upload chunks")
    count = item.get("chunk_count")
    if count is not None:
        _integer(count, 1, result["total"])
        if len(chunks) > count:
            raise ValueError("too many received chunks")
    chunk_size = item.get("chunk_size")
    if chunk_size is not None:
        _integer(chunk_size, 1, 4 * 1024 * 1024)
        if count is not None and count != (result["total"] + chunk_size - 1) // chunk_size:
            raise ValueError("inconsistent legacy upload chunk geometry")
    received = 0
    for index, chunk in chunks.items():
        if not isinstance(index, str) or not re.fullmatch(r"0|[1-9][0-9]{0,18}", index):
            raise ValueError("invalid chunk index")
        if count is not None and int(index) >= count:
            raise ValueError("chunk outside upload")
        if not isinstance(chunk, dict):
            raise ValueError("invalid chunk metadata")
        received += _integer(chunk.get("size"))
        if received > result["total"]:
            raise ValueError("received upload exceeds total")
    return {**result, "received": received, "chunk_count": count, "received_count": len(chunks)}


def read_uploads(data_root: Path, now: float) -> tuple[list[dict], int]:
    """Return recent unfinished sessions and a count of unreadable entries.

    A present SQLite state is authoritative, even when corrupt or locked; never
    display a stale legacy manifest in its place. Names are safe terminal text.
    The configured root may be a junction, but its upload subtree cannot contain
    links. A scan limit contributes one bad entry instead of silently truncating;
    many simultaneously locked sessions cannot stall the dashboard for minutes.
    """
    uploads, bad = [], 0
    try:
        now = _time(now)
        root = Path(data_root).resolve(strict=True)
        upload_root = root / "uploads"
        _checked_path(upload_root, root, directory=True)
        sessions = upload_root / ".chunks"
        _checked_path(sessions, upload_root, directory=True)
    except FileNotFoundError:
        return [], 0
    except (OSError, ValueError, TypeError, OverflowError):
        return [], 1
    try:
        with os.scandir(sessions) as entries:
            checked = 0
            deadline = time.monotonic() + MAX_SCAN_SECONDS
            for entry in entries:
                try:
                    if time.monotonic() >= deadline:
                        bad += 1
                        break
                    if not entry.is_dir(follow_symlinks=False) and not entry.is_symlink():
                        continue
                    checked += 1
                    if checked > MAX_SESSIONS:
                        bad += 1
                        break
                    directory = Path(entry.path)
                    _checked_path(directory, sessions, directory=True)
                    database = directory / STATE_FILENAME
                    manifest = directory / "manifest.json"
                    if os.path.lexists(database):
                        item = _sqlite(database, directory)
                    elif os.path.lexists(manifest):
                        item = _legacy(manifest, directory)
                    else:
                        continue  # Session creation has not published its state.
                    if now - item["updated"] > RECENT_SECONDS or item["job_id"] is not None:
                        continue
                    uploads.append(item)
                except (OSError, ValueError, TypeError, KeyError, OverflowError, RecursionError, sqlite3.Error):
                    bad += 1
    except OSError:
        bad += 1
    uploads.sort(key=lambda item: item["updated"], reverse=True)
    return uploads, bad
