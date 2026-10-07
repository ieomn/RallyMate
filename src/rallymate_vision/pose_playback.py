"""Bounded, observed-only skeleton replay from existing frame artifacts.

Coordinates remain in the original video frame. Camera stabilization and body
normalization belong to measurement code, never to a video overlay. No boxes,
missing joints, interpolated poses or inferred player identities are emitted.
"""
from __future__ import annotations

import json
import math
from collections.abc import Iterator, Mapping
from pathlib import Path
from typing import Any

from rallymate_vision.pose.metadata import keypoint_schema


POSE_PLAYBACK_VERSION = "observed-pose-playback-v1.0.0"
MIN_KEYPOINT_CONFIDENCE = 0.35
MAX_WINDOW_MS = 10_000
MAX_SAMPLE_LIMIT = 600
MAX_WINDOW_FRAMES = 10_000
MAX_DISPLAY_AGE_MS = 100
MAX_ARTIFACT_BYTES = 1024 * 1024 * 1024
MAX_PRIMARY_BYTES = 256 * 1024 * 1024
MAX_ARTIFACT_LINES = 2_000_000
MAX_LINE_BYTES = 1024 * 1024
JOINT_NAMES = tuple(item["name"] for item in keypoint_schema("halpe26")["keypoints"])
SKELETON_EDGES = [
    ["nose", "left_eye"], ["nose", "right_eye"], ["left_eye", "left_ear"], ["right_eye", "right_ear"],
    ["head", "neck"], ["neck", "left_shoulder"], ["neck", "right_shoulder"],
    ["left_shoulder", "right_shoulder"], ["left_shoulder", "left_elbow"], ["left_elbow", "left_wrist"],
    ["right_shoulder", "right_elbow"], ["right_elbow", "right_wrist"],
    ["left_shoulder", "left_hip"], ["right_shoulder", "right_hip"], ["left_hip", "right_hip"],
    ["neck", "hip"], ["hip", "left_hip"], ["hip", "right_hip"],
    ["left_hip", "left_knee"], ["left_knee", "left_ankle"],
    ["right_hip", "right_knee"], ["right_knee", "right_ankle"],
    ["left_ankle", "left_big_toe"], ["left_ankle", "left_small_toe"], ["left_ankle", "left_heel"],
    ["right_ankle", "right_big_toe"], ["right_ankle", "right_small_toe"], ["right_ankle", "right_heel"],
]


class PosePlaybackError(ValueError):
    """An artifact cannot provide a safely aligned pose playback window."""


def _finite(value: Any) -> float | None:
    if not isinstance(value, (int, float)) or isinstance(value, bool):
        return None
    try:
        return float(value) if math.isfinite(value) else None
    except OverflowError:
        return None


def _integer(value: Any, minimum: int = 0) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and value >= minimum


def _strict_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result = {}
    for key, value in pairs:
        if key in result:
            raise PosePlaybackError("duplicate JSON key in pose artifact")
        result[key] = value
    return result


def _reject_number(value: str) -> None:
    raise PosePlaybackError("nonfinite JSON number in pose artifact")


def _rows(path: Path, *, allow_partial: bool, maximum_bytes: int) -> Iterator[Mapping[str, Any]]:
    """Stream a bounded source; only an incomplete running-job tail is ignored."""
    try:
        if path.stat().st_size > maximum_bytes:
            raise PosePlaybackError("pose artifact exceeds the bounded read limit")
        with path.open("rb") as handle:
            consumed = 0
            for count in range(MAX_ARTIFACT_LINES + 1):
                raw = handle.readline(MAX_LINE_BYTES + 1)
                if not raw:
                    return
                consumed += len(raw)
                if count == MAX_ARTIFACT_LINES or consumed > maximum_bytes or len(raw) > MAX_LINE_BYTES:
                    raise PosePlaybackError("pose artifact exceeds the bounded read limit")
                if not raw.strip():
                    continue
                if allow_partial and not raw.endswith(b"\n"):
                    return
                value = json.loads(raw, object_pairs_hook=_strict_object, parse_constant=_reject_number)
                if not isinstance(value, Mapping):
                    raise PosePlaybackError("pose artifact row must be an object")
                yield value
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise PosePlaybackError("pose artifact could not be read") from exc


def _primary_rows(path: Path, *, allow_partial: bool) -> Iterator[Mapping[str, Any]]:
    previous = -1
    for row in _rows(path, allow_partial=allow_partial, maximum_bytes=MAX_PRIMARY_BYTES):
        index = row.get("processed_index")
        if not _integer(index) or index <= previous:
            raise PosePlaybackError("primary timeline indexes must be unique and increasing")
        previous = index
        yield row


