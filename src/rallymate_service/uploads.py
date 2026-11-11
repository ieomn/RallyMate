"""Durable byte-range uploads. Chunks are transport units, never video clips."""
from __future__ import annotations

import hashlib
import errno
import json
import math
import os
import shutil
import time
import threading
import uuid
from contextlib import contextmanager
from pathlib import Path

from fastapi import HTTPException

from rallymate_service import upload_state

# Preserve the legacy wire size as the API's maximum accepted chunk size.
CHUNK_BYTES = 4 * 1024 * 1024
MIN_CHUNK_BYTES = 128 * 1024
DEFAULT_CHUNK_BYTES = 1024 * 1024
SESSION_TTL_SECONDS = 24 * 60 * 60
UPLOAD_LOCK_TIMEOUT_SECONDS = 10.0
_local_locks_guard = threading.Lock()
_local_locks: dict[str, list] = {}


@contextmanager
def _local_file_lock(path: Path, deadline: float):
    """Wake local waiters directly and retain entries only while in use."""
    key = os.path.normcase(str(path.resolve()))
    with _local_locks_guard:
        entry = _local_locks.setdefault(key, [threading.Lock(), 0])
        entry[1] += 1
    acquired = False
    try:
        acquired = entry[0].acquire(timeout=max(0.0, deadline - time.monotonic()))
        if not acquired:
            raise TimeoutError("Timed out waiting for the upload session lock")
        yield
    finally:
        if acquired:
            entry[0].release()
        with _local_locks_guard:
            entry[1] -= 1
            if not entry[1]:
                del _local_locks[key]


def _acquire_windows_lock(handle, deadline: float) -> None:
    import msvcrt

    delay = 0.005
    while True:
        try:
            handle.seek(0)
            msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
            return
        except OSError as error:
            if error.errno not in {errno.EACCES, errno.EAGAIN, errno.EDEADLK}:
                raise
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise
            time.sleep(min(delay, remaining))
            delay = min(delay * 2, 0.02)


