"""Versioned, content-addressed evidence for scoring artifacts.

Only the persisted representation changes. Readers restore the original values,
and a bounded cache shares repeated read-only evidence between records. Never
mutate a nested value returned by a reader: it may belong to another record too.
"""
from __future__ import annotations

from collections import OrderedDict
from collections.abc import Callable, Iterable, Iterator
import hashlib
import json
import math
import os
from pathlib import Path
import sqlite3
import sys
import tempfile
from typing import Any


CODEC_VERSION = "scoring-artifact-evidence-v1"
POOL_FILENAME = "scoring-evidence.sqlite3"
REFERENCE_KEY = "__rallymate_evidence_ref__"
EVIDENCE_REFERENCE_KEY = REFERENCE_KEY
DEFAULT_MAX_RECORD_BYTES = 64 * 1024 * 1024
DEFAULT_CACHE_BYTES = 64 * 1024 * 1024
_MIN_POOL_BYTES = 4096
_JSON_FAST_MAX_BYTES = 1024 * 1024
_JSON_FAST_MAX_NODES = 16384
_JSON_FAST_MAX_DEPTH = 64
_POOL_FIELDS = frozenset({
    "source_pose_person_association", "source_window", "geometry_mask",
    "camera_compensation", "frame_dimensions", "source_time_binding",
})


class ArtifactEvidenceError(ValueError):
    """An evidence artifact is invalid, foreign, missing, or over budget."""


def contains_artifact_references(value: Any) -> bool:
    """Detect codec records before an offline loader freezes JSONL without its pool."""
    stack = [iter((value,))]
    while stack:
        try:
            item = next(stack[-1])
        except StopIteration:
            stack.pop()
            continue
        if isinstance(item, dict):
            if REFERENCE_KEY in item:
                return True
            stack.append(iter(item.values()))
        elif isinstance(item, list):
            stack.append(iter(item))
    return False


def _check(callback: Callable[[], None] | None) -> None:
    if callback is not None:
        callback()


def _digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _strict_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    value = {}
    for key, item in pairs:
        if key in value:
            raise ArtifactEvidenceError("duplicate JSON key in evidence pool")
        value[key] = item
    return value


def _reject_constant(value: str) -> None:
    raise ArtifactEvidenceError(f"non-finite JSON value in evidence pool: {value}")


def _strict_loads(payload: bytes | str) -> Any:
    return json.loads(payload, object_pairs_hook=_strict_object, parse_constant=_reject_constant)


def resident_evidence_bytes(value: Any, limit: int) -> int:
    """Conservatively charge Python objects, not their much smaller JSON text."""
    total = 0
    seen: set[int] = set()
    pending = [value]
    while pending:
        item = pending.pop()
        if id(item) in seen:
            continue
        seen.add(id(item))
        total += sys.getsizeof(item)
        if total > limit:
            return total
        if isinstance(item, dict):
            pending.extend(item.keys())
            pending.extend(item.values())
        elif isinstance(item, list):
            pending.extend(item)
    return total


