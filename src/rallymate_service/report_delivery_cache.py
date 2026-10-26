"""Bounded, process-local delivery of already computed report evidence.

This queue never runs inference or scoring. Keys are supplied by the API from
the complete report dependency snapshot; a source check surrounds each build.
"""
from __future__ import annotations

from collections import OrderedDict
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from pathlib import Path
from threading import Lock
from typing import Callable, Hashable, Literal
import hashlib
import time
import uuid
from rallymate_service.report_delivery_store import ReportDeliveryStore


ReportDelivery = Literal["inline", "deferred-v1"]
REPORT_PROCESS_VERSION = "deferred-report-v1:" + uuid.uuid4().hex


def artifact_signature(path: Path, *, content_hash: bool = False) -> tuple:
    """Include replacement/change identity as well as length and modification time."""
    try:
        stat = path.stat()
        signature = (str(path.resolve()), stat.st_dev, stat.st_ino, stat.st_size,
                     stat.st_mtime_ns, stat.st_ctime_ns)
        if not content_hash:
            return signature
        digest = hashlib.sha256()
        with path.open("rb") as stream:
            for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                digest.update(chunk)
        if artifact_signature(path) != signature:
            raise OSError("report dependency changed while computing its identity")
        return (*signature, digest.hexdigest())
    except FileNotFoundError:
        return (str(path.resolve()), None)


class DeliveryError(Exception):
    def __init__(self, status: int, code: str, message: str):
        super().__init__(message)
        self.status = status
        self.code = code
        self.message = message

    def detail(self) -> dict:
        return {"code": self.code, "message": self.message}


@dataclass(frozen=True)
class DeliveryResult:
    state: Literal["preparing", "ready", "error"]
    body: bytes | None = None
    error: DeliveryError | None = None


