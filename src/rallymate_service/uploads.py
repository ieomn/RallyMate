"""Durable byte-range uploads. Chunks are transport units, never video clips."""
from __future__ import annotations

import hashlib
import json
import math
import os
import shutil
import time
import uuid
from contextlib import contextmanager
from pathlib import Path

from fastapi import HTTPException

# Preserve the legacy wire size as the API's maximum accepted chunk size.
CHUNK_BYTES = 4 * 1024 * 1024
DEFAULT_CHUNK_BYTES = 128 * 1024
SESSION_TTL_SECONDS = 24 * 60 * 60


@contextmanager
def exclusive_file(path: Path):
    """Cross-process lock, released by the OS even if the server is killed."""
    with path.open("a+b") as handle:
        if os.name == "nt":
            import msvcrt
            if path.stat().st_size == 0:
                handle.write(b"0")
                handle.flush()
            handle.seek(0)
            msvcrt.locking(handle.fileno(), msvcrt.LK_LOCK, 1)
        else:
            import fcntl
            fcntl.flock(handle, fcntl.LOCK_EX)
        try:
            yield
        finally:
            if os.name == "nt":
                handle.seek(0)
                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(handle, fcntl.LOCK_UN)


class UploadStore:
    def __init__(self, root: Path, max_bytes: int):
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

    def read(self, directory: Path) -> dict:
        try:
            data = json.loads((directory / "manifest.json").read_text("utf-8"))
        except FileNotFoundError:
            raise HTTPException(404, "上传会话不存在，请重新开始上传") from None
        if not data.get("job_id") and time.time() - data["updated_at"] > SESSION_TTL_SECONDS:
            raise HTTPException(410, "上传会话已过期，请重新开始上传")
        return data

    def save(self, directory: Path, data: dict):
        data["updated_at"] = time.time()
        temporary = directory / "manifest.tmp"
        with temporary.open("w", encoding="utf-8") as handle:
            json.dump(data, handle, ensure_ascii=False)
            handle.flush()
            os.fsync(handle.fileno())
        temporary.replace(directory / "manifest.json")

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

    def create(self, upload_id: str, filename: str, size: int, options: dict) -> dict:
        if isinstance(size, bool) or not isinstance(size, int) or not 0 < size <= self.max_bytes:
            raise HTTPException(413, "视频为空或超过上传大小限制")
        directory = self.directory(upload_id)
        with exclusive_file(self.root / ".create.lock"):
            self.cleanup()
            directory.mkdir(exist_ok=True)
            with exclusive_file(directory / ".lock"):
                if (directory / "manifest.json").exists():
                    data = self.read(directory)
                    if (data["filename"], data["size"], data["options"]) != (filename, size, options):
                        raise HTTPException(409, "上传会话与所选文件不一致")
                    if not data["chunks"] and not data.get("job_id") and data.get("chunk_size", CHUNK_BYTES) > DEFAULT_CHUNK_BYTES:
                        # The browser retries create with a new UUID on 410. Do
                        # not rewrite this manifest: an old in-flight PUT can
                        # still complete against its original chunk protocol.
                        raise HTTPException(410, "上传分块方式已更新，请重新建立上传会话")
                    return self.public(data)
                # Parts + assembled input + durable queue input coexist briefly.
                reserved = 0
                active = 0
                for manifest in self.root.glob("*/manifest.json"):
                    item = json.loads(manifest.read_text("utf-8"))
                    if not item.get("job_id") and time.time() - item["updated_at"] <= SESSION_TTL_SECONDS:
                        reserved += item["size"]
                        active += 1
                if active >= 64:
                    raise HTTPException(429, "上传人数较多，请稍后继续")
                if shutil.disk_usage(self.root).free < 3 * (size + reserved) + 512 * 1024 * 1024:
                    raise HTTPException(507, "云端可用空间不足，请稍后重试")
                data = {"upload_id": upload_id, "filename": filename, "size": size,
                        "chunk_size": DEFAULT_CHUNK_BYTES,
                        "chunk_count": math.ceil(size / DEFAULT_CHUNK_BYTES), "options": options,
                        "chunks": {}, "job_id": None}
                self.save(directory, data)
                return self.public(data)

    def status(self, upload_id: str) -> dict:
        # Atomic manifest replacement makes unlocked reads safe.
        return self.public(self.read(self.directory(upload_id)))

    def put(self, upload_id: str, index: int, content: bytes, digest: str) -> dict:
        directory = self.directory(upload_id)
        self.read(directory)
        if len(digest) != 64 or hashlib.sha256(content).hexdigest() != digest.lower():
            raise HTTPException(422, "分块校验失败，请重传此块")
        with exclusive_file(directory / ".lock"):
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
                return self.public(data)
            if data.get("job_id"):
                raise HTTPException(409, "视频已提交推理")
            partial = directory / f"{index}.part.tmp"
            with partial.open("wb") as handle:
                handle.write(content)
                handle.flush()
                os.fsync(handle.fileno())
            partial.replace(directory / f"{index}.part")
            data["chunks"][str(index)] = {"sha256": digest.lower(), "size": len(content)}
            self.save(directory, data)
            return self.public(data)

    def complete(self, upload_id: str, enqueue, existing_job) -> dict:
        directory = self.directory(upload_id)
        self.read(directory)
        with exclusive_file(directory / ".lock"):
            data = self.read(directory)
            # Covers loss of the response and a crash after durable queue insertion.
            job = existing_job(upload_id)
            if job:
                data["job_id"] = upload_id
                self.save(directory, data)
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
                            del data["chunks"][str(index)]
                            self.save(directory, data)
                            raise HTTPException(409, "分块存储校验失败，请继续上传以补齐")
                        target.write(content)
                    target.flush()
                    os.fsync(target.fileno())
                if merged.stat().st_size != data["size"]:
                    raise HTTPException(422, "视频合并长度不正确")
                job = enqueue(merged, data)
                data["job_id"] = upload_id
                self.save(directory, data)
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
        for manifest in self.root.glob("*/manifest.json"):
            with exclusive_file(manifest.parent / ".lock"):
                data = json.loads(manifest.read_text("utf-8"))
                if data.get("job_id") or time.time() - data["updated_at"] > SESSION_TTL_SECONDS:
                    self.remove_parts(manifest.parent)
                    (manifest.parent / "assembled.video").unlink(missing_ok=True)
                    cleaned += 1
        return cleaned
