"""Read-only motion observability derived from a Stage 1 frame artifact.

The first-stage pipeline already persists per-frame ``ball`` and ``racket``
detections in ``frames.jsonl``.  This module deliberately lives outside the
detector and scoring code: it turns those observations into a small payload a
client can render without changing the inference contract.

The ball preview contains a *short, constant-velocity extrapolation*.  It is a
visualisation aid only.  It is not a tennis-ball model, a physics simulation,
or an accuracy claim.  Racket output is limited to the generic detector's
bounding-box track and confidence statistics; no racket keypoints are
invented when the current model does not provide them, and it is explicitly
marked unassociated with the primary-player timeline.
"""

from __future__ import annotations

import json
import math
import statistics
from collections import defaultdict
from collections.abc import Iterable, Mapping
from pathlib import Path
from typing import Any


TRAJECTORY_SCHEMA_VERSION = "1.0.0"
TRAJECTORY_RESULT_KIND = "trajectory_and_racket_observability_preview"
DEFAULT_SAMPLE_LIMIT = 240
DEFAULT_PREDICTION_HORIZON_MS = 400
MAX_PREDICTION_POINTS = 8
MAX_VELOCITY_SEGMENT_GAP_MS = 500
# A trajectory preview is a read-only convenience endpoint, so it must not
# become an unbounded JSONL ingestion surface.  These caps are deliberately
# independent of the much larger video upload limit: a corrupt or malicious
# frames artifact should fail closed before its points are materialized.
MAX_FRAMES_ARTIFACT_BYTES = 256 * 1024 * 1024
MAX_FRAMES_ARTIFACT_LINES = 2_000_000
MAX_OBSERVATION_POINTS = 500_000


class TrajectoryExtractionError(ValueError):
    """Raised when a frame artifact cannot be safely converted to a preview."""


def _finite(value: Any) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    result = float(value)
    return result if math.isfinite(result) else None


def _positive_int(value: Any) -> int | None:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        return None
    return value


def _clamp01(value: float) -> float:
    return max(0.0, min(1.0, value))


def _round(value: float | None, digits: int = 6) -> float | None:
    return round(value, digits) if value is not None else None


def _pair(value: Any) -> tuple[float, float] | None:
    if not isinstance(value, (list, tuple)) or len(value) != 2:
        return None
    first, second = _finite(value[0]), _finite(value[1])
    if first is None or second is None:
        return None
    return first, second


def _box(value: Any) -> tuple[float, float, float, float] | None:
    if not isinstance(value, (list, tuple)) or len(value) != 4:
        return None
    values = [_finite(item) for item in value]
    if any(item is None for item in values):
        return None
    x0, y0, x1, y1 = (float(item) for item in values)
    if x1 < x0 or y1 < y0:
        return None
    return x0, y0, x1, y1


def _sample_evenly(items: list[dict[str, Any]], limit: int) -> list[dict[str, Any]]:
    """Return deterministic first/last-preserving samples from a sorted list."""

    if len(items) <= limit:
        return items
    if limit <= 1:
        return [items[0]]
    indexes = {round(index * (len(items) - 1) / (limit - 1)) for index in range(limit)}
    return [item for index, item in enumerate(items) if index in indexes]


def _mean_confidence(points: Iterable[Mapping[str, Any]]) -> float:
    values = [
        float(value)
        for point in points
        if (value := _finite(point.get("confidence"))) is not None
    ]
    return statistics.fmean(values) if values else 0.0


def _confidence_summary(points: list[dict[str, Any]]) -> dict[str, float | None]:
    values = [
        float(value)
        for point in points
        if (value := _finite(point.get("confidence"))) is not None
    ]
    if not values:
        return {"mean": None, "median": None, "min": None, "max": None}
    return {
        "mean": _round(statistics.fmean(values), 4),
        "median": _round(statistics.median(values), 4),
        "min": _round(min(values), 4),
        "max": _round(max(values), 4),
    }


def _longest_missing_run(points: list[dict[str, Any]], frame_count: int) -> int:
    if not points or frame_count <= 0:
        return frame_count if frame_count > 0 else 0
    indexes = {int(point["processed_index"]) for point in points}
    first = min(indexes)
    last = max(indexes)
    longest = current = 0
    for index in range(first, last + 1):
        if index in indexes:
            current = 0
        else:
            current += 1
            longest = max(longest, current)
    return longest


def _track_id(value: Any) -> int | None:
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        return None
    return value


