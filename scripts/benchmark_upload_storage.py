"""Compare local resumable-upload storage, never network or GPU throughput.

Every case owns a temporary directory and the same deterministic byte stream.
PUT timings include server hashing, fsync and metadata commit, but exclude
payload generation, client checksum generation and the idle-job check. Complete
timing includes chunk verification, assembly, durable state and file handoff.
"""
from __future__ import annotations

import argparse
from contextlib import contextmanager
from collections import Counter, defaultdict
import hashlib
import json
import math
import os
from pathlib import Path
import tempfile
import time
import uuid
from unittest.mock import patch

from rallymate_service import upload_state
from rallymate_service.uploads import CHUNK_BYTES, MIN_CHUNK_BYTES, UploadStore
try:
    from scripts.benchmark_idle import IdleJobsGuard, require_idle
except ModuleNotFoundError as error:
    if error.name != "scripts":
        raise
    from benchmark_idle import IdleJobsGuard, require_idle


MIB = 1024 * 1024


def payload_at(offset: int, length: int) -> bytes:
    # Identical bytes for every chunk-size choice, with bounded host allocation.
    start = offset % 256
    return (bytes(range(256)) * ((start + length + 255) // 256))[start:start + length]


def payload_sha256(size: int) -> str:
    digest = hashlib.sha256()
    for offset in range(0, size, CHUNK_BYTES):
        digest.update(payload_at(offset, min(CHUNK_BYTES, size - offset)))
    return digest.hexdigest()


class _MeasuredStore(UploadStore):
    def __init__(self, *args, metrics, phase, **kwargs):
        super().__init__(*args, **kwargs)
        self.metrics, self.phase = metrics, phase

    def read(self, directory):
        manifest = directory / "manifest.json"
        if manifest.is_file() and not self._sqlite(directory):
            bucket = self.metrics[self.phase[0]]
            bucket["manifest_full_reads"] += 1
            bucket["manifest_json_bytes_read"] += manifest.stat().st_size
        return super().read(directory)

    def save(self, directory, data):
        super().save(directory, data)
        bucket = self.metrics[self.phase[0]]
        bucket["manifest_full_writes"] += 1
        bucket["manifest_json_bytes_written"] += (directory / "manifest.json").stat().st_size


@contextmanager
def _sqlite_metrics(metrics, phase):
    original = upload_state.connection
    @contextmanager
    def traced(directory):
        with original(directory) as conn:
            def observe(statement):
                query = " ".join(statement.upper().split())
                bucket = metrics[phase[0]]
                bucket["sqlite_statements"] += 1
                if "FROM CHUNKS" in query:
                    bucket["sqlite_chunk_lookup" if "WHERE CHUNK_INDEX=" in query else "sqlite_full_chunk_reads"] += 1
                if query.startswith("INSERT INTO CHUNKS"):
                    bucket["sqlite_chunk_inserts"] += 1
            conn.set_trace_callback(observe)
            yield conn
    with patch.object(upload_state, "connection", traced):
        yield


def run_case(*, backend: str, size: int, chunk_bytes: int, idle_jobs_db: Path,
             expected_sha256: str, temporary_parent: Path | None = None, idle_check=None) -> dict:
    if backend not in {"manifest", "sqlite"}:
        raise ValueError("Unknown upload storage backend")
    check_idle = idle_check if idle_check is not None else lambda: require_idle(idle_jobs_db)
    check_idle()
    metrics, phase = defaultdict(Counter), ["setup"]
    with tempfile.TemporaryDirectory(prefix="upload-storage-benchmark-", dir=temporary_parent) as temporary:
        working = Path(temporary)
        root = working / "chunks"
        store = _MeasuredStore(root, max(size, 1), chunk_bytes=chunk_bytes, metrics=metrics, phase=phase)
        upload_id = str(uuid.uuid4())
        directory = store.directory(upload_id)
        with _sqlite_metrics(metrics, phase):
            if backend == "manifest":
                directory.mkdir()
                store.save(directory, {"upload_id": upload_id, "filename": "benchmark.mp4", "size": size,
                    "chunk_size": chunk_bytes, "chunk_count": math.ceil(size / chunk_bytes),
                    "options": {}, "chunks": {}, "job_id": None})
            else:
                store.create(upload_id, "benchmark.mp4", size, {})
            expected_chunks = {}
            put_seconds = 0.
            phase[0] = "put"
            for index, offset in enumerate(range(0, size, chunk_bytes)):
                check_idle()
                content = payload_at(offset, min(chunk_bytes, size - offset))
                digest = hashlib.sha256(content).hexdigest()
                expected_chunks[str(index)] = digest
                started = time.perf_counter()
                receipt = store.put(upload_id, index, content, digest, compact_ack=True)
                put_seconds += time.perf_counter() - started
                if receipt["chunk_index"] != index or receipt["sha256"] != digest:
                    raise AssertionError("Chunk acknowledgement lost its source binding")
            phase[0] = "verification_excluded"
            store = _MeasuredStore(root, max(size, 1), chunk_bytes=chunk_bytes, metrics=metrics, phase=phase)
            restored = store.status(upload_id)
            if restored["chunk_sha256"] != expected_chunks or restored["received_bytes"] != size:
                raise AssertionError("Persisted resume state differs from uploaded bytes")
            first = payload_at(0, min(size, chunk_bytes))
            store.put(upload_id, 0, first, hashlib.sha256(first).hexdigest(), compact_ack=True)
            if store.status(upload_id) != restored:
                raise AssertionError("Duplicate chunk changed the resume state")
            jobs, enqueue_calls = {}, []
            handed_off = working / "verified.video"
            def enqueue(path, metadata):
                if metadata["upload_id"] != upload_id or path.stat().st_size != size:
                    raise AssertionError("Complete changed upload identity or size")
                path.replace(handed_off)
                enqueue_calls.append(upload_id)
                jobs[upload_id] = {"id": upload_id}
                return jobs[upload_id]
            check_idle()
            phase[0] = "complete"
            started = time.perf_counter()
            result = store.complete(upload_id, enqueue, jobs.get)
            complete_seconds = time.perf_counter() - started
            phase[0] = "verification_excluded"
            final_digest = hashlib.sha256()
            with handed_off.open("rb") as stream:
                for block in iter(lambda: stream.read(CHUNK_BYTES), b""):
                    final_digest.update(block)
            if final_digest.hexdigest() != expected_sha256:
                raise AssertionError("Assembled output differs from the source byte stream")
            if store.complete(upload_id, enqueue, jobs.get) != result or len(enqueue_calls) != 1:
                raise AssertionError("Completion is not idempotent")
            if backend == "sqlite" and metrics["put"]["sqlite_full_chunk_reads"]:
                raise AssertionError("SQLite PUT unexpectedly read the entire chunk history")
            metadata_size = sum(path.stat().st_size for path in directory.iterdir()
                                if path.name.startswith("state.sqlite3") or path.name == "manifest.json")
        return {
            "backend": backend, "source_size_bytes": size, "chunk_size_bytes": chunk_bytes,
            "chunk_count": math.ceil(size / chunk_bytes), "source_sha256": expected_sha256,
            "temporary_filesystem_path": str(working.resolve()),
            "put_store_seconds": put_seconds, "complete_store_seconds": complete_seconds,
            "put_storage_mib_per_second": size / MIB / put_seconds,
            "metadata_bytes_retained": metadata_size,
            "metadata_io": {key: dict(value) for key, value in metrics.items()},
            "verification": {"resumed_exactly": True, "duplicate_chunk_idempotent": True,
                "complete_idempotent": True, "enqueue_calls": len(enqueue_calls), "assembled_sha256_exact": True},
        }


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--size-mib", type=float, default=16)
    parser.add_argument("--chunk-kib", type=int, nargs="+", default=[128, 1024, 2048, 4096])
    parser.add_argument("--repeats", type=int, default=1)
    parser.add_argument("--temp-root", type=Path, help="Existing directory on the filesystem to measure")
    parser.add_argument("--idle-jobs-db", type=Path, required=True)
    parser.add_argument("--idle-jobs-python", type=Path,
                        help="Optional native-OS Python for a persistent read-only jobs bridge")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(argv)
    size = round(args.size_mib * MIB)
    chunks = [value * 1024 for value in args.chunk_kib]
    if not 1 <= size <= 512 * MIB or not 1 <= args.repeats <= 5:
        parser.error("size must be 1 byte..512 MiB, repeats must be 1..5")
    if (not chunks or len(chunks) != len(set(chunks)) or
            any(not MIN_CHUNK_BYTES <= value <= CHUNK_BYTES for value in chunks)):
        parser.error("chunk sizes must be distinct values in 128..4096 KiB")
    if args.output.exists():
        parser.error("output already exists; choose a new report path")
    if args.temp_root is not None and not args.temp_root.is_dir():
        parser.error("temp-root must be an existing directory")
    with IdleJobsGuard(args.idle_jobs_db, python=args.idle_jobs_python) as guard:
        guard.check()
        expected = payload_sha256(size)
        cases = []
        for repeat in range(args.repeats):
            for chunk_bytes in chunks:
                # Alternate order to reduce a consistent warm-filesystem advantage.
                for backend in (("manifest", "sqlite") if repeat % 2 == 0 else ("sqlite", "manifest")):
                    case = run_case(backend=backend, size=size, chunk_bytes=chunk_bytes,
                        idle_jobs_db=args.idle_jobs_db, expected_sha256=expected, temporary_parent=args.temp_root,
                        idle_check=guard.check)
                    case["repeat"] = repeat + 1
                    cases.append(case)
                    print(json.dumps({key: case[key] for key in ("backend", "repeat", "chunk_size_bytes", "put_store_seconds", "complete_store_seconds")}), flush=True)
    report = {
        "schema_version": "upload-storage-benchmark-v1", "status": "completed",
        "scope": "local_store_only_not_network_or_end_to_end_video_analysis",
        "durability": "file_fsync_and_atomic_part_replace; SQLite synchronous=FULL; both backends fsync_directory_on_POSIX",
        "timing": "sum_of_actual_store_calls; metadata_counter_and_SQL_trace_instrumentation_included; payload_generation_client_hash_idle_checks_resume_validation_and_final_hash_excluded",
        "metadata_byte_semantics": "actual legacy JSON file lengths at read/write boundaries; not physical block_IO_or_SQLite_WAL_IO; SQLite reports SQL operation counts",
        "transport": "none; no HTTP, Cloudflare, upload API or GPU inference",
        "handoff_scope": "both backends use the same simplified Path.replace callback; excludes API video probe, API directory fsync, durable job insertion, and the former second full-file copy; complete timing does not measure old-versus-new API handoff speed",
        "platform": os.name, "source_size_bytes": size, "source_sha256": expected, "cases": cases,
        "idle_guard": guard.receipt,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
