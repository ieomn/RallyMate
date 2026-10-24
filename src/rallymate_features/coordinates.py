from __future__ import annotations

import math
from copy import deepcopy
from collections import Counter
from fractions import Fraction
from typing import Any

import numpy as np

from rallymate_features.schemas import PoseSequence, validated_source_time_binding
from rallymate_features.validity import valid_point_mask


COORDINATE_CONTRACT_VERSION = "isotropic-frame-long-edge-v1.1.0"
POSE_ASSOCIATION_LEDGER_VERSION = "source-pose-person-association-ledger-v1.0.0"


def validated_camera_transform(camera: Any) -> np.ndarray | None:
    """Accept only a declared, bounded, orientation-preserving similarity map.

    The map is current original pixels -> the epoch's reference pixels. This
    validates geometry, not optical-flow accuracy or anatomical 3D rotation.
    """
    if not isinstance(camera, dict) or camera.get("compensation_valid") is not True:
        return None
    try:
        matrix = np.asarray(camera.get("matrix_to_reference"))
        if matrix.shape != (2, 3) or matrix.dtype.kind not in "fiu":
            return None
        matrix = matrix.astype(np.float64)
    except (TypeError, ValueError, OverflowError):
        return None
    if not np.isfinite(matrix).all():
        return None
    linear = matrix[:, :2]
    determinant = float(np.linalg.det(linear))
    if not .25 <= determinant <= 4:
        return None
    # A positive determinant alone would still allow severe shear or unequal
    # x/y scaling, which would reintroduce the angle distortion fixed here.
    gram = linear.T @ linear
    if not np.allclose(gram, np.eye(2) * determinant, rtol=1e-5, atol=1e-8):
        return None
    return matrix


def _finite_number(value: Any) -> bool:
    try:
        return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)
    except OverflowError:
        return False


def _bound_model_points(record, selected, *, unique_source):
    """Authorize raw model geometry using independent decoder and selection data."""
    from rallymate_scoring.pose_observations import sam_source_bound_points, SOURCE_FIELDS
    frame = record["frame"]
    if (not isinstance(selected, dict) or selected.get("selection_status") != "selected"
            or type(selected.get("primary_player_id")) is not int or selected["primary_player_id"] != 1
            or selected.get("identity_ambiguous") is not False or not unique_source
            or any(selected.get(key) != frame.get(other) for key, other in (
                ("processed_index", "processed_index"), ("source_frame_index", "index"), ("timestamp_ms", "timestamp_ms")))):
        return None
    track = selected.get("source_track_id")
    if type(track) is not int or track < 0:
        return None
    poses = [p for p in record.get("poses", []) if p.get("person_track_id") == track]
    detections = [d for d in record.get("detections", []) if d.get("class_name") == "player" and d.get("track_id") == track]
    if len(poses) != 1 or len(detections) != 1:
        return None
    camera = record.get("camera_motion")
    try:
        result = sam_source_bound_points(poses[0], {**frame, "camera_motion": camera})
    except (OverflowError, TypeError, ValueError):
        return None
    if result is None:
        return None
    points, binding = result
    if (not isinstance(frame.get("video_sha256"), str)
            or frame["video_sha256"].lower() != binding["video_sha256"].lower()
            or any(type(frame.get(key)) is not int or frame[key] != binding[key] for key in ("pts", "time_base_num", "time_base_den"))
            or not isinstance(camera, dict) or type(camera.get("reference_epoch")) is not int
            or camera["reference_epoch"] != binding["camera_reference_epoch"]
            or validated_camera_transform(camera) is None
            or "selection_epoch" in selected and (type(selected["selection_epoch"]) is not int or selected["selection_epoch"] != binding["selection_epoch"])):
        return None
    ledger = record.get("sam3d_requests")
    requests = [r for r in ledger if isinstance(r, dict) and type(r.get("person_track_id")) is int and r["person_track_id"] == track] if isinstance(ledger, list) else []
    if len(requests) != 1:
        return None
    request = requests[0]
    if (request.get("status") != "ok" or type(request.get("selection_epoch")) is not int
            or request["selection_epoch"] != binding["selection_epoch"]
            or any(key in request and (type(request[key]) is not type(binding[key]) or request[key] != binding[key]) for key in SOURCE_FIELDS)):
        return None
    return points, {key: binding[key] for key in SOURCE_FIELDS}