def _normalised_center(
    detection: Mapping[str, Any], width: int | None, height: int | None
) -> tuple[float, float] | None:
    center = _pair(detection.get("center_normalized"))
    if center is not None:
        return _clamp01(center[0]), _clamp01(center[1])

    box = _box(detection.get("bbox_normalized"))
    if box is not None:
        return (
            _clamp01((box[0] + box[2]) / 2.0),
            _clamp01((box[1] + box[3]) / 2.0),
        )

    if width and height:
        center_px = _pair(detection.get("center_px"))
        if center_px is not None:
            return (
                _clamp01(center_px[0] / width),
                _clamp01(center_px[1] / height),
            )
        box_px = _box(detection.get("bbox_px"))
        if box_px is not None:
            return (
                _clamp01((box_px[0] + box_px[2]) / 2.0 / width),
                _clamp01((box_px[1] + box_px[3]) / 2.0 / height),
            )
    return None


def _normalised_box(
    detection: Mapping[str, Any], width: int | None, height: int | None
) -> tuple[float, float, float, float] | None:
    box = _box(detection.get("bbox_normalized"))
    if box is not None:
        return tuple(_clamp01(item) for item in box)  # type: ignore[return-value]
    if width and height:
        box_px = _box(detection.get("bbox_px"))
        if box_px is not None:
            return (
                _clamp01(box_px[0] / width),
                _clamp01(box_px[1] / height),
                _clamp01(box_px[2] / width),
                _clamp01(box_px[3] / height),
            )
    return None


def _point_from_detection(
    *,
    frame: Mapping[str, Any],
    detection: Mapping[str, Any],
    frame_width: int | None,
    frame_height: int | None,
    include_box: bool,
) -> dict[str, Any] | None:
    frame_index = _positive_int(frame.get("index"))
    processed_index = _positive_int(frame.get("processed_index"))
    timestamp_ms = _positive_int(frame.get("timestamp_ms"))
    center = _normalised_center(detection, frame_width, frame_height)
    if (
        frame_index is None
        or processed_index is None
        or timestamp_ms is None
        or center is None
    ):
        return None
    confidence = _finite(detection.get("confidence"))
    confidence = _clamp01(confidence) if confidence is not None else 0.0
    result: dict[str, Any] = {
        "frame_index": frame_index,
        "processed_index": processed_index,
        "timestamp_ms": timestamp_ms,
        "x": _round(center[0]),
        "y": _round(center[1]),
        "confidence": _round(confidence, 4),
        "track_id": _track_id(detection.get("track_id")),
    }
    if include_box:
        box = _normalised_box(detection, frame_width, frame_height)
        if box is not None:
            result["bbox"] = [_round(item) for item in box]
    return result


