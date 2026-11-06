"""Read-only, bounded working set for repeated report passes over JSONL.

Consumers may retain lightweight Mapping handles; only the small LRU holds
decoded rows. Complete records and their proof fields are never projected away.
"""
from __future__ import annotations

from collections import OrderedDict
from collections.abc import Mapping, Sequence
import json
from pathlib import Path
from typing import Any

from rallymate_scoring.artifact_evidence import ArtifactEvidenceReader, resident_evidence_bytes

MAX_RECORD_BYTES = 64 * 1024 * 1024
MAX_RECORDS = 65_000
MAX_CACHED_ROWS = 8
MAX_CACHE_BYTES = 16 * 1024 * 1024


class IndicatorRecordError(ValueError):
    pass


def _object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise IndicatorRecordError(f"duplicate JSON key: {key}")
        result[key] = value
    return result


def _nonfinite(value):
    raise IndicatorRecordError(f"non-finite JSON number: {value}")


def _parse(raw):
    return json.loads(raw, object_pairs_hook=_object, parse_constant=_nonfinite)


class _Record(Mapping):
    def __init__(self, owner, index):
        self.owner, self.index = owner, index

    def __getitem__(self, key):
        return self.owner._row(self.index)[key]

    def __iter__(self):
        return iter(self.owner._row(self.index))

    def __len__(self):
        return len(self.owner._row(self.index))


class IndicatorRecords(Sequence):
    """A context-managed immutable sequence of lazy, complete record mappings."""

    def __init__(self, path: Path, *, cancellation_callback=None):
        self.path = Path(path)
        self.check = cancellation_callback or (lambda: None)
        self.offsets = []
        self.cache = OrderedDict()
        self.cache_bytes = 0
        self.document = None
        self.handle = None
        self.reader = None
        self.closed = False

    def __enter__(self):
        try:
            self.handle = self.path.open("rb")
            self.identity = self._identity()
            self.reader = ArtifactEvidenceReader(self.path.parent, cancellation_callback=self.check)
            self.reader.__enter__()
            first = True
            while True:
                self.check()
                offset = self.handle.tell()
                raw = self.handle.readline(MAX_RECORD_BYTES + 1)
                if not raw:
                    break
                if len(raw) > MAX_RECORD_BYTES:
                    raise IndicatorRecordError("单条指标证据超过读取上限，请重新生成紧凑证据产物")
                if not raw.strip():
                    continue
                try:
                    value = _parse(raw)
                except json.JSONDecodeError:
                    if not first:
                        raise
                    self._regular_document()
                    break
                if first and (isinstance(value, list) or isinstance(value, dict) and any(
                    isinstance(value.get(key), list) for key in
                    ("records", "indicator_features", "predictions", "detections", "actions", "events")
                )):
                    position = self.handle.tell()
                    following = self.handle.readline(MAX_RECORD_BYTES + 1)
                    while following and not following.strip():
                        following = self.handle.readline(MAX_RECORD_BYTES + 1)
                    self.handle.seek(position)
                    if not following:
                        self._regular_document()
                        break
                first = False
                if not isinstance(value, dict):
                    raise IndicatorRecordError("indicator feature record is not an object")
                # Validate even rows later ignored by a particular report view.
                self.reader.resolve_record(value)
                self.offsets.append((offset, len(raw)))
                if len(self.offsets) > MAX_RECORDS:
                    raise IndicatorRecordError("指标记录超过报告处理上限，请分段分析视频")
            if not len(self):
                raise IndicatorRecordError("indicator features are empty")
            if self._identity() != self.identity:
                raise IndicatorRecordError("indicator features changed during reading")
            return self
        except BaseException:
            self.close()
            raise

    def _identity(self):
        stat = self.path.stat()
        return stat.st_size, stat.st_mtime_ns, stat.st_ino

    def _regular_document(self):
        if self.path.stat().st_size > MAX_RECORD_BYTES:
            raise IndicatorRecordError("普通 JSON 导出超过读取上限，请使用逐条 JSONL 格式")
        self.handle.seek(0)
        value = _parse(self.handle.read(MAX_RECORD_BYTES + 1))
        if isinstance(value, dict):
            rows = next((value[k] for k in
                         ("records", "indicator_features", "predictions", "detections", "actions", "events")
                         if isinstance(value.get(k), list)), [value])
        else:
            rows = value if isinstance(value, list) else []
        if len(rows) > MAX_RECORDS or any(not isinstance(row, dict) for row in rows):
            raise IndicatorRecordError("invalid or oversized indicator feature document")
        # Keep compact references here too: a small regular JSON export can
        # otherwise expand into many independent large proofs at once.
        for row in rows:
            self.check()
            self.reader.resolve_record(row)
        self.document = rows

    def _row(self, index):
        if self.closed:
            raise IndicatorRecordError("indicator record reader is closed")
        self.check()
        cached = self.cache.get(index)
        if cached is not None:
            self.cache.move_to_end(index)
            return cached[0]
        if self.document is not None:
            row = self.reader.resolve_record(self.document[index])
        else:
            if self._identity() != self.identity:
                raise IndicatorRecordError("indicator features changed during report preparation")
            offset, size = self.offsets[index]
            self.handle.seek(offset)
            raw = self.handle.read(size)
            if len(raw) != size:
                raise IndicatorRecordError("indicator feature record was truncated")
            row = self.reader.resolve_record(_parse(raw))
        # A tiny encoded reference can retain a large decoded proof after the
        # resolver evicts it. Charge retained Python objects conservatively,
        # even if another row or the resolver also owns the same proof.
        resident_bytes = resident_evidence_bytes(row, MAX_CACHE_BYTES)
        if resident_bytes <= MAX_CACHE_BYTES:
            while self.cache and (len(self.cache) >= MAX_CACHED_ROWS or self.cache_bytes + resident_bytes > MAX_CACHE_BYTES):
                _, (_, old_size) = self.cache.popitem(last=False)
                self.cache_bytes -= old_size
            self.cache[index] = row, resident_bytes
            self.cache_bytes += resident_bytes
        return row

    def __len__(self):
        return len(self.document) if self.document is not None else len(self.offsets)

    def __getitem__(self, index):
        if isinstance(index, slice):
            return [self[i] for i in range(*index.indices(len(self)))]
        if index < 0:
            index += len(self)
        if not 0 <= index < len(self):
            raise IndexError(index)
        return _Record(self, index)

    def close(self):
        if self.reader is not None:
            self.reader.__exit__(None, None, None)
            self.reader = None
        if self.handle is not None:
            self.handle.close()
            self.handle = None
        self.cache.clear()
        self.cache_bytes = 0
        self.document = None
        self.closed = True

    def __exit__(self, *args):
        self.close()