def _source_time_binding(record, selected, *, unique_source):
    """Retain verified decoder PTS without authorizing any pose geometry."""
    frame = record["frame"]
    if not unique_source:
        return None, "duplicate_source_frame"
    if frame.get("timestamp_source") != "decoder_pts":
        return None, "decoder_pts_provenance_unavailable"
    if not isinstance(selected, dict):
        return None, "primary_timeline_missing_or_duplicated"
    if (selected.get("selection_status") != "selected" or selected.get("identity_ambiguous") is not False
            or type(selected.get("primary_player_id")) is not int or selected["primary_player_id"] != 1
            or type(selected.get("source_track_id")) is not int or selected["source_track_id"] < 0):
        return None, "selected_identity_unverified"
    if any(type(selected.get(key)) is not int or selected[key] != frame.get(other) for key, other in (
            ("processed_index", "processed_index"), ("source_frame_index", "index"), ("timestamp_ms", "timestamp_ms"))):
        return None, "primary_timeline_frame_or_video_time_mismatch"
    camera = record.get("camera_motion")
    epoch = camera.get("reference_epoch") if isinstance(camera, dict) else None
    selection_epoch = selected.get("selection_epoch")
    if any(type(value) is not int or value < 0 for value in (epoch, selection_epoch)):
        return None, "source_identity_epoch_unavailable"
    binding = {"frame_index": frame.get("index"), "processed_index": frame.get("processed_index"),
        "video_sha256": frame.get("video_sha256"), "pts": frame.get("pts"),
        "time_base_num": frame.get("time_base_num"), "time_base_den": frame.get("time_base_den"),
        "source_timestamp_ms": frame.get("source_timestamp_ms"), "video_timestamp_ms": frame.get("timestamp_ms"),
        "timestamp_source": frame.get("timestamp_source"), "primary_player_id": selected["primary_player_id"],
        "person_track_id": selected["source_track_id"], "selection_epoch": selection_epoch,
        "camera_reference_epoch": epoch}
    binding = validated_source_time_binding(binding)
    if binding is None:
        return None, "decoder_source_time_binding_invalid"
    for row, fields in ((selected, ("source_timestamp_ms", "camera_reference_epoch", "video_sha256",
                                    "pts", "time_base_num", "time_base_den")),
                        (frame, ("selection_epoch", "camera_reference_epoch", "person_track_id"))):
        for key in fields:
            if key not in row:
                continue
            actual, expected = row[key], binding[key]
            if key == "video_sha256" and isinstance(actual, str):
                actual = actual.lower()
            if (isinstance(actual, bool) or actual != expected
                    or key in {"camera_reference_epoch", "pts", "time_base_num", "time_base_den",
                               "selection_epoch", "person_track_id"} and type(actual) is not int):
                return None, "decoder_timeline_identity_or_clock_mismatch"
    benchmark = record.get("benchmark_provenance")
    if benchmark is not None:
        if (not isinstance(benchmark, dict)
                or type(benchmark.get("selected_person_track_id")) is not int
                or benchmark["selected_person_track_id"] != binding["person_track_id"]
                or type(benchmark.get("selection_epoch")) is not int
                or benchmark["selection_epoch"] != binding["selection_epoch"]):
            return None, "frozen_selection_identity_mismatch"
    return binding, None


def _frame_dimensions(frame: dict[str, Any]) -> tuple[int, int] | None:
    values = frame.get("width"), frame.get("height")
    if not all(isinstance(value, int) and not isinstance(value, bool) and value > 0 for value in values):
        return None
    return values


def _isotropic_point(point: dict[str, Any], dimensions: tuple[int, int], transform: np.ndarray) -> tuple[list[float] | None, str]:
    width, height = dimensions
    if point.get("in_frame") is False:
        return None, "out_of_frame"
    confidence = point.get("confidence")
    if not _finite_number(confidence) or not 0 <= confidence <= 1:
        return None, "invalid_confidence"
    # A malformed or partial pixel observation is not rescued by another
    # representation of that same observation.
    if "x_px" in point or "y_px" in point:
        x, y = point.get("x_px"), point.get("y_px")
        source = "original_frame_pixels"
    else:
        x, y = point.get("x_normalized"), point.get("y_normalized")
        if not all(_finite_number(value) and 0 <= value <= 1 for value in (x, y)):
            return None, "invalid_normalized_coordinate"
        x, y = x * width, y * height
        source = "normalized_with_explicit_dimensions"
    if not all(_finite_number(value) for value in (x, y)) or not (0 <= x <= width and 0 <= y <= height):
        return None, "invalid_pixel_coordinate"
    denominator = max(width, height)
    corrected = transform @ np.asarray([x, y, 1.0])
    if not np.isfinite(corrected).all():
        return None, "invalid_transformed_coordinate"
    # A valid reference coordinate may fall outside the current image after a
    # camera pan; do not clamp it or change the measured geometry.
    return (corrected / denominator).tolist(), source