class ReportDeliveryCache:
    def __init__(self, *, max_queued: int = 2, max_entries: int = 4,
                 max_bytes: int = 128 * 1024 * 1024, error_ttl_seconds: float = 5.0,
                 storage_dir: Path | None = None, preparation_timeout_seconds: float = 300.0):
        if (max_queued < 0 or max_entries < 1 or max_bytes < 1 or error_ttl_seconds < 0
                or preparation_timeout_seconds <= 0):
            raise ValueError("report delivery limits must be positive (queue may be zero)")
        self.max_queued = max_queued
        self.max_entries = max_entries
        self.max_bytes = max_bytes
        self.error_ttl_seconds = error_ttl_seconds
        self.preparation_timeout_seconds = preparation_timeout_seconds
        self._lock = Lock()
        self._executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="report-delivery")
        self._pending: set[Hashable] = set()
        self._pending_scopes: dict[Hashable, Hashable] = {}
        self._pending_started: dict[Hashable, float] = {}
        self._store = (ReportDeliveryStore(storage_dir, max_body_bytes=max_bytes)
                       if storage_dir is not None else None)
        self._completed: OrderedDict[Hashable, DeliveryResult] = OrderedDict()
        self._failed_at: dict[Hashable, float] = {}
        self._bytes = 0
        self._closed = False

    def is_preparing(self, scope: Hashable) -> bool:
        """Skip duplicate source scans only while a build is already pending.

        This never serves bytes. The API still validates job status/cancellation,
        and the worker checks the full source before and after preparing bytes.
        """
        with self._lock:
            return not self._closed and scope in self._pending_scopes

    @staticmethod
    def _timeout_error() -> DeliveryError:
        return DeliveryError(504, "report_preparation_timeout", "Report preparation exceeded its time limit.")

    def _pending_result_locked(self, key: Hashable) -> DeliveryResult:
        if time.monotonic() - self._pending_started[key] >= self.preparation_timeout_seconds:
            # Retain the execution slot until its worker exits. Python threads
            # cannot be safely killed, and timeout must not duplicate work.
            return DeliveryResult("error", error=self._timeout_error())
        return DeliveryResult("preparing")

    def pending_result(self, scope: Hashable) -> DeliveryResult | None:
        with self._lock:
            if self._closed or scope not in self._pending_scopes:
                return None
            return self._pending_result_locked(self._pending_scopes[scope])

    def _check_deadline(self, key: Hashable) -> None:
        with self._lock:
            if self._pending_result_locked(key).state == "error":
                raise self._timeout_error()

    def request(self, key: Hashable, build: Callable[[], bytes],
                source_is_current: Callable[[], bool], *, scope: Hashable | None = None,
                persistent_key: str | None = None) -> DeliveryResult:
        with self._lock:
            if self._closed:
                return DeliveryResult("error", error=DeliveryError(
                    503, "report_delivery_unavailable", "Report preparation is shutting down."))
            cached = self._completed.get(key)
            if cached is not None and cached.error is not None and (
                    time.monotonic() - self._failed_at[key] >= self.error_ttl_seconds):
                del self._completed[key]
                del self._failed_at[key]
                cached = None
            if cached is not None:
                self._completed.move_to_end(key)
                if cached.state != "ready":
                    return cached
            elif key in self._pending or (scope is not None and scope in self._pending_scopes):
                pending_key = key if key in self._pending else self._pending_scopes[scope]
                return self._pending_result_locked(pending_key)
            elif len(self._pending) >= self.max_queued + 1:
                return DeliveryResult("error", error=DeliveryError(
                    503, "report_preparation_queue_full", "Report preparation is busy; retry later."))
            else:
                self._pending.add(key)
                self._pending_started[key] = time.monotonic()
                if scope is not None:
                    self._pending_scopes[scope] = key
                self._executor.submit(self._build, key, build, source_is_current, scope, persistent_key)
                # Even a very quick build has the same deterministic first response.
                return DeliveryResult("preparing")
        # Never hold the queue lock while checking files/DB or invoking a caller.
        # A change after the API captured its key must invalidate a ready hit.
        try:
            current = source_is_current()
        except DeliveryError as exc:
            current = False
            source_error = exc
        except Exception:
            current = False
            source_error = None
        else:
            source_error = None
        if not current:
            with self._lock:
                if self._completed.get(key) is cached:
                    del self._completed[key]
                    self._bytes -= len(cached.body or b"")
            return DeliveryResult("error", error=source_error or DeliveryError(
                409, "report_source_changed", "Report source changed; request it again."))
        return cached

    def _build(self, key, build, source_is_current, scope=None, persistent_key=None):
        stored = False
        try:
            self._check_deadline(key)
            if not source_is_current():
                raise DeliveryError(409, "report_source_changed", "Report source changed; request it again.")
            body = (self._store.load(persistent_key)
                    if self._store is not None and persistent_key is not None else None)
            disk_hit = body is not None
            if body is None:
                body = build()
            self._check_deadline(key)
            if not isinstance(body, bytes):
                raise TypeError("report builder must return UTF-8 bytes")
            if not source_is_current():
                raise DeliveryError(409, "report_source_changed", "Report source changed; request it again.")
            if len(body) > self.max_bytes:
                raise DeliveryError(413, "report_delivery_limit", "Prepared report exceeds the delivery cache limit.")
            self._check_deadline(key)
            if not disk_hit and self._store is not None and persistent_key is not None:
                self._store.save(persistent_key, body)
                stored = True
            self._check_deadline(key)
            result = DeliveryResult("ready", body=body)
        except DeliveryError as exc:
            if stored and exc.code == "report_preparation_timeout":
                self._store.remove(persistent_key)
            result = DeliveryResult("error", error=exc)
        except Exception:
            # Do not leak file paths, credentials, or arbitrary exception text.
            result = DeliveryResult("error", error=DeliveryError(
                500, "report_preparation_failed", "Report preparation failed."))
        with self._lock:
            self._pending.discard(key)
            self._pending_started.pop(key, None)
            self._pending_scopes.pop(scope, None)
            if self._closed:
                return
            self._completed[key] = result
            if result.error is not None:
                self._failed_at[key] = time.monotonic()
            self._bytes += len(result.body or b"")
            while len(self._completed) > self.max_entries or self._bytes > self.max_bytes:
                old_key, old = self._completed.popitem(last=False)
                self._failed_at.pop(old_key, None)
                self._bytes -= len(old.body or b"")

    def stats(self) -> dict[str, int]:
        with self._lock:
            return {"pending": len(self._pending), "entries": len(self._completed), "bytes": self._bytes}

    def close(self) -> None:
        with self._lock:
            self._closed = True
            self._completed.clear()
            self._failed_at.clear()
            self._bytes = 0
        self._executor.shutdown(wait=True, cancel_futures=True)
        with self._lock:
            self._pending.clear()
            self._pending_scopes.clear()
            self._pending_started.clear()