def _read_observations(
    frames_path: Path,
) -> tuple[
    int,
    dict[str, set[str]],
    dict[str, dict[Any, list[dict[str, Any]]]],
    dict[str, Any],
]:
    """Read only ball/racket observations and basic frame metadata."""

    if not frames_path.exists() or not frames_path.is_file():
        raise TrajectoryExtractionError(f"frames artifact is missing: {frames_path}")
    try:
        artifact_bytes = frames_path.stat().st_size
    except OSError as exc:
        raise TrajectoryExtractionError(
            f"cannot stat frames artifact: {frames_path}"
        ) from exc
    if artifact_bytes > MAX_FRAMES_ARTIFACT_BYTES:
        raise TrajectoryExtractionError(
            "frames artifact exceeds the trajectory preview byte limit "
            f"({MAX_FRAMES_ARTIFACT_BYTES} bytes): {frames_path}"
        )

    frame_count = 0
    schema_versions: set[str] = set()
    tracks: dict[str, dict[Any, list[dict[str, Any]]]] = {
        "ball": defaultdict(list),
        "racket": defaultdict(list),
    }
    dimensions: dict[str, Any] = {"width": None, "height": None}
    try:
        handle = frames_path.open("r", encoding="utf-8")
    except OSError as exc:
        raise TrajectoryExtractionError(f"cannot open frames artifact: {exc}") from exc

    observation_count = 0
    with handle:
        for line_number, raw_line in enumerate(handle, start=1):
            if line_number > MAX_FRAMES_ARTIFACT_LINES:
                raise TrajectoryExtractionError(
                    "frames artifact exceeds the trajectory preview line limit "
                    f"({MAX_FRAMES_ARTIFACT_LINES}): {frames_path}"
                )
            if not raw_line.strip():
                continue
            try:
                record = json.loads(raw_line)
            except (json.JSONDecodeError, UnicodeError) as exc:
                raise TrajectoryExtractionError(
                    f"invalid frames JSON at line {line_number}: {exc}"
                ) from exc
            if not isinstance(record, Mapping):
                raise TrajectoryExtractionError(
                    f"frames line {line_number} is not an object"
                )
            frame = record.get("frame")
            if not isinstance(frame, Mapping):
                raise TrajectoryExtractionError(
                    f"frames line {line_number} has no frame object"
                )
            frame_count += 1
            schema = record.get("schema_version")
            if isinstance(schema, str) and schema:
                schema_versions.add(schema)
            width = _positive_int(frame.get("width"))
            height = _positive_int(frame.get("height"))
            if dimensions["width"] is None and width is not None:
                dimensions["width"] = width
            if dimensions["height"] is None and height is not None:
                dimensions["height"] = height
            detections = record.get("detections", [])
            if not isinstance(detections, list):
                continue
            for raw_detection in detections:
                if not isinstance(raw_detection, Mapping):
                    continue
                class_name = raw_detection.get("class_name")
                if class_name not in tracks:
                    continue
                point = _point_from_detection(
                    frame=frame,
                    detection=raw_detection,
                    frame_width=width,
                    frame_height=height,
                    include_box=class_name == "racket",
                )
                if point is None:
                    continue
                observation_count += 1
                if observation_count > MAX_OBSERVATION_POINTS:
                    raise TrajectoryExtractionError(
                        "frames artifact exceeds the trajectory preview observation "
                        f"limit ({MAX_OBSERVATION_POINTS}): {frames_path}"
                    )
                track_key: Any = point["track_id"]
                if track_key is None:
                    # Keep detections without IDs visible, but do not pretend
                    # that disconnected observations form a verified track.
                    track_key = "untracked"
                tracks[class_name][track_key].append(point)
    return frame_count, {"schema_version": schema_versions}, tracks, dimensions


def _select_track(
    tracks: Mapping[Any, list[dict[str, Any]]],
) -> tuple[Any | None, list[dict[str, Any]]]:
    candidates = [
        (
            key,
            sorted(
                points, key=lambda item: (item["processed_index"], item["timestamp_ms"])
            ),
        )
        for key, points in tracks.items()
        if points
    ]
    if not candidates:
        return None, []
    key, points = max(
        candidates,
        key=lambda item: (
            len(item[1]),
            _mean_confidence(item[1]),
            isinstance(item[0], int),
            str(item[0]),
        ),
    )
    # A malformed/duplicated artifact can contain two rows for one processed
    # frame.  Keep the highest-confidence observation deterministically.
    by_index: dict[int, dict[str, Any]] = {}
    for point in points:
        index = int(point["processed_index"])
        previous = by_index.get(index)
        if previous is None or point["confidence"] > previous["confidence"]:
            by_index[index] = point
    return key, [by_index[index] for index in sorted(by_index)]


def _track_payload(
    *,
    selected_track_id: Any,
    points: list[dict[str, Any]],
    track_count: int,
    frame_count: int,
    sample_limit: int,
    include_box: bool,
) -> dict[str, Any]:
    track_id = selected_track_id if isinstance(selected_track_id, int) else None
    observed = _sample_evenly(points, sample_limit)
    coverage = len(points) / max(frame_count, 1)
    payload: dict[str, Any] = {
        "track_id": track_id,
        "track_id_status": (
            "stable_id" if track_id is not None else "untracked_observations"
        ),
        "track_count": track_count,
        "observed_count": len(points),
        "coverage_fraction": _round(coverage, 4),
        "first_timestamp_ms": points[0]["timestamp_ms"] if points else None,
        "last_timestamp_ms": points[-1]["timestamp_ms"] if points else None,
        "longest_missing_run_frames": _longest_missing_run(points, frame_count),
        "missing_run_semantics": "within_selected_track_span",
        "confidence": _confidence_summary(points),
        "observed": observed,
    }
    if include_box:
        payload["geometry_status"] = "bbox_only"
    return payload