def pose_sequence_from_records(
    records: list[dict[str, Any]],
    primary_timeline: list[dict[str, Any]],
) -> PoseSequence:
    """Convert source observations to equal-axis image-plane coordinates.

    Divide *both* pixel axes by the frame's long edge. Independent width/
    height normalization is only a storage format, not Euclidean geometry.
    Missing frame dimensions make that frame unavailable; no aspect ratio is
    inferred for legacy records.
    """
    from rallymate_vision.pose.association import audit_pose_person_associations, POLICY_VERSION
    timeline = {}
    for item in primary_timeline:
        index = int(item["processed_index"])
        timeline[index] = item if index not in timeline else None
    has_model_estimates = any(pose.get("source") == "sam3d" for record in records for pose in record.get("poses", []))
    source_frame_counts = Counter((str(record["frame"].get("video_sha256", "")).lower(), record["frame"].get("index")) for record in records)
    time_frame_counts = Counter(record["frame"].get("index") for record in records)
    names = sorted(
        {
            point["name"]
            for record in records
            for pose in record.get("poses", [])
            for point in pose.get("keypoints", [])
            if isinstance(point.get("name"), str)
        }
    )
    timestamps = []
    frames = []
    keypoints = {name: [] for name in names}
    confidences = {name: [] for name in names}
    model_masks = {name: [] for name in names}
    model_bindings = []
    source_time_bindings = {}
    pose_association_rows = {}
    source_time_rejections = {}
    time_origin_ranges = {}
    dimensions_by_frame = []
    dimension_counts: Counter[tuple[int, int]] = Counter()
    source_counts: Counter[str] = Counter()
    rejected_counts: Counter[str] = Counter()
    invalid_dimensions_frames = []
    has_camera_observations = any("camera_motion" in record for record in records)
    camera_statuses = []
    camera_epochs = []
    transforms = []
    compensated_frames = []
    has_reference_epochs = any(isinstance(record.get("camera_motion"), dict) and "reference_epoch" in record["camera_motion"]
                               for record in records)
    for record in records:
        frame = record["frame"]
        camera = record.get("camera_motion")
        camera_status = camera.get("status") if isinstance(camera, dict) else None
        epoch = camera.get("reference_epoch") if isinstance(camera, dict) else None
        epoch = epoch if isinstance(epoch, int) and not isinstance(epoch, bool) and epoch >= 0 else None
        transform = np.eye(2, 3)
        declared_compensation = isinstance(camera, dict) and ("compensation_valid" in camera or "matrix_to_reference" in camera)
        if declared_compensation:
            transform = validated_camera_transform(camera)
            if transform is None or epoch is None or camera_status not in {"reference", "stationary", "moving"}:
                transform = None
                camera_status = "unavailable"
            else:
                compensated_frames.append(int(frame["index"]))
                if camera_status == "moving":
                    camera_status = "compensated"
        camera_statuses.append(camera_status if camera_status in {"reference", "stationary", "compensated", "moving", "unavailable"} else "unavailable")
        camera_epochs.append(epoch)
        transforms.append(transform if transform is not None else np.full((2, 3), np.nan))
        processed_index = int(frame["processed_index"])
        selected = timeline.get(processed_index)
        # Recompute from all original same-frame poses before selecting one.
        # Caller-supplied audit flags are never used as a source of authority.
        frame_size = _frame_dimensions(frame)
        audit = audit_pose_person_associations(record.get("poses", []),
            [value for value in record.get("detections", []) if value.get("class_name") == "player"],
            frame_size=frame_size) if frame_size is not None else None
        pose_association_rows[str(int(frame["index"]))] = {
            "frame_index": int(frame["index"]), "processed_index": processed_index,
            **{key: deepcopy(frame.get(key)) for key in ("video_sha256", "pts", "time_base_num", "time_base_den")},
            "selected_source_track_id": selected.get("source_track_id") if selected else None,
            "audit": audit, "audit_unavailable_reason": None if audit is not None else "frame_dimensions_unavailable",
        }
        time_binding, time_reason = _source_time_binding(record, selected,
            unique_source=time_frame_counts[frame.get("index")] == 1)
        if time_binding is not None:
            identity = tuple(time_binding[key] for key in ("video_sha256", "time_base_num", "time_base_den"))
            exact_ms = Fraction(time_binding["pts"] * time_binding["time_base_num"] * 1000, time_binding["time_base_den"])
            offset = exact_ms - time_binding["video_timestamp_ms"]
            lower, upper = offset - Fraction(51, 100), offset + Fraction(51, 100)
            if identity in time_origin_ranges:
                previous_lower, previous_upper = time_origin_ranges[identity]
                lower, upper = max(lower, previous_lower), min(upper, previous_upper)
            if lower > upper:
                time_binding, time_reason = None, "source_and_video_time_origin_mismatch"
            else:
                time_origin_ranges[identity] = lower, upper
        if time_binding is not None:
            source_time_bindings[int(frame["index"])] = time_binding
        else:
            source_time_rejections[int(frame["index"])] = time_reason
        source_track_id = selected.get("source_track_id") if selected else None
        pose = next(
            (
                value
                for value in record.get("poses", [])
                if value.get("person_track_id") == source_track_id
            ),
            None,
        )
        bound = _bound_model_points(record, selected, unique_source=source_frame_counts[(str(frame.get("video_sha256", "")).lower(), frame.get("index"))] == 1) if has_model_estimates else None
        model_bindings.append(bound[1] if bound is not None else None)
        point_map = {
            point["name"]: point for point in pose.get("keypoints", [])
        } if pose is not None else {}
        timestamps.append(int(frame["timestamp_ms"]))
        frames.append(int(frame["index"]))
        dimensions = _frame_dimensions(frame)
        dimensions_by_frame.append(dimensions if dimensions is not None else [math.nan, math.nan])
        if dimensions is None:
            invalid_dimensions_frames.append(int(frame["index"]))
        else:
            dimension_counts[dimensions] += 1
        for name in names:
            point = point_map.get(name)
            if dimensions is None:
                xy, reason = None, "frame_dimensions_unavailable"
            elif transform is None:
                xy, reason = None, "camera_reference_transform_unavailable"
            elif point is None:
                xy, reason = None, "point_unobserved"
            elif has_model_estimates:
                raw = bound[0].get(name) if bound is not None else None
                corrected = transform @ np.asarray([*raw, 1.0]) if raw is not None else None
                xy = (corrected / max(dimensions)).tolist() if corrected is not None and np.isfinite(corrected).all() else None
                reason = "source_bound_model_estimate_raw_pixels" if xy is not None else "model_geometry_unbound_or_unavailable"
            else:
                xy, reason = _isotropic_point(point, dimensions, transform)
            model_masks[name].append(bool(has_model_estimates and xy is not None))
            if xy is None:
                keypoints[name].append([math.nan, math.nan])
                confidences[name].append(math.nan)
                rejected_counts[reason] += 1
            else:
                keypoints[name].append(xy)
                confidences[name].append(math.nan if has_model_estimates else float(point["confidence"]))
                source_counts[reason] += 1
    return PoseSequence(
        timestamp_ms=np.asarray(timestamps, dtype=np.int64),
        source_frames=np.asarray(frames, dtype=np.int64),
        keypoints_xy={name: np.asarray(values, dtype=np.float64) for name, values in keypoints.items()},
        confidence={name: np.asarray(values, dtype=np.float64) for name, values in confidences.items()},
        frame_dimensions_px=np.asarray(dimensions_by_frame, dtype=np.float64).reshape((-1, 2)),
        camera_motion_status=tuple(camera_statuses) if has_camera_observations else None,
        frame_transforms_to_reference=np.asarray(transforms, dtype=np.float64).reshape((-1, 2, 3)),
        camera_reference_epochs=tuple(camera_epochs) if has_reference_epochs else None,
        model_estimate_masks={name: np.asarray(values, dtype=bool) for name, values in model_masks.items()} if has_model_estimates else {},
        model_estimate_bindings=tuple(model_bindings) if has_model_estimates else None,
        source_time_bindings=source_time_bindings,
        coordinate_metadata={
            "contract_version": COORDINATE_CONTRACT_VERSION,
            "coordinate_system": "camera_reference_xy_divided_by_source_long_edge",
            "axis_units": "same_for_x_and_y",
            "is_metric_or_3d": False,
            "source_pose_person_association": {
                "version": POSE_ASSOCIATION_LEDGER_VERSION, "policy_version": POLICY_VERSION,
                "source": "recomputed_from_original_record_poses_and_player_detections",
                "scope": "same_source_frame_geometric_duplicate_audit_not_visual_identity_truth",
                "rows_by_source_frame": pose_association_rows,
            },
            "source_time_binding": {
                "version": "decoder-source-time-binding-v1",
                "semantics": "decoder_time_and_selected_identity_only_not_pose_validity_or_visibility",
                "exact_time_formula": "Fraction(pts * time_base_num * 1000, time_base_den)",
                "export_timestamp_check_tolerance_ms": .51,
                "duration_or_window_tolerance_ms": 0,
                "video_clock_policy": "one_constant_origin_per_video_timebase_with_export_rounding_only",
                "bound_frame_count": len(source_time_bindings),
                "unavailable_source_frames": source_time_rejections,
            },
            "camera_compensation": {
                "method": "validated_current_pixels_to_reference_similarity",
                "semantics": "background_image_motion_compensation_not_3d_or_anatomical_rotation",
                "applied_source_frames": compensated_frames,
                "reference_epochs": sorted({epoch for epoch in camera_epochs if epoch is not None}),
                "scale_bounds": [0.5, 2.0],
                "unverified_legacy_frames_use_identity_only": True,
            },
            "frame_dimensions": [
                {"width": size[0], "height": size[1], "frame_count": count}
                for size, count in sorted(dimension_counts.items())
            ],
            "invalid_dimensions_source_frames": invalid_dimensions_frames,
            "point_source_counts": dict(sorted(source_counts.items())),
            "rejected_point_counts": dict(sorted(rejected_counts.items())),
            "camera_motion_verification": {
                "status": "observed" if has_camera_observations else "camera_motion_unverified",
                "status_counts": dict(sorted(Counter(camera_statuses).items())) if has_camera_observations else {},
                "verified_fraction": (sum(status in {"reference", "stationary", "compensated", "moving"} for status in camera_statuses) / len(records)
                                      if records and has_camera_observations else 0.0),
            },
        },
    )


