from __future__ import annotations

import gc
import hashlib
import itertools
import json
import math
import platform
import statistics
import time
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable


REPORT_VERSION = "rtmpose-same-frame-diagnostic-v1.0.0"
EXPECTED_PROTOCOL_VERSION = "rtmpose-same-frame-diagnostic-2026-09-04.1"
EXPECTED_FAMILIES = ("M", "L", "X")


class PoseSameFrameDiagnosticError(ValueError):
    """Raised when a diagnostic input, protocol, or output violates its contract."""


def sha256_file(path: str | Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest().upper()


def canonical_sha256(value: Any) -> str:
    encoded = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest().upper()


def _inside(root: Path, relative: Any, label: str) -> Path:
    if not isinstance(relative, str) or not relative.strip():
        raise PoseSameFrameDiagnosticError(f"{label} must be a non-empty relative path")
    value = Path(relative)
    if value.is_absolute():
        raise PoseSameFrameDiagnosticError(f"{label} must be relative to the workspace")
    resolved = (root / value).resolve()
    if not resolved.is_relative_to(root):
        raise PoseSameFrameDiagnosticError(f"{label} escapes the workspace")
    return resolved


def _bound_file(
    root: Path,
    relative: Any,
    expected_sha256: Any,
    label: str,
    *,
    expected_bytes: int | None = None,
) -> dict[str, Any]:
    path = _inside(root, relative, label)
    if not path.is_file():
        raise PoseSameFrameDiagnosticError(f"{label} does not exist: {path}")
    observed_sha256 = sha256_file(path)
    if observed_sha256 != str(expected_sha256 or "").upper():
        raise PoseSameFrameDiagnosticError(f"{label} SHA-256 drifted")
    size = path.stat().st_size
    if expected_bytes is not None and size != expected_bytes:
        raise PoseSameFrameDiagnosticError(f"{label} byte size drifted")
    return {
        "path": str(path),
        "relative_path": Path(relative).as_posix(),
        "sha256": observed_sha256,
        "bytes": size,
    }


def _require_false_fields(value: Any, fields: Iterable[str], label: str) -> None:
    if not isinstance(value, dict):
        raise PoseSameFrameDiagnosticError(f"{label} must be an object")
    drifted = [field for field in fields if value.get(field) is not False]
    if drifted:
        raise PoseSameFrameDiagnosticError(
            f"{label} must keep false fields: {', '.join(drifted)}"
        )


def load_diagnostic_registry(
    workspace: str | Path,
    registry_path: str | Path = "models/rtmpose/m96-same-frame-diagnostic.json",
) -> dict[str, Any]:
    root = Path(workspace).resolve()
    path = _inside(root, str(registry_path), "diagnostic registry")
    if not path.is_file():
        raise PoseSameFrameDiagnosticError(f"diagnostic registry does not exist: {path}")
    try:
        registry = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise PoseSameFrameDiagnosticError(f"diagnostic registry is invalid JSON: {exc}") from exc
    if not isinstance(registry, dict):
        raise PoseSameFrameDiagnosticError("diagnostic registry must be an object")
    if registry.get("schema_version") != "1.0.0":
        raise PoseSameFrameDiagnosticError("unsupported diagnostic registry schema_version")
    if registry.get("protocol_version") != EXPECTED_PROTOCOL_VERSION:
        raise PoseSameFrameDiagnosticError("unsupported diagnostic protocol_version")

    safety_fields = (
        "changes_deployment_registry",
        "changes_production_default",
        "opens_sealed_holdout",
        "uses_ground_truth",
        "claims_RallyMate_accuracy",
        "promotes_candidate",
        "promotes_F3_or_F4",
        "generates_grade_or_threshold",
        "uses_confidence_as_accuracy",
        "uses_model_agreement_as_accuracy",
    )
    _require_false_fields(registry.get("safety"), safety_fields, "registry safety")

    production = registry.get("production_default_binding")
    if not isinstance(production, dict):
        raise PoseSameFrameDiagnosticError("production default binding is missing")
    production_binding = _bound_file(
        root,
        production.get("registry_relative_path"),
        production.get("registry_sha256"),
        "production deployment registry",
    )
    try:
        deployed = json.loads(Path(production_binding["path"]).read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise PoseSameFrameDiagnosticError("production deployment registry is invalid JSON") from exc
    if deployed.get("default_preset") != production.get("preset_id"):
        raise PoseSameFrameDiagnosticError("bound production default preset drifted")

    source_bindings = []
    source_roles: set[str] = set()
    for item in registry.get("source_registry_bindings", []):
        if not isinstance(item, dict):
            raise PoseSameFrameDiagnosticError("source registry binding must be an object")
        role = str(item.get("role", "")).strip()
        if not role or role in source_roles:
            raise PoseSameFrameDiagnosticError("source registry roles must be unique")
        source_roles.add(role)
        source_bindings.append(
            {
                "role": role,
                "binding": _bound_file(
                    root,
                    item.get("relative_path"),
                    item.get("sha256"),
                    f"source registry {role}",
                ),
            }
        )
    if len(source_bindings) != 2:
        raise PoseSameFrameDiagnosticError("exactly two source registry bindings are required")

    protocol = registry.get("development_protocol")
    if not isinstance(protocol, dict) or protocol.get("phase") != "development_same_frame_diagnostic_only":
        raise PoseSameFrameDiagnosticError("development diagnostic protocol is missing")
    if protocol.get("ground_truth_used") is not False or protocol.get("accuracy_claim") is not False:
        raise PoseSameFrameDiagnosticError("diagnostic protocol must not use truth or claim accuracy")
    holdout = protocol.get("sealed_holdout")
    if not isinstance(holdout, dict) or holdout.get("use_during_development") is not False:
        raise PoseSameFrameDiagnosticError("sealed holdout guard is invalid")
    holdout_id = str(holdout.get("video_id", "")).strip()
    holdout_relative = Path(str(holdout.get("video_relative_path", ""))).as_posix()
    holdout_sha256 = str(holdout.get("video_sha256", "")).upper()
    if not holdout_id or not holdout_relative or len(holdout_sha256) != 64:
        raise PoseSameFrameDiagnosticError("sealed holdout identity is incomplete")
    # Resolve only the declared path for traversal checking. The holdout file is never opened.
    _inside(root, holdout_relative, "sealed holdout declared path")

    sampling = protocol.get("sampling")
    execution = protocol.get("execution")
    metrics = protocol.get("metrics")
    if not isinstance(sampling, dict) or not isinstance(execution, dict) or not isinstance(metrics, dict):
        raise PoseSameFrameDiagnosticError("sampling, execution, and metrics blocks are required")
    windows_per_video = int(sampling.get("windows_per_video", 0))
    frames_per_window = int(sampling.get("frames_per_window", 0))
    max_players = int(sampling.get("max_players", 0))
    if windows_per_video < 1 or frames_per_window < 2 or max_players != 1:
        raise PoseSameFrameDiagnosticError("sampling must use positive windows, >=2 frames, and one player")
    if execution.get("profile") != "realtime" or execution.get("flip_test") is not False:
        raise PoseSameFrameDiagnosticError("all candidates must use the common realtime no-flip profile")
    roi_margin = float(execution.get("roi_margin", -1))
    min_roi_size_px = int(execution.get("min_roi_size_px", 0))
    warmup_calls = int(execution.get("warmup_calls_per_model", -1))
    repeat_calls = int(execution.get("same_input_repeat_calls_per_video_and_model", 0))
    if not math.isfinite(roi_margin) or roi_margin < 0 or min_roi_size_px < 1:
        raise PoseSameFrameDiagnosticError("ROI execution parameters are invalid")
    if warmup_calls < 0 or repeat_calls < 2:
        raise PoseSameFrameDiagnosticError("warmup or repeat-call parameters are invalid")
    confidence_thresholds = [float(value) for value in metrics.get("confidence_thresholds", [])]
    distance_thresholds = [float(value) for value in metrics.get("normalized_distance_thresholds", [])]
    cross_model_min_confidence = float(metrics.get("cross_model_min_confidence", -1))
    if (
        not confidence_thresholds
        or confidence_thresholds != sorted(set(confidence_thresholds))
        or any(not 0 <= value <= 1 for value in confidence_thresholds)
        or not distance_thresholds
        or distance_thresholds != sorted(set(distance_thresholds))
        or any(not 0 < value <= 1 for value in distance_thresholds)
        or not 0 <= cross_model_min_confidence <= 1
    ):
        raise PoseSameFrameDiagnosticError("diagnostic thresholds are invalid")
    if metrics.get("distance_normalizer") != "pose_estimator_expanded_ROI_diagonal":
        raise PoseSameFrameDiagnosticError("distance normalizer drifted")
    if metrics.get("missing_pose_counts_against_coverage_denominator") is not True:
        raise PoseSameFrameDiagnosticError("missing poses must remain in coverage denominators")

    videos = protocol.get("videos")
    if not isinstance(videos, list) or len(videos) != 3:
        raise PoseSameFrameDiagnosticError("exactly three development videos are required")
    verified_videos = []
    seen_video_ids: set[str] = set()
    seen_video_paths: set[str] = set()
    seen_video_hashes: set[str] = set()
    for item in videos:
        if not isinstance(item, dict):
            raise PoseSameFrameDiagnosticError("development video entry must be an object")
        video_id = str(item.get("video_id", "")).strip()
        video_relative = Path(str(item.get("video_relative_path", ""))).as_posix()
        video_sha256 = str(item.get("video_sha256", "")).upper()
        if (
            not video_id
            or video_id in seen_video_ids
            or video_relative in seen_video_paths
            or video_sha256 in seen_video_hashes
        ):
            raise PoseSameFrameDiagnosticError("development video identities must be unique")
        if video_id == holdout_id or video_relative == holdout_relative or video_sha256 == holdout_sha256:
            raise PoseSameFrameDiagnosticError("development inputs alias the sealed holdout")
        seen_video_ids.add(video_id)
        seen_video_paths.add(video_relative)
        seen_video_hashes.add(video_sha256)
        verified_videos.append(
            {
                "video_id": video_id,
                "declared_frame_count": int(item.get("frame_count", -1)),
                "video": _bound_file(
                    root,
                    video_relative,
                    video_sha256,
                    f"development video {video_id}",
                ),
                "frozen_frames": _bound_file(
                    root,
                    item.get("frozen_frames_relative_path"),
                    item.get("frozen_frames_sha256"),
                    f"frozen frames {video_id}",
                ),
            }
        )
    if any(item["declared_frame_count"] < 1 for item in verified_videos):
        raise PoseSameFrameDiagnosticError("development frame counts must be positive")

    model_items = registry.get("models")
    if not isinstance(model_items, list) or len(model_items) != 3:
        raise PoseSameFrameDiagnosticError("exactly three model families are required")
    verified_models = []
    seen_families: set[str] = set()
    seen_ids: set[str] = set()
    seen_checkpoints: set[str] = set()
    for item in model_items:
        if not isinstance(item, dict):
            raise PoseSameFrameDiagnosticError("model entry must be an object")
        family = str(item.get("family", "")).strip()
        candidate_id = str(item.get("candidate_id", "")).strip()
        if family not in EXPECTED_FAMILIES or family in seen_families:
            raise PoseSameFrameDiagnosticError("model families must be exactly M, L, and X")
        if not candidate_id or candidate_id in seen_ids:
            raise PoseSameFrameDiagnosticError("candidate IDs must be unique and non-empty")
        if item.get("native_keypoint_format") != "halpe26":
            raise PoseSameFrameDiagnosticError("all diagnostic models must use Halpe26")
        input_size = item.get("input_size_hw")
        if not isinstance(input_size, list) or len(input_size) != 2 or any(int(v) < 1 for v in input_size):
            raise PoseSameFrameDiagnosticError("model input_size_hw is invalid")
        checkpoint = _bound_file(
            root,
            item.get("model_relative_path"),
            item.get("checkpoint_sha256"),
            f"{family} checkpoint",
            expected_bytes=int(item.get("checkpoint_bytes", -1)),
        )
        config = _bound_file(
            root,
            item.get("config_runtime_relative_path"),
            item.get("config_sha256"),
            f"{family} config",
        )
        if checkpoint["sha256"] in seen_checkpoints:
            raise PoseSameFrameDiagnosticError("model checkpoints must be distinct")
        seen_families.add(family)
        seen_ids.add(candidate_id)
        seen_checkpoints.add(checkpoint["sha256"])
        verified_models.append(
            {
                **item,
                "input_size_hw": [int(value) for value in input_size],
                "checkpoint": checkpoint,
                "config": config,
            }
        )
    if tuple(sorted(seen_families, key=EXPECTED_FAMILIES.index)) != EXPECTED_FAMILIES:
        raise PoseSameFrameDiagnosticError("model families must be exactly M, L, and X")
    verified_models.sort(key=lambda item: EXPECTED_FAMILIES.index(item["family"]))

    return {
        "root": str(root),
        "registry": {
            "path": str(path),
            "relative_path": Path(registry_path).as_posix(),
            "sha256": sha256_file(path),
            "bytes": path.stat().st_size,
            "protocol_version": registry["protocol_version"],
        },
        "production_default": {
            "preset_id": production["preset_id"],
            "registry": production_binding,
        },
        "source_registries": source_bindings,
        "protocol": protocol,
        "holdout_identity": {
            "video_id": holdout_id,
            "declared_relative_path": holdout_relative,
            "declared_sha256": holdout_sha256,
            "file_opened": False,
        },
        "videos": verified_videos,
        "models": verified_models,
    }


def _player_key(detection: dict[str, Any]) -> tuple[float, str]:
    bbox = detection.get("bbox_px")
    if not isinstance(bbox, list) or len(bbox) != 4:
        raise PoseSameFrameDiagnosticError("player bbox must contain four values")
    values = [float(value) for value in bbox]
    if not all(math.isfinite(value) for value in values) or values[2] <= values[0] or values[3] <= values[1]:
        raise PoseSameFrameDiagnosticError("player bbox must be finite and non-empty")
    area = (values[2] - values[0]) * (values[3] - values[1])
    return (-area, str(detection.get("track_id", "")))


def _evenly_spaced(items: list[Any], count: int) -> list[Any]:
    if len(items) < count:
        raise PoseSameFrameDiagnosticError(
            f"only {len(items)} eligible windows are available; {count} required"
        )
    if count == 1:
        return [items[len(items) // 2]]
    indexes = [round(index * (len(items) - 1) / (count - 1)) for index in range(count)]
    if len(set(indexes)) != count:
        raise PoseSameFrameDiagnosticError("even window selection produced duplicates")
    return [items[index] for index in indexes]


def select_frozen_same_frame_samples(
    frames_path: str | Path,
    *,
    video_id: str,
    declared_frame_count: int,
    windows_per_video: int,
    frames_per_window: int,
) -> dict[str, Any]:
    rows: list[dict[str, Any]] = []
    seen_processed: set[int] = set()
    seen_source: set[int] = set()
    line_count = 0
    previous_processed: int | None = None
    previous_source: int | None = None
    with Path(frames_path).open("r", encoding="utf-8") as handle:
        for line_number, line in enumerate(handle, start=1):
            if not line.strip():
                continue
            line_count += 1
            try:
                record = json.loads(line)
                frame = record["frame"]
                processed = int(frame["processed_index"])
                source = int(frame.get("source_frame_index", frame["index"]))
                timestamp_ms = int(frame["timestamp_ms"])
                width = int(frame["width"])
                height = int(frame["height"])
            except (json.JSONDecodeError, KeyError, TypeError, ValueError) as exc:
                raise PoseSameFrameDiagnosticError(
                    f"{video_id} frozen frame line {line_number} is invalid"
                ) from exc
            if processed in seen_processed or source in seen_source:
                raise PoseSameFrameDiagnosticError(f"{video_id} repeats a frame index")
            if (
                previous_processed is not None
                and (processed <= previous_processed or source <= int(previous_source))
            ):
                raise PoseSameFrameDiagnosticError(f"{video_id} frame order is not strictly increasing")
            if width < 1 or height < 1:
                raise PoseSameFrameDiagnosticError(f"{video_id} has invalid frame dimensions")
            seen_processed.add(processed)
            seen_source.add(source)
            previous_processed, previous_source = processed, source
            players = [item for item in record.get("detections", []) if item.get("class_name") == "player"]
            if not players:
                continue
            selected = sorted(players, key=_player_key)[0]
            bbox = [float(value) for value in selected["bbox_px"]]
            track_id = selected.get("track_id")
            if track_id is None or isinstance(track_id, (dict, list)):
                raise PoseSameFrameDiagnosticError(f"{video_id} player track_id is invalid")
            confidence = float(selected.get("confidence", 0.0))
            if not math.isfinite(confidence):
                raise PoseSameFrameDiagnosticError(f"{video_id} player confidence is invalid")
            rows.append(
                {
                    "video_id": video_id,
                    "processed_index": processed,
                    "source_frame_index": source,
                    "timestamp_ms": timestamp_ms,
                    "frame_width": width,
                    "frame_height": height,
                    "player": {
                        "class_name": "player",
                        "track_id": track_id,
                        "confidence": round(confidence, 6),
                        "bbox_px": [round(value, 6) for value in bbox],
                    },
                }
            )
    if line_count != declared_frame_count:
        raise PoseSameFrameDiagnosticError(
            f"{video_id} frozen frame count drifted: {line_count} != {declared_frame_count}"
        )

    segments: list[list[dict[str, Any]]] = []
    current: list[dict[str, Any]] = []
    for row in rows:
        if current:
            previous = current[-1]
            contiguous = (
                row["processed_index"] == previous["processed_index"] + 1
                and row["source_frame_index"] == previous["source_frame_index"] + 1
                and row["player"]["track_id"] == previous["player"]["track_id"]
                and row["frame_width"] == previous["frame_width"]
                and row["frame_height"] == previous["frame_height"]
            )
            if not contiguous:
                segments.append(current)
                current = []
        current.append(row)
    if current:
        segments.append(current)

    candidates: list[list[dict[str, Any]]] = []
    for segment in segments:
        for offset in range(0, len(segment) - frames_per_window + 1, frames_per_window):
            candidates.append(segment[offset : offset + frames_per_window])
    selected_windows = _evenly_spaced(candidates, windows_per_video)
    samples = []
    windows = []
    for window_number, window in enumerate(selected_windows, start=1):
        window_id = f"{video_id}-w{window_number:02d}"
        sample_ids = []
        for position, row in enumerate(window):
            sample_id = (
                f"{video_id}:{row['source_frame_index']}:"
                f"{row['player']['track_id']}"
            )
            item = {
                **row,
                "window_id": window_id,
                "window_position": position,
                "sample_id": sample_id,
            }
            samples.append(item)
            sample_ids.append(sample_id)
        windows.append(
            {
                "window_id": window_id,
                "track_id": window[0]["player"]["track_id"],
                "source_frame_start": window[0]["source_frame_index"],
                "source_frame_end": window[-1]["source_frame_index"],
                "sample_ids": sample_ids,
            }
        )
    return {
        "video_id": video_id,
        "eligible_frame_count": len(rows),
        "stable_segment_count": len(segments),
        "candidate_non_overlapping_window_count": len(candidates),
        "selected_windows": windows,
        "samples": samples,
    }


def _percentile(values: list[float], percentile: float) -> float | None:
    if not values:
        return None
    ordered = sorted(float(value) for value in values)
    position = (len(ordered) - 1) * percentile / 100.0
    lower = math.floor(position)
    upper = math.ceil(position)
    if lower == upper:
        return round(ordered[lower], 6)
    weight = position - lower
    return round(ordered[lower] * (1.0 - weight) + ordered[upper] * weight, 6)


def _distribution(values: Iterable[float]) -> dict[str, Any]:
    finite = [float(value) for value in values if math.isfinite(float(value))]
    return {
        "count": len(finite),
        "min": round(min(finite), 6) if finite else None,
        "mean": round(statistics.mean(finite), 6) if finite else None,
        "p10": _percentile(finite, 10),
        "p50": _percentile(finite, 50),
        "p90": _percentile(finite, 90),
        "p95": _percentile(finite, 95),
        "max": round(max(finite), 6) if finite else None,
    }


def _rate(numerator: int, denominator: int) -> float | None:
    return round(numerator / denominator, 6) if denominator else None


def _threshold_key(value: float) -> str:
    return format(float(value), ".6g")


def _pose_points(pose: dict[str, Any] | None) -> list[dict[str, Any]]:
    if pose is None:
        return []
    points = pose.get("keypoints")
    if not isinstance(points, list) or len(points) != 26:
        raise PoseSameFrameDiagnosticError("returned Halpe26 pose must contain 26 keypoints")
    for expected, point in enumerate(points):
        if int(point.get("index", -1)) != expected:
            raise PoseSameFrameDiagnosticError("returned Halpe26 indexes are not contiguous")
        for field in ("x_px", "y_px", "confidence"):
            if not math.isfinite(float(point.get(field, math.nan))):
                raise PoseSameFrameDiagnosticError("returned pose contains a non-finite value")
    return points


def _roi_diagonal(pose: dict[str, Any]) -> float:
    bbox = pose.get("roi_bbox_px")
    if not isinstance(bbox, list) or len(bbox) != 4:
        raise PoseSameFrameDiagnosticError("pose ROI must contain four values")
    diagonal = math.hypot(float(bbox[2]) - float(bbox[0]), float(bbox[3]) - float(bbox[1]))
    if not math.isfinite(diagonal) or diagonal <= 0:
        raise PoseSameFrameDiagnosticError("pose ROI diagonal must be positive")
    return diagonal


def _coverage_metrics(
    observations: list[dict[str, Any]], confidence_thresholds: list[float]
) -> dict[str, Any]:
    call_count = len(observations)
    returned = [item for item in observations if item.get("pose") is not None]
    confidences = [
        float(point["confidence"])
        for item in returned
        for point in _pose_points(item["pose"])
    ]
    in_frame_count = sum(
        point.get("in_frame") is not False
        for item in returned
        for point in _pose_points(item["pose"])
    )
    expected_points = call_count * 26
    threshold_coverage = {}
    for threshold in confidence_thresholds:
        count = sum(value >= threshold for value in confidences)
        threshold_coverage[_threshold_key(threshold)] = {
            "keypoint_count": count,
            "expected_keypoint_denominator": expected_points,
            "rate_including_missing_poses": _rate(count, expected_points),
        }
    per_joint = []
    names: dict[int, str] = {}
    by_joint: dict[int, list[float]] = defaultdict(list)
    for item in returned:
        for point in _pose_points(item["pose"]):
            index = int(point["index"])
            names[index] = str(point.get("name", index))
            by_joint[index].append(float(point["confidence"]))
    for index in range(26):
        values = by_joint[index]
        per_joint.append(
            {
                "index": index,
                "name": names.get(index),
                "returned_confidence_count": len(values),
                "expected_frame_denominator": call_count,
                "confidence": _distribution(values),
                "threshold_coverage_including_missing_poses": {
                    _threshold_key(threshold): _rate(
                        sum(value >= threshold for value in values), call_count
                    )
                    for threshold in confidence_thresholds
                },
            }
        )
    return {
        "call_count": call_count,
        "pose_return_count": len(returned),
        "pose_return_rate": _rate(len(returned), call_count),
        "expected_keypoint_count": expected_points,
        "returned_keypoint_count": len(confidences),
        "returned_keypoint_rate": _rate(len(confidences), expected_points),
        "coordinate_in_frame_count": in_frame_count,
        "coordinate_in_frame_rate_among_returned": _rate(in_frame_count, len(confidences)),
        "effective_in_frame_rate_including_missing_poses": _rate(in_frame_count, expected_points),
        "confidence_distribution_among_returned": _distribution(confidences),
        "confidence_threshold_coverage": threshold_coverage,
        "per_joint": per_joint,
    }


def _temporal_continuity(
    observations: list[dict[str, Any]], *, min_confidence: float
) -> dict[str, Any]:
    by_window: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    for item in observations:
        by_window[(str(item["video_id"]), str(item["window_id"]))].append(item)
    pose_presence_agreements = 0
    both_pose = 0
    pair_count = 0
    coordinate_displacements: list[float] = []
    confidence_deltas: list[float] = []
    eligible_joint_pairs = 0
    for items in by_window.values():
        ordered = sorted(items, key=lambda item: int(item["window_position"]))
        for left, right in zip(ordered, ordered[1:]):
            if int(right["source_frame_index"]) != int(left["source_frame_index"]) + 1:
                raise PoseSameFrameDiagnosticError("temporal window is not source-frame consecutive")
            pair_count += 1
            left_pose, right_pose = left.get("pose"), right.get("pose")
            pose_presence_agreements += (left_pose is None) == (right_pose is None)
            if left_pose is None or right_pose is None:
                continue
            both_pose += 1
            normalizer = (_roi_diagonal(left_pose) + _roi_diagonal(right_pose)) / 2.0
            left_points = _pose_points(left_pose)
            right_points = _pose_points(right_pose)
            for a, b in zip(left_points, right_points):
                confidence_deltas.append(abs(float(a["confidence"]) - float(b["confidence"])))
                if float(a["confidence"]) >= min_confidence and float(b["confidence"]) >= min_confidence:
                    eligible_joint_pairs += 1
                    coordinate_displacements.append(
                        math.hypot(
                            float(a["x_px"]) - float(b["x_px"]),
                            float(a["y_px"]) - float(b["y_px"]),
                        )
                        / normalizer
                    )
    return {
        "semantics": (
            "observed_consecutive_frame_output_continuity; coordinate displacement "
            "contains real subject motion, camera motion, ROI motion, and model variation"
        ),
        "min_confidence_for_coordinate_displacement": min_confidence,
        "consecutive_frame_pair_count": pair_count,
        "pose_presence_agreement_rate": _rate(pose_presence_agreements, pair_count),
        "both_pose_pair_rate": _rate(both_pose, pair_count),
        "confidence_eligible_joint_pair_count": eligible_joint_pairs,
        "normalized_coordinate_displacement": _distribution(coordinate_displacements),
        "confidence_absolute_delta": _distribution(confidence_deltas),
    }


def summarize_model_observations(
    observations: list[dict[str, Any]],
    *,
    confidence_thresholds: list[float],
    temporal_min_confidence: float,
) -> dict[str, Any]:
    if not observations:
        raise PoseSameFrameDiagnosticError("model observations must not be empty")
    overall = _coverage_metrics(observations, confidence_thresholds)
    by_video = []
    for video_id in sorted({str(item["video_id"]) for item in observations}):
        subset = [item for item in observations if item["video_id"] == video_id]
        by_video.append({"video_id": video_id, **_coverage_metrics(subset, confidence_thresholds)})
    return {
        "coverage_and_confidence": overall,
        "latency_ms_per_single_roi_call": _distribution(
            float(item["latency_ms"]) for item in observations
        ),
        "temporal_continuity": _temporal_continuity(
            observations, min_confidence=temporal_min_confidence
        ),
        "by_video": by_video,
    }


def summarize_repeatability(repeat_runs: list[dict[str, Any]]) -> dict[str, Any]:
    anchor_summaries = []
    coordinate_deltas: list[float] = []
    confidence_deltas: list[float] = []
    consistent_anchors = 0
    for run in repeat_runs:
        poses = run.get("poses")
        if not isinstance(poses, list) or len(poses) < 2:
            raise PoseSameFrameDiagnosticError("repeat run must contain at least two calls")
        pattern = [pose is not None for pose in poses]
        consistent = len(set(pattern)) == 1
        consistent_anchors += consistent
        reference = poses[0]
        anchor_coordinate: list[float] = []
        anchor_confidence: list[float] = []
        if reference is not None:
            reference_points = _pose_points(reference)
            normalizer = _roi_diagonal(reference)
            for pose in poses[1:]:
                if pose is None:
                    continue
                points = _pose_points(pose)
                for a, b in zip(reference_points, points):
                    anchor_coordinate.append(
                        math.hypot(
                            float(a["x_px"]) - float(b["x_px"]),
                            float(a["y_px"]) - float(b["y_px"]),
                        )
                        / normalizer
                    )
                    anchor_confidence.append(
                        abs(float(a["confidence"]) - float(b["confidence"]))
                    )
        coordinate_deltas.extend(anchor_coordinate)
        confidence_deltas.extend(anchor_confidence)
        anchor_summaries.append(
            {
                "sample_id": run["sample_id"],
                "video_id": run["video_id"],
                "repeat_call_count": len(poses),
                "pose_return_pattern": pattern,
                "pose_presence_consistent": consistent,
                "result_sha256s": [canonical_sha256(pose) for pose in poses],
                "normalized_coordinate_delta_from_first_call": _distribution(anchor_coordinate),
                "confidence_absolute_delta_from_first_call": _distribution(anchor_confidence),
            }
        )
    return {
        "semantics": "same_decoded_frame_same_frozen_ROI_repeatability_not_accuracy",
        "anchor_count": len(repeat_runs),
        "pose_presence_consistent_anchor_rate": _rate(consistent_anchors, len(repeat_runs)),
        "normalized_coordinate_delta_from_first_call": _distribution(coordinate_deltas),
        "confidence_absolute_delta_from_first_call": _distribution(confidence_deltas),
        "anchors": anchor_summaries,
    }


def compare_model_observations(
    model_observations: dict[str, list[dict[str, Any]]],
    *,
    min_confidence: float,
    distance_thresholds: list[float],
) -> dict[str, Any]:
    if tuple(model_observations) != EXPECTED_FAMILIES:
        raise PoseSameFrameDiagnosticError("comparison models must be ordered M, L, X")
    indexed = {
        family: {item["sample_id"]: item for item in observations}
        for family, observations in model_observations.items()
    }
    sample_ids = list(indexed["M"])
    if any(set(value) != set(sample_ids) for value in indexed.values()):
        raise PoseSameFrameDiagnosticError("models were not run on identical sample IDs")

    pairwise = []
    pair_distance_by_family: dict[str, list[float]] = defaultdict(list)
    for left_family, right_family in itertools.combinations(EXPECTED_FAMILIES, 2):
        presence_agreement = 0
        both_pose = 0
        eligible = 0
        distances: list[float] = []
        confidence_deltas: list[float] = []
        for sample_id in sample_ids:
            left_pose = indexed[left_family][sample_id].get("pose")
            right_pose = indexed[right_family][sample_id].get("pose")
            presence_agreement += (left_pose is None) == (right_pose is None)
            if left_pose is None or right_pose is None:
                continue
            both_pose += 1
            if left_pose.get("roi_bbox_px") != right_pose.get("roi_bbox_px"):
                raise PoseSameFrameDiagnosticError("same-frame model ROI bindings differ")
            normalizer = _roi_diagonal(left_pose)
            for a, b in zip(_pose_points(left_pose), _pose_points(right_pose)):
                confidence_deltas.append(abs(float(a["confidence"]) - float(b["confidence"])))
                if float(a["confidence"]) >= min_confidence and float(b["confidence"]) >= min_confidence:
                    eligible += 1
                    distance = math.hypot(
                        float(a["x_px"]) - float(b["x_px"]),
                        float(a["y_px"]) - float(b["y_px"]),
                    ) / normalizer
                    distances.append(distance)
                    pair_distance_by_family[left_family].append(distance)
                    pair_distance_by_family[right_family].append(distance)
        pairwise.append(
            {
                "pair": f"{left_family}_vs_{right_family}",
                "sample_count": len(sample_ids),
                "pose_presence_agreement_rate": _rate(presence_agreement, len(sample_ids)),
                "both_pose_rate": _rate(both_pose, len(sample_ids)),
                "confidence_eligible_joint_pair_count": eligible,
                "normalized_coordinate_distance": _distribution(distances),
                "distance_threshold_coverage": {
                    _threshold_key(threshold): {
                        "joint_pair_count": sum(value <= threshold for value in distances),
                        "eligible_joint_pair_denominator": eligible,
                        "rate": _rate(sum(value <= threshold for value in distances), eligible),
                    }
                    for threshold in distance_thresholds
                },
                "confidence_absolute_delta": _distribution(confidence_deltas),
            }
        )

    unanimous_presence = 0
    all_pose = 0
    all_eligible = 0
    spreads: list[float] = []
    for sample_id in sample_ids:
        poses = [indexed[family][sample_id].get("pose") for family in EXPECTED_FAMILIES]
        pattern = [pose is not None for pose in poses]
        unanimous_presence += len(set(pattern)) == 1
        if not all(pattern):
            continue
        all_pose += 1
        asserted_poses = [pose for pose in poses if pose is not None]
        if len({tuple(pose["roi_bbox_px"]) for pose in asserted_poses}) != 1:
            raise PoseSameFrameDiagnosticError("three-model same-frame ROI bindings differ")
        normalizer = _roi_diagonal(asserted_poses[0])
        point_sets = [_pose_points(pose) for pose in asserted_poses]
        for joint_index in range(26):
            points = [values[joint_index] for values in point_sets]
            if not all(float(point["confidence"]) >= min_confidence for point in points):
                continue
            all_eligible += 1
            spreads.append(
                max(
                    math.hypot(
                        float(a["x_px"]) - float(b["x_px"]),
                        float(a["y_px"]) - float(b["y_px"]),
                    )
                    / normalizer
                    for a, b in itertools.combinations(points, 2)
                )
            )
    return {
        "semantics": (
            "agreement_between_models_on_identical_decoded_frames_and_frozen_ROIs; "
            "agreement can reflect shared error and is not ground-truth accuracy"
        ),
        "min_confidence": min_confidence,
        "distance_normalizer": "pose_estimator_expanded_ROI_diagonal",
        "pairwise": pairwise,
        "three_model_consensus": {
            "sample_count": len(sample_ids),
            "pose_presence_unanimous_rate": _rate(unanimous_presence, len(sample_ids)),
            "all_three_pose_rate": _rate(all_pose, len(sample_ids)),
            "all_three_confidence_eligible_joint_count": all_eligible,
            "maximum_pairwise_normalized_coordinate_spread": _distribution(spreads),
            "distance_threshold_coverage": {
                _threshold_key(threshold): {
                    "joint_count": sum(value <= threshold for value in spreads),
                    "eligible_joint_denominator": all_eligible,
                    "rate": _rate(sum(value <= threshold for value in spreads), all_eligible),
                }
                for threshold in distance_thresholds
            },
        },
        "consensus_centrality_descriptive_only": {
            family: _distribution(pair_distance_by_family[family])
            for family in EXPECTED_FAMILIES
        },
    }


def _comparative_orderings(models: dict[str, dict[str, Any]], threshold: float) -> dict[str, Any]:
    threshold_key = _threshold_key(threshold)

    def entries(path: tuple[str, ...], reverse: bool) -> list[dict[str, Any]]:
        values = []
        for family in EXPECTED_FAMILIES:
            value: Any = models[family]
            for key in path:
                value = value[key]
            values.append({"family": family, "value": value})
        return sorted(
            values,
            key=lambda item: (
                item["value"] is None,
                -float(item["value"]) if reverse and item["value"] is not None else float(item["value"] or 0),
                EXPECTED_FAMILIES.index(item["family"]),
            ),
        )

    return {
        "semantics": "descriptive_orderings_only_no_composite_score_no_accuracy_winner",
        "pose_return_rate_high_to_low": entries(
            ("summary", "coverage_and_confidence", "pose_return_rate"), True
        ),
        f"confidence_{threshold_key}_coverage_high_to_low": entries(
            (
                "summary",
                "coverage_and_confidence",
                "confidence_threshold_coverage",
                threshold_key,
                "rate_including_missing_poses",
            ),
            True,
        ),
        "latency_p50_ms_low_to_high": entries(
            ("summary", "latency_ms_per_single_roi_call", "p50"), False
        ),
        "observed_temporal_displacement_p50_low_to_high": entries(
            (
                "summary",
                "temporal_continuity",
                "normalized_coordinate_displacement",
                "p50",
            ),
            False,
        ),
        "same_input_repeat_delta_p50_low_to_high": entries(
            ("repeatability", "normalized_coordinate_delta_from_first_call", "p50"),
            False,
        ),
    }


def validate_diagnostic_report(
    report: dict[str, Any], *, expected_sample_ids: list[str] | None = None
) -> None:
    if report.get("schema_version") != "1.0.0" or report.get("report_version") != REPORT_VERSION:
        raise PoseSameFrameDiagnosticError("unsupported report identity")
    if report.get("status") != "diagnostic_complete_ground_truth_required":
        raise PoseSameFrameDiagnosticError("diagnostic report status is not complete")
    _require_false_fields(
        report.get("claims"),
        (
            "ground_truth_used",
            "RallyMate_accuracy_measured",
            "RallyMate_accuracy_improved",
            "confidence_is_accuracy",
            "model_agreement_is_accuracy",
            "candidate_promoted",
            "production_default_changed",
            "F3_or_F4_promoted",
            "grade_or_threshold_generated",
        ),
        "report claims",
    )
    protocol = report.get("protocol")
    _require_false_fields(
        protocol,
        (
            "sealed_holdout_opened",
            "ground_truth_used",
            "accuracy_claim",
            "changes_production_default",
        ),
        "report protocol",
    )
    production = report.get("production_default")
    if not isinstance(production, dict) or production.get("unchanged") is not True:
        raise PoseSameFrameDiagnosticError("production default must remain unchanged")
    samples = report.get("sample_manifest")
    if not isinstance(samples, list) or not samples:
        raise PoseSameFrameDiagnosticError("sample manifest is missing")
    sample_ids = [str(item.get("sample_id", "")) for item in samples]
    if len(sample_ids) != len(set(sample_ids)) or any(not value for value in sample_ids):
        raise PoseSameFrameDiagnosticError("sample manifest IDs must be unique and non-empty")
    if expected_sample_ids is not None and sample_ids != expected_sample_ids:
        raise PoseSameFrameDiagnosticError("sample manifest differs from frozen selection")
    if report.get("sample_manifest_sha256") != canonical_sha256(samples):
        raise PoseSameFrameDiagnosticError("sample manifest SHA-256 drifted")
    models = report.get("models")
    if not isinstance(models, dict) or tuple(models) != EXPECTED_FAMILIES:
        raise PoseSameFrameDiagnosticError("report model families must be ordered M, L, X")
    for family, value in models.items():
        observations = value.get("observations") if isinstance(value, dict) else None
        if not isinstance(observations, list):
            raise PoseSameFrameDiagnosticError(f"{family} observations are missing")
        if [item.get("sample_id") for item in observations] != sample_ids:
            raise PoseSameFrameDiagnosticError(f"{family} did not preserve sample ordering")
        for item in observations:
            latency = float(item.get("latency_ms", math.nan))
            if not math.isfinite(latency) or latency < 0:
                raise PoseSameFrameDiagnosticError(f"{family} latency is invalid")
            pose = item.get("pose")
            if pose is not None:
                if pose.get("keypoint_format") != "halpe26":
                    raise PoseSameFrameDiagnosticError(f"{family} topology drifted")
                _pose_points(pose)
    if not isinstance(report.get("cross_model"), dict):
        raise PoseSameFrameDiagnosticError("cross-model diagnostics are missing")


def _decode_samples(
    verified_videos: list[dict[str, Any]], sample_groups: list[dict[str, Any]]
) -> dict[str, Any]:
    import cv2

    cache: dict[str, Any] = {}
    by_video = {item["video_id"]: item for item in sample_groups}
    for video in verified_videos:
        video_id = video["video_id"]
        group = by_video[video_id]
        sample_by_id = {item["sample_id"]: item for item in group["samples"]}
        capture = cv2.VideoCapture(video["video"]["path"])
        if not capture.isOpened():
            raise PoseSameFrameDiagnosticError(f"could not decode development video {video_id}")
        try:
            for window in group["selected_windows"]:
                samples = [sample_by_id[sample_id] for sample_id in window["sample_ids"]]
                capture.set(cv2.CAP_PROP_POS_FRAMES, samples[0]["source_frame_index"])
                for sample in samples:
                    ok, frame = capture.read()
                    if not ok:
                        raise PoseSameFrameDiagnosticError(
                            f"could not decode {video_id} frame {sample['source_frame_index']}"
                        )
                    height, width = frame.shape[:2]
                    if width != sample["frame_width"] or height != sample["frame_height"]:
                        raise PoseSameFrameDiagnosticError(
                            f"decoded dimensions drifted for {sample['sample_id']}"
                        )
                    cache[sample["sample_id"]] = frame
        finally:
            capture.release()
    if len(cache) != sum(len(item["samples"]) for item in sample_groups):
        raise PoseSameFrameDiagnosticError("decoded sample cache is incomplete")
    return cache


def _run_model(
    *,
    model: dict[str, Any],
    samples: list[dict[str, Any]],
    decoded: dict[str, Any],
    device: str,
    execution: dict[str, Any],
    torch: Any,
) -> dict[str, Any]:
    from rallymate_vision.pose import PoseEstimator, RtmposePoseBackend

    backend = RtmposePoseBackend(
        Path(model["checkpoint"]["path"]),
        Path(model["config"]["path"]),
        device=device,
        runtime="pytorch",
        profile=str(execution["profile"]),
        native_keypoint_format="halpe26",
    )
    estimator = PoseEstimator(
        backend,
        roi_margin=float(execution["roi_margin"]),
        min_roi_size_px=int(execution["min_roi_size_px"]),
    )

    def sync() -> None:
        if device != "cpu":
            torch.cuda.synchronize()

    first = samples[0]
    for _ in range(int(execution["warmup_calls_per_model"])):
        estimator.estimate(
            decoded[first["sample_id"]],
            [first["player"]],
            1,
            timestamp_ms=first["timestamp_ms"],
        )
        sync()
    if device != "cpu":
        torch.cuda.reset_peak_memory_stats()

    observations = []
    for sample in samples:
        sync()
        started = time.perf_counter()
        poses = estimator.estimate(
            decoded[sample["sample_id"]],
            [sample["player"]],
            1,
            timestamp_ms=sample["timestamp_ms"],
        )
        sync()
        elapsed_ms = (time.perf_counter() - started) * 1000.0
        if len(poses) > 1:
            raise PoseSameFrameDiagnosticError("one frozen ROI returned more than one pose")
        pose = poses[0] if poses else None
        if pose is not None:
            if pose.get("person_track_id") != sample["player"]["track_id"]:
                raise PoseSameFrameDiagnosticError("pose track binding drifted")
            if pose.get("keypoint_format") != "halpe26":
                raise PoseSameFrameDiagnosticError("pose topology drifted")
            _pose_points(pose)
        observations.append(
            {
                "sample_id": sample["sample_id"],
                "video_id": sample["video_id"],
                "window_id": sample["window_id"],
                "window_position": sample["window_position"],
                "processed_index": sample["processed_index"],
                "source_frame_index": sample["source_frame_index"],
                "timestamp_ms": sample["timestamp_ms"],
                "track_id": sample["player"]["track_id"],
                "latency_ms": round(elapsed_ms, 6),
                "pose": pose,
            }
        )

    repeat_count = int(execution["same_input_repeat_calls_per_video_and_model"])
    anchors = []
    seen_videos: set[str] = set()
    for sample in samples:
        if sample["video_id"] in seen_videos:
            continue
        seen_videos.add(sample["video_id"])
        poses = []
        for _ in range(repeat_count):
            values = estimator.estimate(
                decoded[sample["sample_id"]],
                [sample["player"]],
                1,
                timestamp_ms=sample["timestamp_ms"],
            )
            sync()
            if len(values) > 1:
                raise PoseSameFrameDiagnosticError("repeat call returned more than one pose")
            pose = values[0] if values else None
            if pose is not None:
                _pose_points(pose)
            poses.append(pose)
        anchors.append(
            {
                "sample_id": sample["sample_id"],
                "video_id": sample["video_id"],
                "poses": poses,
            }
        )

    metadata = backend.metadata().to_dict()
    if metadata.get("input_size") != model["input_size_hw"]:
        raise PoseSameFrameDiagnosticError(f"{model['family']} loaded input size drifted")
    if metadata.get("model_sha256") != model["checkpoint"]["sha256"]:
        raise PoseSameFrameDiagnosticError(f"{model['family']} loaded checkpoint drifted")
    if metadata.get("config_sha256") != model["config"]["sha256"]:
        raise PoseSameFrameDiagnosticError(f"{model['family']} loaded config drifted")
    if metadata.get("profile") != execution["profile"]:
        raise PoseSameFrameDiagnosticError(f"{model['family']} execution profile drifted")

    result = {
        "identity": {
            "family": model["family"],
            "candidate_id": model["candidate_id"],
            "source_role": model["source_role"],
            "execution_profile": execution["profile"],
            "flip_test": execution["flip_test"],
        },
        "checkpoint": model["checkpoint"],
        "config": model["config"],
        "metadata": metadata,
        "observations": observations,
        "repeatability": summarize_repeatability(anchors),
        "cuda_steady_state_peak": {
            "allocated_bytes": int(torch.cuda.max_memory_allocated()) if device != "cpu" else None,
            "reserved_bytes": int(torch.cuda.max_memory_reserved()) if device != "cpu" else None,
            "semantics": "model_resident_plus_warm_steady_state_inference_workspace",
        },
    }
    del estimator, backend
    gc.collect()
    if device != "cpu":
        torch.cuda.empty_cache()
    return result


def render_diagnostic_markdown(report: dict[str, Any]) -> str:
    lines = [
        "# RTMPose M/L/X 同帧开发诊断",
        "",
        f"状态：`{report['status']}`",
        "",
        "本报告没有人工关键点真值，因此只描述覆盖率、模型输出置信度、连续帧输出变化、同输入重复性、模型间一致性与本机耗时。它不能证明哪个模型更准确，也不授权替换默认模型。",
        "",
        f"样本：3 个开发视频，{len(report['sample_manifest'])} 个同帧单人 ROI；封存 holdout 未打开。",
        "",
        "| 模型 | 姿态返回率 | 置信度≥0.5 覆盖率 | 单 ROI P50 (ms) | 连续帧位移 P50 | 重复输入差异 P50 |",
        "|---|---:|---:|---:|---:|---:|",
    ]
    for family in EXPECTED_FAMILIES:
        value = report["models"][family]
        coverage = value["summary"]["coverage_and_confidence"]
        temporal = value["summary"]["temporal_continuity"]
        repeat = value["repeatability"]
        lines.append(
            "| {family} | {pose} | {conf} | {latency} | {motion} | {repeat_delta} |".format(
                family=family,
                pose=coverage["pose_return_rate"],
                conf=coverage["confidence_threshold_coverage"]["0.5"]["rate_including_missing_poses"],
                latency=value["summary"]["latency_ms_per_single_roi_call"]["p50"],
                motion=temporal["normalized_coordinate_displacement"]["p50"],
                repeat_delta=repeat["normalized_coordinate_delta_from_first_call"]["p50"],
            )
        )
    lines.extend(
        [
            "",
            "## 解释边界",
            "",
            "- 置信度是模型自己的输出刻度，不是人工真值准确率。",
            "- 模型彼此接近也可能是共同犯错，不是正确性的证明。",
            "- 连续帧位移同时包含真实动作、相机/ROI 移动和模型变化，不能单独叫作抖动。",
            "- 耗时只适用于本次固定设备、固定 ROI、统一 realtime/no-flip 策略；M 的线上服务还包含其他链路成本。",
            "- M 仍是生产默认；L/X 仍只是离线候选。",
            "",
            f"样本清单 SHA-256：`{report['sample_manifest_sha256']}`",
            "",
        ]
    )
    return "\n".join(lines)


def run_same_frame_diagnostic(
    *,
    workspace: str | Path,
    output_path: str | Path,
    summary_path: str | Path,
    registry_path: str | Path = "models/rtmpose/m96-same-frame-diagnostic.json",
    device: str = "0",
) -> dict[str, Any]:
    root = Path(workspace).resolve()
    output = Path(output_path)
    output = output.resolve() if output.is_absolute() else (root / output).resolve()
    summary_output = Path(summary_path)
    summary_output = (
        summary_output.resolve()
        if summary_output.is_absolute()
        else (root / summary_output).resolve()
    )
    for path, label in ((output, "report"), (summary_output, "summary")):
        if not path.is_relative_to(root):
            raise PoseSameFrameDiagnosticError(f"{label} output must stay in the workspace")
        if path.exists():
            raise PoseSameFrameDiagnosticError(f"immutable {label} output already exists: {path}")

    verified = load_diagnostic_registry(root, registry_path)
    protocol = verified["protocol"]
    sampling = protocol["sampling"]
    sample_groups = [
        select_frozen_same_frame_samples(
            video["frozen_frames"]["path"],
            video_id=video["video_id"],
            declared_frame_count=video["declared_frame_count"],
            windows_per_video=int(sampling["windows_per_video"]),
            frames_per_window=int(sampling["frames_per_window"]),
        )
        for video in verified["videos"]
    ]
    samples = [sample for group in sample_groups for sample in group["samples"]]
    decoded = _decode_samples(verified["videos"], sample_groups)

    import mmpose
    import numpy as np
    import torch

    if device != "cpu" and not torch.cuda.is_available():
        raise PoseSameFrameDiagnosticError("CUDA is unavailable for the requested diagnostic")
    if device != "cpu":
        torch.cuda.empty_cache()

    model_reports: dict[str, dict[str, Any]] = {}
    for model in verified["models"]:
        result = _run_model(
            model=model,
            samples=samples,
            decoded=decoded,
            device=device,
            execution=protocol["execution"],
            torch=torch,
        )
        result["summary"] = summarize_model_observations(
            result["observations"],
            confidence_thresholds=[float(value) for value in protocol["metrics"]["confidence_thresholds"]],
            temporal_min_confidence=float(protocol["metrics"]["cross_model_min_confidence"]),
        )
        model_reports[model["family"]] = result

    observations = {
        family: model_reports[family]["observations"] for family in EXPECTED_FAMILIES
    }
    report = {
        "schema_version": "1.0.0",
        "report_version": REPORT_VERSION,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "status": "diagnostic_complete_ground_truth_required",
        "semantics": (
            "same_frame_development_diagnostics_for_coverage_confidence_repeatability_"
            "temporal_continuity_cross_model_agreement_and_local_speed_not_accuracy"
        ),
        "registry": verified["registry"],
        "source_registries": verified["source_registries"],
        "production_default": {
            **verified["production_default"],
            "unchanged": True,
        },
        "protocol": {
            "phase": protocol["phase"],
            "sampling": protocol["sampling"],
            "execution": protocol["execution"],
            "metrics": protocol["metrics"],
            "same_decoded_frames_and_frozen_ROIs_across_models": True,
            "speed_comparison_scope": "within_this_device_run_and_common_realtime_no_flip_profile_only",
            "sealed_holdout_registry_guard": verified["holdout_identity"],
            "sealed_holdout_opened": False,
            "ground_truth_used": False,
            "accuracy_claim": False,
            "changes_production_default": False,
        },
        "environment": {
            "python": platform.python_version(),
            "torch": torch.__version__,
            "mmpose": mmpose.__version__,
            "numpy": np.__version__,
            "device": "cpu" if device == "cpu" else torch.cuda.get_device_name(int(device)),
        },
        "inputs": verified["videos"],
        "selection_diagnostics": [
            {key: value for key, value in group.items() if key != "samples"}
            for group in sample_groups
        ],
        "sample_manifest": samples,
        "sample_manifest_sha256": canonical_sha256(samples),
        "models": model_reports,
        "cross_model": compare_model_observations(
            observations,
            min_confidence=float(protocol["metrics"]["cross_model_min_confidence"]),
            distance_thresholds=[
                float(value) for value in protocol["metrics"]["normalized_distance_thresholds"]
            ],
        ),
        "comparative_orderings": _comparative_orderings(
            model_reports,
            threshold=0.5,
        ),
        "claims": {
            "ground_truth_used": False,
            "RallyMate_accuracy_measured": False,
            "RallyMate_accuracy_improved": False,
            "confidence_is_accuracy": False,
            "model_agreement_is_accuracy": False,
            "candidate_promoted": False,
            "production_default_changed": False,
            "F3_or_F4_promoted": False,
            "grade_or_threshold_generated": False,
        },
    }
    validate_diagnostic_report(report, expected_sample_ids=[item["sample_id"] for item in samples])
    markdown = render_diagnostic_markdown(report)
    output.parent.mkdir(parents=True, exist_ok=True)
    summary_output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(
        json.dumps(report, ensure_ascii=False, indent=2, sort_keys=False, allow_nan=False) + "\n",
        encoding="utf-8",
    )
    summary_output.write_text(markdown, encoding="utf-8")
    decoded.clear()
    gc.collect()
    if device != "cpu":
        torch.cuda.empty_cache()
    return report