def _velocity(points: list[dict[str, Any]]) -> dict[str, float | int | None] | None:
    """Estimate robust recent image-plane velocity from observed centers."""

    segments: list[tuple[float, float, float, int]] = []
    for previous, current in zip(points, points[1:]):
        dt_ms = int(current["timestamp_ms"]) - int(previous["timestamp_ms"])
        if dt_ms <= 0 or dt_ms > MAX_VELOCITY_SEGMENT_GAP_MS:
            continue
        dt = dt_ms / 1000.0
        vx = (float(current["x"]) - float(previous["x"])) / dt
        vy = (float(current["y"]) - float(previous["y"])) / dt
        segments.append((vx, vy, dt, dt_ms))
    if not segments:
        return None
    recent = segments[-5:]
    vx = statistics.median(item[0] for item in recent)
    vy = statistics.median(item[1] for item in recent)
    speed = math.hypot(vx, vy)
    return {
        "vx_normalized_per_s": _round(vx),
        "vy_normalized_per_s": _round(vy),
        "speed_normalized_per_s": _round(speed),
        "direction_image_deg": _round(math.degrees(math.atan2(vy, vx))),
        "segment_count": len(recent),
        "source": "median_of_last_observed_segments",
    }


def _constant_velocity_preview(
    points: list[dict[str, Any]], horizon_ms: int
) -> tuple[str, str | None, list[dict[str, Any]], dict[str, Any] | None]:
    velocity = _velocity(points)
    if horizon_ms <= 0:
        return "not_requested", "prediction_horizon_ms_is_zero", [], velocity
    if len(points) < 2:
        return "not_available", "at_least_two_observed_points_required", [], velocity
    if velocity is None:
        return "not_available", "no_valid_recent_time_segment", [], velocity

    last = points[-1]
    segment_ms = max(33, min(100, int(round((horizon_ms / MAX_PREDICTION_POINTS)))))
    count = min(MAX_PREDICTION_POINTS, max(1, math.ceil(horizon_ms / segment_ms)))
    vx = float(velocity["vx_normalized_per_s"] or 0.0)
    vy = float(velocity["vy_normalized_per_s"] or 0.0)
    predictions: list[dict[str, Any]] = []
    for index in range(1, count + 1):
        # Keep the response bounded to eight points while ensuring the final
        # point always reaches the requested horizon (including 5 s).  A fixed
        # eight-point cap must not silently return only the first 800 ms.
        delta_ms = min(horizon_ms, int(round(horizon_ms * index / count)))
        if delta_ms <= 0:
            continue
        raw_x = float(last["x"]) + vx * delta_ms / 1000.0
        raw_y = float(last["y"]) + vy * delta_ms / 1000.0
        predictions.append(
            {
                "timestamp_ms": int(last["timestamp_ms"]) + delta_ms,
                "x": _round(_clamp01(raw_x)),
                "y": _round(_clamp01(raw_y)),
                "clamped_to_frame": raw_x != _clamp01(raw_x)
                or raw_y != _clamp01(raw_y),
                "source": "constant_velocity_extrapolation",
            }
        )
    return "heuristic_preview", None, predictions, velocity


def _empty_track_payload(include_box: bool) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "track_id": None,
        "track_id_status": "not_observed",
        "track_count": 0,
        "observed_count": 0,
        "coverage_fraction": 0.0,
        "first_timestamp_ms": None,
        "last_timestamp_ms": None,
        "longest_missing_run_frames": 0,
        "missing_run_semantics": "not_observed",
        "confidence": {"mean": None, "median": None, "min": None, "max": None},
        "observed": [],
    }
    if include_box:
        payload["geometry_status"] = "bbox_only"
    return payload


