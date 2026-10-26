"""Optional bounded mesh archiving while the next SAM frame is inferred.

Only disk verification/moves run in the worker. The caller owns publication of
native records and the mesh ledger, in returned batch order, after verification.
Closing joins the worker before caller-owned output files may be closed/reused.
"""
from __future__ import annotations

from collections import deque
from concurrent.futures import Future, ThreadPoolExecutor
from copy import deepcopy
from dataclasses import dataclass
from pathlib import Path
import sys
import time
from typing import Callable

from rallymate_vision.pose.metadata import sha256_file
from rallymate_vision.sam3d_mesh import archive_mesh_records


@dataclass(frozen=True)
class ArchivedMeshBatch:
    """One submitted frame, including all its successful/skipped/failed rows."""

    sequence: int
    records: list[dict]
    mesh_records: list[dict]
    archive_seconds: float


class BoundedMeshArchiver:
    """Single-owner, FIFO disk work with at most two retained frame batches.

    This is opt-in: callers construct it only when the feature is enabled.
    No model, image, dtype, geometry, hashing or containment policy is changed.
    submit/collect/finish must be called by one control thread; the private
    worker never publishes records or writes caller-owned JSONL streams.
    """

    def __init__(self, output_dir: Path, runtime_dir: Path, *, max_pending_frames: int = 2,
                 _archive: Callable | None = None):
        if type(max_pending_frames) is not int or not 1 <= max_pending_frames <= 2:
            raise ValueError("asynchronous mesh archive accepts one or two pending frames")
        self.output_dir = Path(output_dir)
        self.runtime_dir = Path(runtime_dir)
        self.max_pending_frames = max_pending_frames
        self._archive = archive_mesh_records if _archive is None else _archive
        self._source_sha256 = sha256_file(Path(__file__))
        self._pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix="sam-mesh-archive")
        self._pending: deque[Future] = deque()
        self._accepting = True
        self._closed = False
        self._submitted = 0
        self._collected = 0
        self._peak_pending = 0
        self._background_seconds = 0.0
        self._wait_seconds = 0.0
        self._copy_seconds = 0.0

    @property
    def receipt(self) -> dict:
        return {
            "policy": "bounded_mesh_archive_v1",
            "source_sha256": self._source_sha256,
            "enabled": True,
            "worker_threads": 1,
            "max_pending_frames": self.max_pending_frames,
            "submitted_frames": self._submitted,
            "collected_frames": self._collected,
            "pending_frames": len(self._pending),
            "peak_pending_frames": self._peak_pending,
            "archive_background_seconds": self._background_seconds,
            "foreground_wait_seconds": self._wait_seconds,
            "record_copy_seconds": self._copy_seconds,
            "accepting": self._accepting,
            "joined": self._closed,
            "closed": self._closed,
            "timing_semantics": "background archive may overlap inference; do not add it to wall-clock parent stages",
            "integrity_policy": "unchanged archive_mesh_records checksum and path-containment checks before publication",
        }

    def _check_open(self) -> None:
        if not self._accepting:
            raise RuntimeError("mesh archive is closed")

    def _run(self, sequence: int, records: list[dict]) -> ArchivedMeshBatch:
        started = time.perf_counter()
        mesh_records = self._archive(records, self.output_dir, self.runtime_dir)
        return ArchivedMeshBatch(sequence, records, mesh_records, time.perf_counter() - started)

    def _collect_one(self) -> ArchivedMeshBatch:
        future = self._pending[0]
        started = time.perf_counter()
        try:
            batch = future.result()
        except BaseException:
            # No later result may be published after an earlier archive failed.
            # Wait for any already-started disk work before propagating failure.
            self.close()
            raise
        finally:
            self._wait_seconds += time.perf_counter() - started
        self._pending.popleft()
        self._collected += 1
        self._background_seconds += batch.archive_seconds
        return batch

    def collect_ready(self) -> list[ArchivedMeshBatch]:
        """Return completed batches in source order without waiting on new work."""
        self._check_open()
        result = []
        while self._pending and self._pending[0].done():
            result.append(self._collect_one())
        return result

    def submit(self, records: list[dict]) -> list[ArchivedMeshBatch]:
        """Queue an owned copy; return earlier results to publish before continuing.

        Backpressure waits for the oldest result before accepting a third frame.
        This bound includes both queued work and completed but uncollected work.
        The original records, including nested artifacts and bindings, remain
        untouched while the next frame may update the adapter's last_records.
        """
        self._check_open()
        if not isinstance(records, list) or any(not isinstance(row, dict) for row in records):
            raise TypeError("mesh archive requires a list of native record objects")
        ready = self.collect_ready()
        while len(self._pending) >= self.max_pending_frames:
            ready.append(self._collect_one())
        started = time.perf_counter()
        owned = deepcopy(records)
        self._copy_seconds += time.perf_counter() - started
        future = self._pool.submit(self._run, self._submitted, owned)
        self._pending.append(future)
        self._submitted += 1
        self._peak_pending = max(self._peak_pending, len(self._pending))
        return ready

    def finish(self) -> list[ArchivedMeshBatch]:
        """Drain in order and join before scoring, hashing ledgers or task creation."""
        self._check_open()
        result = []
        try:
            while self._pending:
                result.append(self._collect_one())
        except BaseException:
            # Retry a failed join, while preserving the original archive error.
            self.close()
            raise
        self.close()
        return result

    def close(self) -> None:
        """Abort uncollected work safely; never publish records from cleanup.

        In-progress file verification/moves cannot be interrupted safely and are
        joined. Any moved but unpublished mesh stays inside this job; a failed
        or cancelled job cannot become a ready render task through this object.
        Idempotent cleanup must run before caller-owned native/ledger streams
        close. Call finish, and publish its returned batches, on normal success.
        """
        if self._closed:
            return
        self._accepting = False
        primary_error = sys.exc_info()[1]
        try:
            for future in self._pending:
                future.cancel()
            self._pool.shutdown(wait=True, cancel_futures=True)
        except BaseException as cleanup_error:
            # ExitStack invokes close while an archive/cancellation exception
            # is active. Keep that primary error, with cleanup failure as its
            # explicit cause. A failed/interrupted join must remain retryable.
            if primary_error is not None and primary_error is not cleanup_error:
                raise primary_error from cleanup_error
            raise
        self._pending.clear()
        self._closed = True
