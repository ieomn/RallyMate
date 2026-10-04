from __future__ import annotations

import math
from collections import Counter
from typing import Any

import numpy as np

from rallymate_features.schemas import PoseSequence
from rallymate_features.validity import valid_point_mask


COORDINATE_CONTRACT_VERSION = "isotropic-frame-long-edge-v1.1.0"


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
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


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
    timeline = {int(item["processed_index"]): item for item in primary_timeline}
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
        source_track_id = selected.get("source_track_id") if selected else None
        pose = next(
            (
                value
                for value in record.get("poses", [])
                if value.get("person_track_id") == source_track_id
            ),
            None,
        )
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
            else:
                xy, reason = _isotropic_point(point, dimensions, transform)
            if xy is None:
                keypoints[name].append([math.nan, math.nan])
                confidences[name].append(math.nan)
                rejected_counts[reason] += 1
            else:
                keypoints[name].append(xy)
                confidences[name].append(float(point["confidence"]))
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
        coordinate_metadata={
            "contract_version": COORDINATE_CONTRACT_VERSION,
            "coordinate_system": "camera_reference_xy_divided_by_source_long_edge",
            "axis_units": "same_for_x_and_y",
            "is_metric_or_3d": False,
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
    mask = valid_point_mask(
        values, sequence.confidence[name], confidence_min=confidence_min
    )
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