def build_trajectory_preview(
    frames_path: str | Path,
    *,
    sample_limit: int = DEFAULT_SAMPLE_LIMIT,
    prediction_horizon_ms: int = DEFAULT_PREDICTION_HORIZON_MS,
) -> dict[str, Any]:
    """Build a bounded, JSON-safe trajectory/racket observability payload.

    ``sample_limit`` bounds each returned observed track, while all points are
    still read to compute coverage and confidence.  The function is pure with
    respect to the artifact and does not mutate it.
    """

    if isinstance(sample_limit, bool) or not isinstance(sample_limit, int):
        raise TrajectoryExtractionError("sample_limit must be an integer")
    if not 1 <= sample_limit <= 1000:
        raise TrajectoryExtractionError("sample_limit must be in the 1..1000 range")
    if isinstance(prediction_horizon_ms, bool) or not isinstance(
        prediction_horizon_ms, int
    ):
        raise TrajectoryExtractionError("prediction_horizon_ms must be an integer")
    if not 0 <= prediction_horizon_ms <= 5000:
        raise TrajectoryExtractionError(
            "prediction_horizon_ms must be in the 0..5000 range"
        )

    path = Path(frames_path).resolve()
    frame_count, metadata, tracks, dimensions = _read_observations(path)
    ball_track_id, ball_points = _select_track(tracks["ball"])
    racket_track_id, racket_points = _select_track(tracks["racket"])

    ball = (
        _track_payload(
            selected_track_id=ball_track_id,
            points=ball_points,
            track_count=len(tracks["ball"]),
            frame_count=frame_count,
            sample_limit=sample_limit,
            include_box=False,
        )
        if ball_points
        else _empty_track_payload(False)
    )
    prediction_status, prediction_reason, predictions, velocity = (
        _constant_velocity_preview(ball_points, prediction_horizon_ms)
        if ball_points
        else ("not_available", "ball_not_observed", [], None)
    )
    ball.update(
        {
            "prediction_status": prediction_status,
            "prediction_reason": prediction_reason,
            "prediction_horizon_ms": prediction_horizon_ms,
            "predicted": predictions,
            "predicted_covered_horizon_ms": (
                int(predictions[-1]["timestamp_ms"] - ball_points[-1]["timestamp_ms"])
                if predictions
                else 0
            ),
            "velocity": velocity,
            "semantics": (
                "observed_detection_centers_plus_constant_velocity_extrapolation;"
                " not_a_physics_model_or_accuracy_claim"
            ),
        }
    )

    racket = (
        _track_payload(
            selected_track_id=racket_track_id,
            points=racket_points,
            track_count=len(tracks["racket"]),
            frame_count=frame_count,
            sample_limit=sample_limit,
            include_box=True,
        )
        if racket_points
        else _empty_track_payload(True)
    )
    racket.update(
        {
            # The Stage 1 frames contract carries independent racket detections
            # but no link to the selected primary-player timeline.  Keep this
            # explicit so consumers do not present an opponent's racket as the
            # target athlete's equipment.
            "association_status": "unassociated",
            "association_reason": "primary_player_link_not_present_in_frames_contract",
            "recognition_status": (
                "generic_bbox_detection" if racket_points else "not_observed"
            ),
            "keypoint_status": "not_available_from_current_detection_contract",
            "prediction_status": "not_available",
            "prediction_reason": "racket_keypoints_and_motion_model_not_configured",
            "semantics": (
                "generic_tennis_racket_bbox_observability_only;"
                " not_racket_keypoint_or_contact_accuracy"
            ),
        }
    )

    has_observations = bool(ball_points or racket_points)
    limitations = [
        "球轨迹来自逐帧通用 sports-ball 检测框中心，不等同网球专项轨迹模型。",
        "prediction_status=heuristic_preview 时仅做短时常速度外推，不代表真实飞行轨迹、旋转、落点或识别准确率。",
        "当前球拍输出只有通用 bbox 与 track 置信度，没有拍头、拍柄、拍面或甜区关键点。",
        "球拍框尚未关联 primary_player 时间线（association_status=unassociated），不代表目标球员球拍。",
        "coverage_fraction 与 confidence 描述可观测性，不是 precision、recall 或教练评分。",
    ]
    return {
        "schema_version": TRAJECTORY_SCHEMA_VERSION,
        "result_kind": TRAJECTORY_RESULT_KIND,
        "status": "ready" if has_observations else "not_observed",
        "source": {
            "frames_path": str(path),
            "frames_schema_versions": sorted(metadata["schema_version"]),
            "frame_count": frame_count,
            "width": dimensions["width"],
            "height": dimensions["height"],
            "coordinate_space": "normalized_frame_0_1",
            "timebase": "source_video_timestamp_ms",
        },
        "ball": ball,
        "racket": racket,
        "limitations": limitations,
    }


__all__ = [
    "DEFAULT_PREDICTION_HORIZON_MS",
    "DEFAULT_SAMPLE_LIMIT",
    "MAX_FRAMES_ARTIFACT_BYTES",
    "MAX_FRAMES_ARTIFACT_LINES",
    "MAX_OBSERVATION_POINTS",
    "TRAJECTORY_RESULT_KIND",
    "TRAJECTORY_SCHEMA_VERSION",
    "TrajectoryExtractionError",
    "build_trajectory_preview",
]