def point_series(
    sequence: PoseSequence,
    name: str,
    *,
    confidence_min: float = 0.25,
) -> tuple[np.ndarray, np.ndarray]:
    if name not in sequence.keypoints_xy:
        values = np.full((sequence.timestamp_ms.size, 2), np.nan)
        return values, np.zeros(sequence.timestamp_ms.size, dtype=bool)
    values = np.asarray(sequence.keypoints_xy[name], dtype=np.float64).copy()
    if sequence.model_estimate_bindings is not None:
        mask = np.asarray(sequence.model_estimate_masks.get(name, np.zeros(len(values), dtype=bool))) & np.isfinite(values).all(axis=1)
    else:
        mask = valid_point_mask(values, sequence.confidence[name], confidence_min=confidence_min)
    values[~mask] = np.nan
    return values, mask


def center_series(
    sequence: PoseSequence,
    first: str,
    second: str,
) -> tuple[np.ndarray, np.ndarray]:
    a, valid_a = point_series(sequence, first)
    b, valid_b = point_series(sequence, second)
    valid = valid_a & valid_b
    center = (a + b) / 2.0
    center[~valid] = np.nan
    return center, valid


def shoulder_center(sequence: PoseSequence) -> tuple[np.ndarray, np.ndarray]:
    return center_series(sequence, "left_shoulder", "right_shoulder")


