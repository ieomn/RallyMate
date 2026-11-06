"""Incremental, read-only trajectory input for append-only running artifacts.

Only complete JSONL lines advance the cursor. The cache retains all compact
ball/racket observations, never the dense pose fields. Initial reads still scan
the existing file; rendering still traverses retained observations. Fixed count
limits bound retained objects, not an asserted number of Python heap bytes.
"""
from __future__ import annotations

from collections import OrderedDict
from collections.abc import Mapping
from dataclasses import dataclass, field
import os
from pathlib import Path
from threading import Lock

from rallymate_vision.trajectory import (
    MAX_FRAMES_ARTIFACT_BYTES, MAX_FRAMES_ARTIFACT_LINES, MAX_FRAMES_LINE_BYTES,
    TrajectoryExtractionError, TrajectoryObservationAccumulator,
    build_trajectory_preview, parse_trajectory_line,
)


@dataclass
class _Entry:
    generation: tuple
    identity: tuple
    observations: TrajectoryObservationAccumulator | None = field(default_factory=TrajectoryObservationAccumulator)
    offset: int = 0
    lines: int = 0
    stamp: tuple | None = None
    prefix: bytes = b""
    boundary: bytes = b""
    fallback_reason: str | None = None
    diagnostics: dict = field(default_factory=dict)
    payload_key: tuple | None = None
    payload: dict | None = None


class _SourceChanged(Exception):
    pass


