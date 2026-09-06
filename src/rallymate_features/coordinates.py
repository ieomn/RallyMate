from __future__ import annotations

import math
from typing import Any

import numpy as np

from rallymate_features.schemas import PoseSequence
from rallymate_features.validity import valid_point_mask


def pose_sequence_from_records(
    records: list[dict[str, Any]],
    primary_timeline: list[dict[str, Any]],
) -> PoseSequence:
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
    for record in records:
        frame = record["frame"]
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
        for name in names:
            point = point_map.get(name)
            if point is None or point.get("in_frame") is False:
                keypoints[name].append([math.nan, math.nan])
                confidences[name].append(math.nan)
            else:
                keypoints[name].append(
                    [float(point["x_normalized"]), float(point["y_normalized"])]
                )
                confidences[name].append(float(point["confidence"]))
    return PoseSequence(
        timestamp_ms=np.asarray(timestamps, dtype=np.int64),
        source_frames=np.asarray(frames, dtype=np.int64),
        keypoints_xy={name: np.asarray(values, dtype=np.float64) for name, values in keypoints.items()},
        confidence={name: np.asarray(values, dtype=np.float64) for name, values in confidences.items()},
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