def _bounded_json_snapshot(value: Any, max_bytes: int, allow_nan: bool,
                           cancellation_callback: Callable[[], None] | None) -> tuple[Any, int] | None:
    """Prove a small UTF-8 bound while copying only bounded built-in containers.

    Immutable scalars are shared. Private containers prevent a callback or
    another thread from enlarging an admitted object before the C encoder runs.
    Unsupported inputs retain the original streaming encoder's error behavior.
    """
    if type(max_bytes) is not int or max_bytes <= 0 or type(allow_nan) is not bool:
        return None
    limit = min(max_bytes, _JSON_FAST_MAX_BYTES)
    fallback = object()
    active: set[int] = set()
    nodes = size = 0

    def reserve(amount: int) -> bool:
        nonlocal size
        size += amount
        return size <= limit

    def copy(item, depth):
        nonlocal nodes
        nodes += 1
        if nodes % 256 == 0:
            _check(cancellation_callback)
        if nodes > _JSON_FAST_MAX_NODES or depth > _JSON_FAST_MAX_DEPTH:
            return fallback
        kind = type(item)
        if item is None:
            return item if reserve(4) else fallback
        if kind is bool:
            return item if reserve(4 if item else 5) else fallback
        if kind is int:
            # 0.30103 > log10(2): upper bound decimal digits without converting
            # an arbitrarily large integer to a temporary decimal string.
            bound = max(1, (item.bit_length() * 30103) // 100000 + 1) + (item < 0)
            return item if reserve(bound) else fallback
        if kind is float:
            if not allow_nan and not math.isfinite(item):
                return fallback
            # CPython's shortest binary64 representation, including signs and
            # exponents/nonfinite spellings, fits conservatively in 32 bytes.
            return item if reserve(32) else fallback
        if kind is str:
            # Each code point needs at most six JSON/UTF-8 bytes (\u00xx).
            if not reserve(6 * len(item) + 2):
                return fallback
            if not item.isascii():
                try:
                    item.encode("utf-8")
                except UnicodeEncodeError:
                    return fallback  # Preserve the streaming error location.
            return item
        if kind not in (list, tuple, dict) or id(item) in active:
            return fallback
        if not reserve(2):
            return fallback
        active.add(id(item))
        try:
            if kind is dict:
                result = {}
                for key, child in item.items():
                    if type(key) is not str:
                        return fallback  # Preserve mixed-key sorting semantics.
                    if not reserve(1 + bool(result)):  # colon and optional comma
                        return fallback
                    copied_key, copied_child = copy(key, depth + 1), copy(child, depth + 1)
                    if copied_key is fallback or copied_child is fallback:
                        return fallback
                    result[copied_key] = copied_child
            else:
                result = []
                for child in item:
                    if result and not reserve(1):
                        return fallback
                    copied = copy(child, depth + 1)
                    if copied is fallback:
                        return fallback
                    result.append(copied)
            return result
        finally:
            active.remove(id(item))

    snapshot = copy(value, 0)
    return None if snapshot is fallback else (snapshot, size)


def _json_bytes(value: Any, *, max_bytes: int = DEFAULT_MAX_RECORD_BYTES,
                allow_nan: bool = True,
                cancellation_callback: Callable[[], None] | None = None) -> bytes:
    _check(cancellation_callback)
    admitted = _bounded_json_snapshot(value, max_bytes, allow_nan, cancellation_callback)
    if admitted is not None:
        snapshot, upper_bound = admitted
        _check(cancellation_callback)
        encoded = json.dumps(snapshot, ensure_ascii=False, sort_keys=True,
                             separators=(",", ":"), allow_nan=allow_nan).encode("utf-8")
        _check(cancellation_callback)
        if len(encoded) > upper_bound:
            raise ArtifactEvidenceError("Internal JSON encoding bound was exceeded")
        return encoded
    output = bytearray()
    size = 0
    encoder = json.JSONEncoder(ensure_ascii=False, sort_keys=True,
                               separators=(",", ":"), allow_nan=allow_nan)
    for index, chunk in enumerate(encoder.iterencode(value)):
        if index % 4096 == 0:
            _check(cancellation_callback)
        data = chunk.encode("utf-8")
        size += len(data)
        if size > max_bytes:
            raise ArtifactEvidenceError(
                f"评分证据单条记录超过内存预算（{max_bytes} bytes）；处理已停止，原始产物保留"
            )
        output.extend(data)
    return bytes(output)


def _binding(video_id: str, source_id: str, video_sha256: str | None) -> dict:
    if not isinstance(video_id, str) or not video_id or not isinstance(source_id, str) or not source_id:
        raise ArtifactEvidenceError("evidence video_id/source_id must be non-empty strings")
    if video_sha256 is not None and (
        not isinstance(video_sha256, str) or len(video_sha256) != 64
        or any(c not in "0123456789abcdefABCDEF" for c in video_sha256)
    ):
        raise ArtifactEvidenceError("evidence video_sha256 must be a SHA-256")
    return {"codec": CODEC_VERSION, "video_id": video_id, "source_id": source_id,
            "video_sha256": video_sha256.lower() if video_sha256 else None}


def _file_sha256(path: Path, callback: Callable[[], None] | None = None) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            _check(callback)
            digest.update(chunk)
    return digest.hexdigest()


def _write_evidence_file(directory, filename, rows, *, binding, max_record_bytes,
                         allow_nan, cancellation_callback=None):
    """Process entry point; uses exactly the serial evidence encoder."""
    with EvidencePoolWriter(directory, video_id=binding["video_id"],
            source_id=binding["source_id"], video_sha256=binding["video_sha256"],
            max_record_bytes=max_record_bytes, allow_nan=allow_nan,
            cancellation_callback=cancellation_callback) as writer:
        writer.write_jsonl(Path(directory) / filename, rows)
    return len(rows)


class EvidencePoolWriter:
    """Stage compact JSONL files and publish one immutable SQLite evidence pool."""

    def __init__(self, output_dir: str | Path, *, video_id: str, source_id: str,
                 video_sha256: str | None = None,
                 cancellation_callback: Callable[[], None] | None = None,
                 max_record_bytes: int = DEFAULT_MAX_RECORD_BYTES,
                 allow_nan: bool = True):
        self.directory = Path(output_dir).resolve()
        self.directory.mkdir(parents=True, exist_ok=True)
        if type(max_record_bytes) is not int or not 0 < max_record_bytes <= DEFAULT_MAX_RECORD_BYTES:
            raise ArtifactEvidenceError("record byte budget must be positive and at most 64 MiB")
        self.callback = cancellation_callback
        self.max_record_bytes = max_record_bytes
        self.allow_nan = allow_nan
        self.binding = _binding(video_id, source_id, video_sha256)
        self.binding_sha256 = _digest(_json_bytes(self.binding))
        self._identity: OrderedDict[int, tuple[Any, Any]] = OrderedDict()
        self._staged: list[tuple[Path, Path]] = []
        self._closed = False
        self.manifest: dict[str, Any] | None = None
        fd, temporary = tempfile.mkstemp(prefix=".scoring-evidence-", suffix=".sqlite3", dir=self.directory)
        os.close(fd)
        self._temporary = Path(temporary)
        self.connection = sqlite3.connect(temporary)
        self.connection.execute("PRAGMA journal_mode=DELETE")
        self.connection.execute("PRAGMA synchronous=FULL")
        self.connection.execute("CREATE TABLE metadata (key TEXT PRIMARY KEY, value TEXT NOT NULL)")
        self.connection.execute("CREATE TABLE evidence (sha256 TEXT PRIMARY KEY, payload BLOB NOT NULL)")
        self.connection.execute("INSERT INTO metadata VALUES ('binding', ?)",
                                (_json_bytes(self.binding).decode("utf-8"),))

    def __enter__(self) -> "EvidencePoolWriter":
        return self

    def _encode(self, value: Any, field: str = "") -> Any:
        if isinstance(value, dict) and REFERENCE_KEY in value:
            raise ArtifactEvidenceError("input evidence contains a reserved storage reference")
        if isinstance(value, (dict, list)) and field in _POOL_FIELDS:
            identity = id(value)
            cached = self._identity.get(identity)
            if cached is not None and cached[0] is value:
                self._identity.move_to_end(identity)
                return cached[1]
            _check(self.callback)
            packed = self._encode_children(value)
            payload = _json_bytes(packed, max_bytes=self.max_record_bytes, allow_nan=False,
                                  cancellation_callback=self.callback)
            if len(payload) >= _MIN_POOL_BYTES:
                digest = _digest(payload)
                self.connection.execute("INSERT OR IGNORE INTO evidence VALUES (?, ?)", (digest, payload))
                packed = {REFERENCE_KEY: {
                    "codec": CODEC_VERSION, "pool": POOL_FILENAME,
                    "binding_sha256": self.binding_sha256, "sha256": digest,
                }}
            self._identity[identity] = (value, packed)
            # Retaining an object protects against id reuse. Cap this optimization;
            # the computation already owns the immutable evidence itself.
            while len(self._identity) > 4096:
                self._identity.popitem(last=False)
            return packed
        return self._encode_children(value)

    def _encode_children(self, value: Any) -> Any:
        if isinstance(value, dict):
            # Only containers can hold a storage reference or a pooled field.
            # Native geometry contains millions of scalar coordinates: keep
            # these immutable leaves in place instead of dispatching twice per
            # number through _encode and _encode_children.
            return {key: self._encode(item, key) if isinstance(item, (dict, list)) else item
                    for key, item in value.items()}
        if isinstance(value, list):
            return [self._encode(item) if isinstance(item, (dict, list)) else item for item in value]
        return value

    def write_jsonl(self, path: str | Path, records: Iterable[dict[str, Any]]) -> None:
        destination = Path(path).resolve()
        if destination.parent != self.directory or destination.name == POOL_FILENAME:
            raise ArtifactEvidenceError("evidence JSONL output must be in its run directory")
        fd, temporary = tempfile.mkstemp(prefix="." + destination.name + "-", suffix=".tmp", dir=self.directory)
        staged = Path(temporary)
        self._staged.append((staged, destination))
        with os.fdopen(fd, "wb") as handle:
            for record in records:
                _check(self.callback)
                if not isinstance(record, dict):
                    raise ArtifactEvidenceError("artifact records must be objects")
                handle.write(_json_bytes(self._encode(record), max_bytes=self.max_record_bytes,
                                         allow_nan=self.allow_nan,
                                         cancellation_callback=self.callback) + b"\n")
            handle.flush()
            os.fsync(handle.fileno())

    def write_jsonl_parallel(self, outputs, *, cpu_pool, progress_callback=None) -> None:
        """Encode independent files on CPUs, retaining one ordered evidence pool.

        Each child runs the ordinary validating writer in its own staging
        directory. Only the parent merges evidence and publishes destinations.
        Original file order and first occurrence of every payload are preserved.
        """
        destinations = []
        for path, rows in outputs:
            destination = Path(path).resolve()
            if destination.parent != self.directory or destination.name == POOL_FILENAME:
                raise ArtifactEvidenceError("evidence JSONL output must be in its run directory")
            destinations.append((destination, rows))
        with tempfile.TemporaryDirectory(prefix=".scoring-cpu-", dir=self.directory) as temporary:
            staging = Path(temporary).resolve()
            if staging.parent != self.directory:
                raise ArtifactEvidenceError("CPU evidence staging escaped its run directory")
            tasks = []
            for index, (destination, rows) in enumerate(destinations):
                tasks.append(((str(staging / str(index)), destination.name, rows), {
                    "binding": self.binding, "max_record_bytes": self.max_record_bytes,
                    "allow_nan": self.allow_nan,
                }))
            for index, count in enumerate(cpu_pool.ordered_map(_write_evidence_file, tasks)):
                _check(self.callback)
                child_directory = staging / str(index)
                connection = sqlite3.connect(child_directory / POOL_FILENAME)
                try:
                    binding = connection.execute(
                        "SELECT value FROM metadata WHERE key='binding'").fetchone()
                    if binding is None or json.loads(binding[0]) != self.binding:
                        raise ArtifactEvidenceError("CPU evidence binding changed")
                    for digest, payload in connection.execute(
                            "SELECT sha256, payload FROM evidence ORDER BY rowid"):
                        _check(self.callback)
                        self.connection.execute("INSERT OR IGNORE INTO evidence VALUES (?, ?)",
                                                (digest, payload))
                finally:
                    connection.close()
                destination = destinations[index][0]
                fd, staged_name = tempfile.mkstemp(prefix="." + destination.name + "-",
                                                   suffix=".tmp", dir=self.directory)
                os.close(fd)
                staged = Path(staged_name)
                self._staged.append((staged, destination))
                os.replace(child_directory / destination.name, staged)
                if progress_callback is not None:
                    progress_callback(count)

    def close(self) -> None:
        if self._closed:
            return
        _check(self.callback)
        self.connection.commit()
        self.connection.close()
        self._closed = True
        pool = self.directory / POOL_FILENAME
        os.replace(self._temporary, pool)
        for temporary, destination in self._staged:
            os.replace(temporary, destination)
        self.manifest = {
            **self.binding, "path": POOL_FILENAME, "sha256": _file_sha256(pool, self.callback),
            "binding_sha256": self.binding_sha256,
        }
        self._identity.clear()

    def __exit__(self, exc_type, exc, tb) -> None:
        try:
            if exc_type is None:
                self.close()
        finally:
            if not self._closed:
                self.connection.close()
                self._closed = True
            self._identity.clear()
            for path in [self._temporary, *(item[0] for item in self._staged)]:
                if path.parent.resolve() == self.directory and path.exists():
                    path.unlink()


class ArtifactEvidenceReader:
    """Read old inline or new referenced records using one bounded evidence cache."""

    def __init__(self, output_dir: str | Path, *,
                 cancellation_callback: Callable[[], None] | None = None,
                 expected_video_id: str | None = None, expected_source_id: str | None = None,
                 cache_bytes: int = DEFAULT_CACHE_BYTES):
        self.directory = Path(output_dir).resolve()
        self.callback = cancellation_callback
        self.expected_video_id = expected_video_id
        self.expected_source_id = expected_source_id
        self.cache_bytes = max(0, cache_bytes)
        self._cache: OrderedDict[str, tuple[Any, int]] = OrderedDict()
        self._cached_bytes = 0
        self.connection: sqlite3.Connection | None = None
        self.binding: dict | None = None
        self.binding_sha256: str | None = None

    def __enter__(self) -> "ArtifactEvidenceReader":
        return self

    def close(self) -> None:
        if self.connection is not None:
            self.connection.close()
            self.connection = None
        self._cache.clear()
        self._cached_bytes = 0

    def __exit__(self, *args) -> None:
        self.close()

    def _open_pool(self) -> None:
        if self.connection is not None:
            return
        path = self.directory / POOL_FILENAME
        if path.is_symlink() or not path.is_file() or path.resolve().parent != self.directory:
            raise ArtifactEvidenceError("scoring evidence pool is missing or outside its run directory")
        try:
            connection = sqlite3.connect(path.as_uri() + "?mode=ro&immutable=1", uri=True)
            connection.execute("PRAGMA query_only=ON")
            row = connection.execute("SELECT value FROM metadata WHERE key='binding'").fetchone()
            if row is None or len(row[0]) > 16384:
                raise ArtifactEvidenceError("invalid evidence pool binding")
            binding = _strict_loads(row[0])
            expected = _binding(binding.get("video_id"), binding.get("source_id"), binding.get("video_sha256"))
            if binding != expected:
                raise ArtifactEvidenceError("unsupported or malformed evidence pool binding")
            if self.expected_video_id is not None and binding["video_id"] != self.expected_video_id:
                raise ArtifactEvidenceError("evidence pool belongs to another video")
            if self.expected_source_id is not None and binding["source_id"] != self.expected_source_id:
                raise ArtifactEvidenceError("evidence pool belongs to another source task")
        except (sqlite3.Error, ValueError, AttributeError, TypeError) as exc:
            if 'connection' in locals():
                connection.close()
            if isinstance(exc, ArtifactEvidenceError):
                raise
            raise ArtifactEvidenceError("invalid scoring evidence pool") from exc
        self.binding = binding
        self.binding_sha256 = _digest(_json_bytes(binding))
        self.connection = connection

    def _resolve(self, value: Any, *, ancestors: frozenset[str] = frozenset(), depth: int = 0) -> Any:
        if depth > 128:
            raise ArtifactEvidenceError("evidence nesting exceeds budget")
        if isinstance(value, dict) and REFERENCE_KEY in value:
            reference = value[REFERENCE_KEY]
            if set(value) != {REFERENCE_KEY} or not isinstance(reference, dict) or set(reference) != {
                "codec", "pool", "binding_sha256", "sha256"
            } or reference.get("codec") != CODEC_VERSION or reference.get("pool") != POOL_FILENAME:
                raise ArtifactEvidenceError("invalid evidence reference")
            self._open_pool()
            if reference["binding_sha256"] != self.binding_sha256:
                raise ArtifactEvidenceError("evidence reference belongs to another run")
            digest = reference["sha256"]
            if not isinstance(digest, str) or len(digest) != 64 or any(c not in "0123456789abcdef" for c in digest):
                raise ArtifactEvidenceError("invalid evidence content hash")
            if digest in ancestors:
                raise ArtifactEvidenceError("cyclic evidence reference")
            cached = self._cache.get(digest)
            if cached is not None:
                self._cache.move_to_end(digest)
                return cached[0]
            _check(self.callback)
            try:
                row = self.connection.execute("SELECT length(payload) FROM evidence WHERE sha256=?", (digest,)).fetchone()
                if row is None:
                    raise ArtifactEvidenceError("referenced evidence is missing")
                if row[0] > DEFAULT_MAX_RECORD_BYTES:
                    raise ArtifactEvidenceError("referenced evidence exceeds record budget")
                payload = self.connection.execute("SELECT payload FROM evidence WHERE sha256=?", (digest,)).fetchone()[0]
                if not isinstance(payload, bytes) or _digest(payload) != digest:
                    raise ArtifactEvidenceError("evidence content hash mismatch")
                unpacked = _strict_loads(payload)
            except (sqlite3.Error, json.JSONDecodeError, UnicodeDecodeError) as exc:
                raise ArtifactEvidenceError("invalid referenced evidence") from exc
            unpacked = self._resolve(unpacked, ancestors=ancestors | {digest}, depth=depth + 1)
            resident_bytes = resident_evidence_bytes(unpacked, self.cache_bytes)
            if resident_bytes <= self.cache_bytes:
                while self._cache and self._cached_bytes + resident_bytes > self.cache_bytes:
                    _, (_, old_size) = self._cache.popitem(last=False)
                    self._cached_bytes -= old_size
                self._cache[digest] = (unpacked, resident_bytes)
                self._cached_bytes += resident_bytes
            return unpacked
        if isinstance(value, dict):
            if depth == 128 and value:
                raise ArtifactEvidenceError("evidence nesting exceeds budget")
            return {key: self._resolve(item, ancestors=ancestors, depth=depth + 1)
                    if isinstance(item, (dict, list)) else item for key, item in value.items()}
        if isinstance(value, list):
            if depth == 128 and value:
                raise ArtifactEvidenceError("evidence nesting exceeds budget")
            return [self._resolve(item, ancestors=ancestors, depth=depth + 1)
                    if isinstance(item, (dict, list)) else item for item in value]
        return value

    def resolve_record(self, record: dict[str, Any]) -> dict[str, Any]:
        _check(self.callback)
        if not isinstance(record, dict):
            raise ArtifactEvidenceError("artifact records must be objects")
        result = self._resolve(record)
        video_id = record.get("video_id")
        if self.expected_video_id is not None and video_id is not None and video_id != self.expected_video_id:
            raise ArtifactEvidenceError("artifact record belongs to another video")
        if self.binding is not None and video_id is not None and video_id != self.binding["video_id"]:
            raise ArtifactEvidenceError("artifact record and evidence pool video differ")
        return result

    def iter_records(self, path: str | Path, *, max_record_bytes: int = DEFAULT_MAX_RECORD_BYTES,
                     resolve_evidence: bool = True) -> Iterator[dict[str, Any]]:
        if type(max_record_bytes) is not int or not 0 < max_record_bytes <= DEFAULT_MAX_RECORD_BYTES:
            raise ArtifactEvidenceError("record byte budget must be positive and at most 64 MiB")
        path = Path(path).resolve()
        if path.parent != self.directory:
            raise ArtifactEvidenceError("artifact file is outside its run directory")
        with path.open("rb") as handle:
            line_number = 0
            while True:
                _check(self.callback)
                physical_line = handle.readline(max_record_bytes + 1)
                if not physical_line:
                    break
                if len(physical_line) > max_record_bytes:
                    raise ArtifactEvidenceError(f"{path.name}: record exceeds {max_record_bytes} byte memory budget")
                try:
                    lines = physical_line.decode("utf-8").splitlines()
                except UnicodeDecodeError as exc:
                    raise ArtifactEvidenceError(f"invalid UTF-8 artifact {path.name}") from exc
                for line in lines:
                    line_number += 1
                    if not line.strip():
                        continue
                    try:
                        record = json.loads(line)
                    except json.JSONDecodeError as exc:
                        raise ArtifactEvidenceError(f"invalid JSONL artifact at {path}:{line_number}") from exc
                    if not isinstance(record, dict):
                        raise ArtifactEvidenceError(f"JSONL artifact must be an object at {path}:{line_number}")
                    yield self.resolve_record(record) if resolve_evidence else record


def iter_artifact_records(path: str | Path, *, cancellation_callback: Callable[[], None] | None = None,
                          max_record_bytes: int = DEFAULT_MAX_RECORD_BYTES,
                          expected_video_id: str | None = None,
                          expected_source_id: str | None = None) -> Iterator[dict[str, Any]]:
    path = Path(path).resolve()
    with ArtifactEvidenceReader(path.parent, cancellation_callback=cancellation_callback,
                                expected_video_id=expected_video_id,
                                expected_source_id=expected_source_id) as reader:
        yield from reader.iter_records(path, max_record_bytes=max_record_bytes)


def verify_evidence_manifest(output_dir: str | Path, manifest: dict[str, Any], *,
                             expected_video_id: str | None = None,
                             expected_source_id: str | None = None,
                             cancellation_callback: Callable[[], None] | None = None) -> None:
    if not isinstance(manifest, dict) or set(manifest) != {
        "codec", "video_id", "source_id", "video_sha256", "path", "sha256", "binding_sha256"
    } or manifest.get("codec") != CODEC_VERSION or manifest.get("path") != POOL_FILENAME:
        raise ArtifactEvidenceError("invalid evidence storage manifest")
    with ArtifactEvidenceReader(output_dir, cancellation_callback=cancellation_callback,
                                expected_video_id=expected_video_id,
                                expected_source_id=expected_source_id) as reader:
        reader._open_pool()
        if any(manifest[key] != reader.binding[key] for key in reader.binding):
            raise ArtifactEvidenceError("evidence manifest binding mismatch")
        if manifest["binding_sha256"] != reader.binding_sha256:
            raise ArtifactEvidenceError("evidence manifest binding hash mismatch")
        actual = _file_sha256(reader.directory / POOL_FILENAME, cancellation_callback)
        if not isinstance(manifest["sha256"], str) or actual != manifest["sha256"].lower():
            raise ArtifactEvidenceError("evidence pool file hash mismatch")