def hip_center(sequence: PoseSequence) -> tuple[np.ndarray, np.ndarray]:
    return center_series(sequence, "left_hip", "right_hip")


def body_center(sequence: PoseSequence) -> tuple[np.ndarray, np.ndarray]:
    shoulder, shoulder_valid = shoulder_center(sequence)
    hip, hip_valid = hip_center(sequence)
    valid = shoulder_valid & hip_valid
    center = (shoulder + hip) / 2.0
    center[~valid] = np.nan
    return center, valid


def body_scale(sequence: PoseSequence) -> tuple[np.ndarray, np.ndarray]:
    left_shoulder, ls_valid = point_series(sequence, "left_shoulder")
    right_shoulder, rs_valid = point_series(sequence, "right_shoulder")
    left_hip, lh_valid = point_series(sequence, "left_hip")
    right_hip, rh_valid = point_series(sequence, "right_hip")
    shoulders, shoulders_valid = shoulder_center(sequence)
    hips, hips_valid = hip_center(sequence)
    candidates = np.column_stack(
        [
            np.linalg.norm(left_shoulder - right_shoulder, axis=1),
            np.linalg.norm(left_hip - right_hip, axis=1),
            np.linalg.norm(shoulders - hips, axis=1),
        ]
    )
    candidate_valid = np.column_stack(
        [ls_valid & rs_valid, lh_valid & rh_valid, shoulders_valid & hips_valid]
    )
    candidates[~candidate_valid] = np.nan
    candidates[candidates <= 1e-6] = np.nan
    scale = np.full(sequence.timestamp_ms.size, np.nan)
    for index, row in enumerate(candidates):
        finite = row[np.isfinite(row)]
        if finite.size:
            scale[index] = float(np.median(finite))
    valid = np.isfinite(scale)
    return scale, valid