def _keypoints(pose: Mapping[str, Any], width: int, height: int) -> list[dict[str, Any]]:
    if pose.get("coordinate_space") is not None and pose.get("coordinate_space") != "original_frame":
        return []
    points = pose.get("keypoints")
    if not isinstance(points, list):
        return []
    selected: dict[str, Mapping[str, Any]] = {}
    conflicts = set()
    for point in points:
        if not isinstance(point, Mapping) or point.get("name") not in JOINT_NAMES:
            continue
        name = point["name"]
        if name in selected and selected[name] != point:
            conflicts.add(name)
        selected[name] = point
    result = []
    for name in JOINT_NAMES:
        point = selected.get(name)
        if point is None or name in conflicts or point.get("in_frame") is False or point.get("confidence_in_range") is False:
            continue
        confidence = _finite(point.get("confidence"))
        if confidence is None or not MIN_KEYPOINT_CONFIDENCE <= confidence <= 1:
            continue
        if "x_px" in point or "y_px" in point:
            x, y = _finite(point.get("x_px")), _finite(point.get("y_px"))
            if x is None or y is None:
                continue
            x, y = x / width, y / height
        else:
            x, y = _finite(point.get("x_normalized")), _finite(point.get("y_normalized"))
        if x is None or y is None or not (0 <= x <= 1 and 0 <= y <= 1):
            continue
        result.append({"name": name, "x": round(x, 6), "y": round(y, 6), "confidence": round(confidence, 5)})
    return result


def _select_pose(record: Mapping[str, Any], frame: Mapping[str, Any], primary: Mapping[str, Any] | None,
                 has_primary: bool) -> tuple[Mapping[str, Any] | None, str, str | None]:
    poses = record.get("poses")
    poses = [item for item in poses if isinstance(item, Mapping) and _integer(item.get("person_track_id"), 1)] if isinstance(poses, list) else []
    if has_primary:
        if (primary is None or not _integer(primary.get("source_frame_index"))
                or _finite(primary.get("timestamp_ms")) is None
                or any(primary.get(key) != frame.get(frame_key) for key, frame_key in (
                ("processed_index", "processed_index"), ("source_frame_index", "index"), ("timestamp_ms", "timestamp_ms")))):
            return None, "unavailable", "primary_frame_binding_unavailable"
        if primary.get("selection_status") != "selected" or primary.get("identity_ambiguous") is True:
            return None, "unavailable", "primary_identity_unavailable"
        track = primary.get("source_track_id")
        if not _integer(track, 1):
            return None, "unavailable", "primary_identity_unavailable"
        matches = [pose for pose in poses if pose["person_track_id"] == track]
        return (matches[0], "selected", None) if len(matches) == 1 else (None, "unavailable", "selected_pose_missing_or_conflicting")
    if len(poses) != 1:
        return None, "unavailable", "single_person_not_observed"
    return poses[0], "single_visible_person", None