@contextmanager
def exclusive_file(path: Path):
    """Cross-process lock, released by the OS even if the server is killed."""
    if os.name == "nt":
        import msvcrt

        # LK_LOCK sleeps roughly one second after contention. A local lock
        # wakes same-process PUTs promptly; nonblocking OS attempts retain
        # cross-process exclusion without that fixed one-second stall.
        deadline = time.monotonic() + UPLOAD_LOCK_TIMEOUT_SECONDS
        with _local_file_lock(path, deadline), path.open("a+b") as handle:
            if os.fstat(handle.fileno()).st_size == 0:
                handle.write(b"0")
                handle.flush()
            _acquire_windows_lock(handle, deadline)
            try:
                yield
            finally:
                handle.seek(0)
                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
        return

    with path.open("a+b") as handle:
        import fcntl
        fcntl.flock(handle, fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(handle, fcntl.LOCK_UN)


class UploadStore:
    def __init__(self, root: Path, max_bytes: int, *, chunk_bytes: int | None = None):
        if chunk_bytes is None:
            chunk_bytes = int(os.environ.get("RALLYMATE_UPLOAD_CHUNK_BYTES", str(DEFAULT_CHUNK_BYTES)))
        if isinstance(chunk_bytes, bool) or not isinstance(chunk_bytes, int) or not MIN_CHUNK_BYTES <= chunk_bytes <= CHUNK_BYTES:
            raise ValueError("RALLYMATE_UPLOAD_CHUNK_BYTES must be between 131072 and 4194304")
        self.chunk_bytes = chunk_bytes
        self.root = root
        self.root.mkdir(parents=True, exist_ok=True)
        self.max_bytes = max_bytes

    def directory(self, upload_id: str) -> Path:
        try:
            if str(uuid.UUID(upload_id)) != upload_id:
                raise ValueError()
        except ValueError:
            raise HTTPException(404, "上传会话不存在") from None
        return self.root / upload_id

    @staticmethod
    def _check_expiry(data: dict) -> dict:
        if not data.get("job_id") and time.time() - data["updated_at"] > SESSION_TTL_SECONDS:
            raise HTTPException(410, "上传会话已过期，请重新开始上传")
        return data

    @staticmethod
    def _sqlite(directory: Path) -> bool:
        return (directory / upload_state.STATE_FILENAME).is_file()

    def read_metadata(self, directory: Path) -> dict:
        """Validate a session without loading its growing chunk history."""
        if self._sqlite(directory):
            with upload_state.connection(directory) as conn:
                return self._check_expiry(upload_state.header(conn))
        return self.read(directory)

    def read(self, directory: Path) -> dict:
        if self._sqlite(directory):
            with upload_state.connection(directory) as conn:
                return self._check_expiry(upload_state.snapshot(conn))
        try:
            data = json.loads((directory / "manifest.json").read_text("utf-8"))
        except FileNotFoundError:
            raise HTTPException(404, "上传会话不存在，请重新开始上传") from None
        return self._check_expiry(data)

    def save(self, directory: Path, data: dict):
        """Write a legacy session; SQLite state uses incremental transactions."""
        if self._sqlite(directory):
            raise ValueError("SQLite upload state must be updated incrementally")
        data["updated_at"] = time.time()
        temporary = directory / "manifest.tmp"
        with temporary.open("w", encoding="utf-8") as handle:
            json.dump(data, handle, ensure_ascii=False)
            handle.flush()
            os.fsync(handle.fileno())
        temporary.replace(directory / "manifest.json")
        upload_state.sync_directory(directory)

    @staticmethod
    def public(data: dict) -> dict:
        return {
            "upload_id": data["upload_id"], "size": data["size"],
            "chunk_size": data.get("chunk_size", CHUNK_BYTES), "chunk_count": data["chunk_count"],
            "received_chunks": sorted(int(i) for i in data["chunks"]),
            "chunk_sha256": {index: item["sha256"] for index, item in data["chunks"].items()},
            "received_bytes": sum(item["size"] for item in data["chunks"].values()),
            "job_id": data.get("job_id"), "expires_in_seconds": SESSION_TTL_SECONDS,
        }

    @staticmethod
    def chunk_receipt(data: dict, index: int) -> dict:
        """Constant-size acknowledgement; full history is only needed to resume."""
        item = data["chunks"][str(index)]
        return {
            "acknowledgement": "chunk-v1", "upload_id": data["upload_id"],
            "size": data["size"], "chunk_size": data.get("chunk_size", CHUNK_BYTES),
            "chunk_count": data["chunk_count"], "chunk_index": index,
            "chunk_bytes": item["size"], "sha256": item["sha256"],
        }

    def create(self, upload_id: str, filename: str, size: int, options: dict) -> dict:
        if isinstance(size, bool) or not isinstance(size, int) or not 0 < size <= self.max_bytes:
            raise HTTPException(413, "视频为空或超过上传大小限制")
        directory = self.directory(upload_id)
        with exclusive_file(self.root / ".create.lock"):
            self.cleanup()
            directory.mkdir(exist_ok=True)
            # Also sync an existing directory: it may be the remainder of a
            # creation attempt that failed before persisting its parent entry.
            upload_state.sync_directory(self.root)
            with exclusive_file(directory / ".lock"):
                if self._sqlite(directory) or (directory / "manifest.json").exists():
                    upload_state.sync_directory(directory)
                    data = self.read(directory)
                    if (data["filename"], data["size"], data["options"]) != (filename, size, options):
                        raise HTTPException(409, "上传会话与所选文件不一致")
                    if not data["chunks"] and not data.get("job_id") and data.get("chunk_size", CHUNK_BYTES) > self.chunk_bytes:
                        # The browser retries create with a new UUID on 410. Do
                        # not rewrite this manifest: an old in-flight PUT can
                        # still complete against its original chunk protocol.
                        raise HTTPException(410, "上传分块方式已更新，请重新建立上传会话")
                    return self.public(data)
                # Parts + assembled input + durable queue input coexist briefly.
                reserved = 0
                active = 0
                for session in self._session_directories():
                    item = self._unchecked_metadata(session)
                    if not item.get("job_id") and time.time() - item["updated_at"] <= SESSION_TTL_SECONDS:
                        reserved += item["size"]
                        active += 1
                if active >= 64:
                    raise HTTPException(429, "上传人数较多，请稍后继续")
                if shutil.disk_usage(self.root).free < 3 * (size + reserved) + 512 * 1024 * 1024:
                    raise HTTPException(507, "云端可用空间不足，请稍后重试")
                data = {"upload_id": upload_id, "filename": filename, "size": size,
                        "chunk_size": self.chunk_bytes,
                        "chunk_count": math.ceil(size / self.chunk_bytes), "options": options}
                upload_state.create(directory, data, time.time())
                return self.public({**data, "chunks": {}, "job_id": None})

    def status(self, upload_id: str) -> dict:
        # SQLite read transaction or atomic legacy manifest replacement.
        return self.public(self.read(self.directory(upload_id)))

    def put(self, upload_id: str, index: int, content: bytes, digest: str, *, compact_ack: bool = False) -> dict:
        directory = self.directory(upload_id)
        self.read_metadata(directory)
        if len(digest) != 64 or hashlib.sha256(content).hexdigest() != digest.lower():
            raise HTTPException(422, "分块校验失败，请重传此块")
        with exclusive_file(directory / ".lock"):
            if self._sqlite(directory):
                return self._put_sqlite(directory, index, content, digest.lower(), compact_ack)
            data = self.read(directory)
            if not 0 <= index < data["chunk_count"]:
                raise HTTPException(422, "无效的分块序号")
            chunk_size = data.get("chunk_size", CHUNK_BYTES)
            expected = min(chunk_size, data["size"] - index * chunk_size)
            if len(content) != expected:
                raise HTTPException(422, "分块长度不正确")
            prior = data["chunks"].get(str(index))
            if prior:
                if prior["sha256"] != digest.lower():
                    raise HTTPException(409, "同一分块已存在不同内容")
                return self.chunk_receipt(data, index) if compact_ack else self.public(data)
            if data.get("job_id"):
                raise HTTPException(409, "视频已提交推理")
            partial = directory / f"{index}.part.tmp"
            with partial.open("wb") as handle:
                handle.write(content)
                handle.flush()
                os.fsync(handle.fileno())
            partial.replace(directory / f"{index}.part")
            upload_state.sync_directory(directory)
            data["chunks"][str(index)] = {"sha256": digest.lower(), "size": len(content)}
            self.save(directory, data)
            return self.chunk_receipt(data, index) if compact_ack else self.public(data)

    def _put_sqlite(self, directory: Path, index: int, content: bytes, digest: str, compact_ack: bool) -> dict:
        with upload_state.connection(directory) as conn:
            data = self._check_expiry(upload_state.header(conn))
            if not 0 <= index < data["chunk_count"]:
                raise HTTPException(422, "无效的分块序号")
            chunk_size = data["chunk_size"]
            expected = min(chunk_size, data["size"] - index * chunk_size)
            if len(content) != expected:
                raise HTTPException(422, "分块长度不正确")
            prior = upload_state.chunk(conn, index)
            if prior is not None:
                if prior["sha256"] != digest:
                    raise HTTPException(409, "同一分块已存在不同内容")
            else:
                if data.get("job_id"):
                    raise HTTPException(409, "视频已提交推理")
                partial = directory / f"{index}.part.tmp"
                with partial.open("wb") as handle:
                    if handle.write(content) != len(content):
                        raise OSError("Incomplete upload chunk write")
                    handle.flush()
                    os.fsync(handle.fileno())
                partial.replace(directory / f"{index}.part")
                upload_state.sync_directory(directory)
                # Commit only after the part is durable. If this transaction
                # fails, the next identical request replaces the orphan file.
                upload_state.insert_chunk(conn, index, digest, len(content), time.time())
                prior = {"sha256": digest, "size": len(content)}
            if compact_ack:
                return self.chunk_receipt({**data, "chunks": {str(index): prior}}, index)
            return self.public(upload_state.snapshot(conn))

    def _mark_complete(self, directory: Path, data: dict, upload_id: str):
        data["job_id"] = upload_id
        if self._sqlite(directory):
            with upload_state.connection(directory) as conn:
                upload_state.mark_complete(conn, upload_id, time.time())
        else:
            self.save(directory, data)

    def _invalidate_chunk(self, directory: Path, data: dict, index: int):
        del data["chunks"][str(index)]
        if self._sqlite(directory):
            with upload_state.connection(directory) as conn:
                upload_state.remove_chunk(conn, index, time.time())
        else:
            self.save(directory, data)

    def complete(self, upload_id: str, enqueue, existing_job) -> dict:
        directory = self.directory(upload_id)
        self.read(directory)
        with exclusive_file(directory / ".lock"):
            data = self.read(directory)
            # Covers loss of the response and a crash after durable queue insertion.
            job = existing_job(upload_id)
            if job:
                self._mark_complete(directory, data, upload_id)
                self.remove_parts(directory)
                return job
            if len(data["chunks"]) != data["chunk_count"]:
                raise HTTPException(409, "还有分块未上传，请继续上传后再合并")
            merged = directory / "assembled.video"
            try:
                with merged.open("wb") as target:
                    for index in range(data["chunk_count"]):
                        part = directory / f"{index}.part"
                        content = part.read_bytes() if part.exists() else b""
                        expected = data["chunks"][str(index)]
                        if len(content) != expected["size"] or hashlib.sha256(content).hexdigest() != expected["sha256"]:
                            self._invalidate_chunk(directory, data, index)
                            raise HTTPException(409, "分块存储校验失败，请继续上传以补齐")
                        target.write(content)
                    target.flush()
                    os.fsync(target.fileno())
                if merged.stat().st_size != data["size"]:
                    raise HTTPException(422, "视频合并长度不正确")
                job = enqueue(merged, data)
                self._mark_complete(directory, data, upload_id)
                self.remove_parts(directory)
                return job
            finally:
                merged.unlink(missing_ok=True)

    @staticmethod
    def remove_parts(directory: Path):
        for part in directory.glob("*.part*"):
            part.unlink(missing_ok=True)

    def cleanup(self) -> int:
        """Remove stale partial bytes, retaining tombstones for safe 410 responses."""
        cleaned = 0
        for directory in self._session_directories():
            with exclusive_file(directory / ".lock"):
                data = self._unchecked_metadata(directory)
                if data.get("job_id") or time.time() - data["updated_at"] > SESSION_TTL_SECONDS:
                    self.remove_parts(directory)
                    (directory / "assembled.video").unlink(missing_ok=True)
                    cleaned += 1
        return cleaned

    def _session_directories(self):
        # Union prevents double counting if an operator left an old manifest
        # beside a new database. No live legacy session is migrated here.
        return sorted({path.parent for name in ("manifest.json", upload_state.STATE_FILENAME)
                       for path in self.root.glob("*/" + name)})

    def _unchecked_metadata(self, directory: Path) -> dict:
        if self._sqlite(directory):
            with upload_state.connection(directory) as conn:
                return upload_state.header(conn)
        return json.loads((directory / "manifest.json").read_text("utf-8"))
