"""Disposable, content-addressed report bytes; source checks remain in the caller.

This is not an authority for scoring. A corrupt/missing cache is a miss, and a
restart can reuse bytes only with the same source and builder fingerprints.
"""
from __future__ import annotations

import hashlib
from importlib import metadata
import json
import os
from pathlib import Path
import re
import sys
import tempfile


def report_builder_fingerprint(source_root: Path) -> str:
    """Bind every local Python builder/helper and installed dependency version.

    Report rules and operator registries are separately content-hashed in each
    source snapshot. No mtime shortcut is used for code or installed versions.
    """
    sources = [(str(path.relative_to(source_root)).replace("\\", "/"),
                hashlib.sha256(path.read_bytes()).hexdigest())
               for path in sorted(source_root.glob("rallymate*/**/*.py"))]
    if not sources:
        raise ValueError("report builder sources are unavailable")
    distributions = sorted((dist.metadata.get("Name", ""), dist.version)
                           for dist in metadata.distributions())
    identity = ("report-bytes-v1", sys.version, sources, distributions)
    return hashlib.sha256(json.dumps(identity, separators=(",", ":")).encode()).hexdigest()


def persistent_report_key(builder_version: str, source_key: tuple) -> str:
    # The process nonce protects in-memory entries but must not defeat restart
    # reuse. Every other field, including all content digests, remains bound.
    encoded = json.dumps((builder_version, source_key[1:]), ensure_ascii=False,
                         separators=(",", ":"), allow_nan=False).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


class ReportDeliveryStore:
    def __init__(self, directory: Path, *, max_body_bytes: int,
                 max_entries: int = 16, max_bytes: int = 512 * 1024 * 1024):
        if min(max_body_bytes, max_entries, max_bytes) < 1:
            raise ValueError("report disk cache limits must be positive")
        self.directory = Path(directory)
        self.max_body_bytes = max_body_bytes
        self.max_entries = max_entries
        self.max_bytes = max_bytes

    def _path(self, key: str) -> Path:
        if not re.fullmatch(r"[0-9a-f]{64}", key):
            raise ValueError("report disk key must be a SHA-256 digest")
        return self.directory / (key + ".report")

    def load(self, key: str) -> bytes | None:
        path = self._path(key)
        try:
            if path.stat().st_size > self.max_body_bytes + 1024:
                return None
            with path.open("rb") as stream:
                header = stream.readline(1024)
                if not header.endswith(b"\n"):
                    return None
                manifest = json.loads(header)
                body = stream.read(self.max_body_bytes + 1)
            if (manifest.get("version") != 1 or manifest.get("key") != key
                    or type(manifest.get("body_bytes")) is not int
                    or manifest["body_bytes"] != len(body)
                    or len(body) > self.max_body_bytes
                    or manifest.get("body_sha256") != hashlib.sha256(body).hexdigest()):
                return None
            # LRU is only storage bookkeeping, never source freshness evidence.
            os.utime(path, None)
            return body
        except (OSError, ValueError, TypeError, AttributeError):
            return None

    def save(self, key: str, body: bytes) -> None:
        path = self._path(key)
        if len(body) > self.max_body_bytes:
            return
        manifest = {"version": 1, "key": key, "body_bytes": len(body),
                    "body_sha256": hashlib.sha256(body).hexdigest()}
        temporary = None
        try:
            self.directory.mkdir(parents=True, exist_ok=True)
            with tempfile.NamedTemporaryFile(dir=self.directory, suffix=".tmp", delete=False) as stream:
                temporary = Path(stream.name)
                stream.write(json.dumps(manifest, separators=(",", ":")).encode() + b"\n")
                stream.write(body)
            # Manifest and body are one atomic replacement, including on Windows.
            os.replace(temporary, path)
            temporary = None
            entries = sorted(self.directory.glob("*.report"), key=lambda item: item.stat().st_mtime_ns)
            sizes = {item: item.stat().st_size for item in entries}
            total = sum(sizes.values())
            while len(entries) > self.max_entries or total > self.max_bytes:
                old = entries.pop(0)
                old.unlink(missing_ok=True)
                total -= sizes[old]
        except OSError:
            # Storage pressure must not hide an otherwise valid report.
            pass
        finally:
            if temporary is not None:
                try:
                    temporary.unlink(missing_ok=True)
                except OSError:
                    pass

    def remove(self, key: str) -> None:
        try:
            self._path(key).unlink(missing_ok=True)
        except OSError:
            pass