def build_pose_playback(frames_path: Path, *, primary_timeline_path: Path | None = None,
                        start_ms: int = 0, duration_ms: int = MAX_WINDOW_MS,
                        sample_limit: int = MAX_SAMPLE_LIMIT, allow_partial: bool = False) -> dict[str, Any]:
    if (not _integer(start_ms) or not _integer(duration_ms, 1) or duration_ms > MAX_WINDOW_MS
            or not _integer(sample_limit, 1) or sample_limit > MAX_SAMPLE_LIMIT):
        raise PosePlaybackError("pose playback window or sample limit is invalid")
    end_ms = start_ms + duration_ms
    has_primary = primary_timeline_path is not None and primary_timeline_path.is_file()
    primary_rows = _primary_rows(primary_timeline_path, allow_partial=allow_partial) if has_primary else iter(())
    current_primary = next(primary_rows, None)
    frames, dimensions = [], set()
    preceding, next_after_start = None, None
    previous_time, previous_index, previous_processed = None, -1, -1
    previous_track, epoch = None, 0
    try:
        for record in _rows(frames_path, allow_partial=allow_partial, maximum_bytes=MAX_ARTIFACT_BYTES):
            frame = record.get("frame")
            if not isinstance(frame, Mapping):
                raise PosePlaybackError("pose source frame metadata is missing")
            timestamp = _finite(frame.get("timestamp_ms"))
            index, processed = frame.get("index"), frame.get("processed_index")
            if (timestamp is None or timestamp < 0 or not _integer(index) or not _integer(processed)
                    or index <= previous_index or processed <= previous_processed
                    or previous_time is not None and timestamp <= previous_time):
                raise PosePlaybackError("pose source frame indexes and timestamps must be increasing")
            if frames:
                frames[-1]["valid_until_ms"] = min(frames[-1]["valid_until_ms"], timestamp)
            if preceding is not None and not frames:
                preceding["valid_until_ms"] = min(preceding["valid_until_ms"], timestamp)
            while current_primary is not None and current_primary["processed_index"] < processed:
                current_primary = next(primary_rows, None)
            primary = current_primary if current_primary is not None and current_primary["processed_index"] == processed else None
            pose, selection, reason = _select_pose(record, frame, primary, has_primary)
            width, height = frame.get("width"), frame.get("height")
            valid_dimensions = _integer(width, 1) and _integer(height, 1) and max(width, height) <= 65_536
            points = _keypoints(pose, width, height) if pose is not None and valid_dimensions else []
            if frame.get("timestamp_source") == "fps_fallback":
                points, reason = [], "source_timestamp_unverified"
            elif not valid_dimensions:
                points, reason = [], "source_dimensions_unavailable"
            elif pose is not None and not points:
                reason = "confident_in_frame_keypoints_unavailable"
            track = pose["person_track_id"] if pose is not None else None
            if (track != previous_track or previous_time is not None and timestamp - previous_time > MAX_DISPLAY_AGE_MS
                    or processed > previous_processed + 1):
                epoch += 1
            observed = {
                "frame_index": index, "processed_index": processed, "timestamp_ms": timestamp,
                "valid_until_ms": min(end_ms, timestamp + MAX_DISPLAY_AGE_MS),
                "person_track_id": track, "selection_epoch": epoch, "selection_status": selection,
                "width": width if valid_dimensions else None, "height": height if valid_dimensions else None,
                "source_keypoint_format": pose.get("keypoint_format") if pose is not None and isinstance(pose.get("keypoint_format"), str) else None,
                "keypoints": points, "reason_code": reason,
            }
            if timestamp >= start_ms and next_after_start is None:
                next_after_start = observed
            if timestamp >= end_ms:
                break
            if timestamp >= start_ms:
                if len(frames) >= MAX_WINDOW_FRAMES:
                    raise PosePlaybackError("pose window exceeds the bounded frame limit")
                if valid_dimensions:
                    dimensions.add((width, height))
                frames.append(observed)
            else:
                preceding = observed
            previous_time, previous_index, previous_processed, previous_track = timestamp, index, processed, track
    finally:
        close = getattr(primary_rows, "close", None)
        if close is not None:
            close()
    observed_count = len(frames)
    # A source frame can straddle a requested window boundary. Retain that
    # still-current observation only within its original lifetime and the same
    # observed identity. An empty/ambiguous next frame never revives a pose.
    include_preceding = (preceding is not None and preceding["keypoints"]
                         and preceding["valid_until_ms"] > start_ms
                         and (next_after_start is None or (
                             next_after_start["keypoints"]
                             and next_after_start["person_track_id"] == preceding["person_track_id"]
                             and next_after_start["selection_epoch"] == preceding["selection_epoch"])))
    if include_preceding:
        frames.insert(0, preceding)
        dimensions.add((preceding["width"], preceding["height"]))
    display_count = len(frames)
    if display_count > sample_limit:
        indexes = {round(index * (display_count - 1) / (sample_limit - 1)) for index in range(sample_limit)} if sample_limit > 1 else {0}
        frames = [frame for index, frame in enumerate(frames) if index in indexes]
    width, height = next(iter(dimensions)) if len(dimensions) == 1 else (None, None)
    return {
        "schema_version": "1.0.0", "version": POSE_PLAYBACK_VERSION, "result_kind": "observed_pose_playback",
        "coordinate_space": "normalized_frame_0_1", "joint_schema": "halpe26_named",
        "keypoint_names": list(JOINT_NAMES), "skeleton_edges": [edge[:] for edge in SKELETON_EDGES],
        "min_keypoint_confidence": MIN_KEYPOINT_CONFIDENCE, "interpolation": "none",
        "frames": frames,
        "source": {"frames_path": "frames.jsonl", "primary_timeline_path": "primary-player.jsonl" if has_primary else None,
                   "requested_start_ms": start_ms, "requested_end_ms": end_ms, "width": width, "height": height,
                   "observed_frames_in_window": observed_count, "returned_frames": len(frames),
                   "includes_preceding_observation": bool(include_preceding),
                   "is_sampled": display_count > sample_limit, "is_partial": allow_partial,
                   "timestamp_semantics": "source_frame_timestamp_ms", "min_keypoint_confidence": MIN_KEYPOINT_CONFIDENCE,
                   "selection_source": "primary_player_timeline" if has_primary else "single_visible_person_only",
                   "max_display_age_ms": MAX_DISPLAY_AGE_MS},
    }