class LiveTrajectoryCache:
    def __init__(self, *, max_jobs=4, max_observations=50_000, max_total_observations=100_000, max_tracks=4096):
        limits = (max_jobs, max_observations, max_total_observations, max_tracks)
        if any(type(value) is not int or value < 1 for value in limits):
            raise ValueError("live trajectory cache limits must be positive integers")
        self.max_jobs, self.max_observations, self.max_total_observations, self.max_tracks = limits
        self._entries = OrderedDict()
        self._lock = Lock()

    def discard(self, job_id):
        with self._lock:
            self._entries.pop(job_id, None)

    def clear(self):
        with self._lock:
            self._entries.clear()

    def counts(self):
        with self._lock:
            return {"jobs": len(self._entries), "observations": self._observation_count()}

    def _observation_count(self):
        return sum(entry.observations.observation_count for entry in self._entries.values() if entry.observations is not None)

    @staticmethod
    def _identity(metadata):
        # POSIX ctime changes on append and must never identify a new sequence.
        return metadata.st_dev, metadata.st_ino

    @staticmethod
    def _anchors(handle, offset):
        handle.seek(0)
        prefix = handle.read(min(64, offset))
        handle.seek(max(0, offset - 64))
        return prefix, handle.read(min(64, offset))

    def _reserve(self, job_id, entry, record):
        """Conservatively reserve before retaining new observations."""
        if not isinstance(record, Mapping):
            return None
        state = entry.observations
        schema = record.get("schema_version")
        if isinstance(schema, str) and (len(schema) > 256 or
                (schema not in state.schema_versions and len(state.schema_versions) >= 16)):
            return "schema_metadata_budget"
        detections = record.get("detections", [])
        if not isinstance(detections, list):
            return None
        candidates, new_tracks = 0, set()
        for item in detections:
            if not isinstance(item, Mapping) or item.get("class_name") not in state.tracks:
                continue
            candidates += 1
            track = item.get("track_id")
            track = track if type(track) is int and track > 0 else "untracked"
            kind = item["class_name"]
            if track not in state.tracks[kind]:
                new_tracks.add((kind, track))
        if state.observation_count + candidates > min(self.max_observations, self.max_total_observations):
            return "observation_budget"
        if sum(len(values) for values in state.tracks.values()) + len(new_tracks) > self.max_tracks:
            return "track_budget"
        while self._observation_count() + candidates > self.max_total_observations:
            victim = next((key for key in self._entries if key != job_id), None)
            if victim is None:
                return "total_observation_budget"
            self._entries.pop(victim)
        return None

    def _ingest(self, job_id, entry, handle, snapshot_size):
        initial = entry.stamp is None
        read_bytes, complete_lines = 0, 0
        handle.seek(entry.offset)
        while handle.tell() < snapshot_size:
            raw = handle.readline(min(snapshot_size - handle.tell(), MAX_FRAMES_LINE_BYTES + 1))
            if not raw:
                break
            read_bytes += len(raw)
            if len(raw) > MAX_FRAMES_LINE_BYTES:
                raise TrajectoryExtractionError("frames artifact exceeds the trajectory preview single-line byte limit")
            if entry.lines + 1 > MAX_FRAMES_ARTIFACT_LINES:
                raise TrajectoryExtractionError("frames artifact exceeds the trajectory preview line limit")
            if not raw.endswith(b"\n"):
                break  # Includes a split UTF-8 codepoint; do not parse or advance.
            if raw.strip():
                record = parse_trajectory_line(raw, entry.lines + 1)
                reason = self._reserve(job_id, entry, record)
                if reason:
                    entry.observations = None
                    entry.fallback_reason = reason
                    break
                entry.observations.observe(record, entry.lines + 1)
            entry.offset += len(raw)
            entry.lines += 1
            complete_lines += 1
        entry.prefix, entry.boundary = self._anchors(handle, entry.offset)
        entry.diagnostics = {"initial_full_scan": initial, "last_update_input_bytes": read_bytes,
                             "last_update_complete_lines": complete_lines, "snapshot_bytes": snapshot_size,
                             "cache_complete_prefix_bytes": entry.offset,
                             "partial_line_pending": entry.offset < snapshot_size if entry.observations is not None else None}
        entry.payload = None
        entry.payload_key = None

    def preview(self, job: dict, frames_path: Path, *, sample_limit: int, prediction_horizon_ms: int):
        path = Path(frames_path).resolve()
        job_id = job["id"]
        generation = (str(path), job.get("started_at"), job.get("attempts"))
        with self._lock:
            for attempt in range(2):
                try:
                    return self._preview(job_id, generation, path, sample_limit, prediction_horizon_ms)
                except _SourceChanged:
                    self._entries.pop(job_id, None)
                    if attempt:
                        raise TrajectoryExtractionError("frames artifact changed while building its live preview") from None
                except Exception:
                    self._entries.pop(job_id, None)
                    raise

    def _preview(self, job_id, generation, path, sample_limit, prediction_horizon_ms):
        with path.open("rb") as handle:
            metadata = os.fstat(handle.fileno())
            identity = self._identity(metadata)
            stamp = metadata.st_size, metadata.st_mtime_ns
            if metadata.st_size > MAX_FRAMES_ARTIFACT_BYTES:
                raise TrajectoryExtractionError("frames artifact exceeds the trajectory preview byte limit")
            entry = self._entries.get(job_id)
            invalid = (entry is None or entry.generation != generation or entry.identity != identity)
            if not invalid:
                invalid = (metadata.st_size < entry.stamp[0]
                           or (metadata.st_size == entry.stamp[0] and stamp != entry.stamp)
                           or self._anchors(handle, entry.offset) != (entry.prefix, entry.boundary))
            if invalid:
                entry = _Entry(generation, identity)
                self._entries[job_id] = entry
            self._entries.move_to_end(job_id)
            while len(self._entries) > self.max_jobs:
                self._entries.popitem(last=False)
            changed = entry.stamp != stamp
            if changed:
                if entry.observations is not None:
                    self._ingest(job_id, entry, handle, metadata.st_size)
                else:
                    entry.payload = None
                    entry.payload_key = None
                    entry.diagnostics.update(initial_full_scan=False, last_update_input_bytes=0,
                                             last_update_complete_lines=0, snapshot_bytes=metadata.st_size)
                entry.stamp = stamp
            after = path.stat()
            if (self._identity(after) != identity or after.st_size < metadata.st_size
                    or (after.st_size == metadata.st_size and after.st_mtime_ns != metadata.st_mtime_ns)):
                raise _SourceChanged()
        payload_key = sample_limit, prediction_horizon_ms
        if entry.payload is None or entry.payload_key != payload_key:
            observations = entry.observations.snapshot() if entry.observations is not None else None
            fallback_snapshot = path.stat().st_size if observations is None else None
            payload = build_trajectory_preview(path, sample_limit=sample_limit,
                prediction_horizon_ms=prediction_horizon_ms, allow_partial=True, _observations=observations)
            # Never publish paths or use a recent-only history to satisfy a budget.
            payload["source"]["frames_path"] = "frames.jsonl"
            payload["job_id"] = job_id
            payload["source"]["read_strategy"] = {**entry.diagnostics,
                "mode": "incremental_complete_history" if observations is not None else "full_scan_cache_budget_fallback",
                "fallback_reason": entry.fallback_reason, "history": "all_complete_frames",
                "retained_observations": entry.observations.observation_count if observations is not None else 0,
                "cache_limits": {"jobs": self.max_jobs, "observations_per_job": self.max_observations,
                                 "total_observations": self.max_total_observations, "tracks_per_job": self.max_tracks,
                                 "schema_versions_per_job": 16, "schema_version_characters": 256},
                "complexity": ("initial_source_scan_linear_in_existing_bytes; later_source_parse_only_appended_bytes; rendering_still_traverses_retained_observations"
                               if observations is not None else "cache_budget_fallback_rescans_the_complete_source_snapshot"),
                "fallback_full_scan": observations is None,
                "fallback_source_bytes_at_start": fallback_snapshot}
            entry.payload, entry.payload_key = payload, payload_key
        return entry.payload
