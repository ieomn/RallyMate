from __future__ import annotations

import json
import math
from pathlib import Path
from typing import Any

import numpy as np

from rallymate_features.schemas import PoseSequence
from rallymate_features.coordinates import COORDINATE_CONTRACT_VERSION, validated_camera_transform


def validate_keypoint_annotation(record: dict[str, Any]) -> None:
    required = {
        "schema_version",
        "video_id",
        "source_frame_index",
        "timestamp_ms",
        "primary_player_id",
        "annotator_id",
        "view_group",
        "joints",
    }
    missing = required - set(record)
    if missing:
        raise ValueError(f"keypoint annotation missing fields: {sorted(missing)}")
    if record["schema_version"] != "1.0.0":
        raise ValueError("unsupported keypoint annotation schema_version")
    if not isinstance(record["video_id"], str) or not record["video_id"]:
        raise ValueError("video_id is required")
    if not isinstance(record["source_frame_index"], int) or record["source_frame_index"] < 0:
        raise ValueError("source_frame_index must be non-negative")
    if not isinstance(record["timestamp_ms"], int) or record["timestamp_ms"] < 0:
        raise ValueError("timestamp_ms must be non-negative")
    if record["primary_player_id"] != 1:
        raise ValueError("this scoring loop only supports primary_player_id=1")
    if not isinstance(record["annotator_id"], str) or not record["annotator_id"]:
        raise ValueError("annotator_id is required")
    if "adjudication_status" in record and record["adjudication_status"] != "accepted":
        raise ValueError("compiled keypoint truth must be adjudication_status=accepted")
    if "adjudication_status" in record and (
        not isinstance(record.get("reviewer_id"), str) or not record["reviewer_id"]
    ):
        raise ValueError("accepted keypoint truth requires reviewer_id")
    if not isinstance(record["view_group"], str) or not record["view_group"]:
        raise ValueError("view_group is required")
    joints = record["joints"]
    if not isinstance(joints, dict) or not joints:
        raise ValueError("joints must be a non-empty object")
    for name, joint in joints.items():
        if not isinstance(name, str) or not isinstance(joint, dict):
            raise ValueError("joint annotations must be named objects")
        visible = joint.get("visible")
        if not isinstance(visible, bool):
            raise ValueError(f"{name}.visible must be boolean")
        x, y = joint.get("x_normalized"), joint.get("y_normalized")
        if visible:
            if not all(isinstance(value, (int, float)) and 0 <= value <= 1 for value in (x, y)):
                raise ValueError(f"visible {name} requires normalized coordinates")
        elif x is not None or y is not None:
            raise ValueError(f"missing {name} must use null coordinates, never zero-fill")


def load_keypoint_ground_truth(path: str | Path) -> list[dict[str, Any]]:
    records = []
    with Path(path).open("r", encoding="utf-8") as handle:
        for line_number, line in enumerate(handle, start=1):
            if not line.strip():
                continue
            try:
                record = json.loads(line)
            except json.JSONDecodeError as exc:
                raise ValueError(
                    f"invalid keypoint ground truth line {line_number}: {exc}"
                ) from exc
            validate_keypoint_annotation(record)
            records.append(record)
    return records


def apply_keypoint_corrections(
    sequence: PoseSequence,
    annotations: list[dict[str, Any]],
) -> PoseSequence:
    """Build an annotation-only sequence without leaking model predictions.

    Unannotated samples remain NaN.  A truth file containing multiple opinions
    for the same frame/joint must be adjudicated before feature evaluation;
    silently taking the last record would make the reported error depend on
    JSONL order.
    """

    frame_to_index = {
        int(frame): index for index, frame in enumerate(sequence.source_frames)
    }
    keypoints = {
        name: np.full(np.asarray(values).shape, np.nan, dtype=np.float64)
        for name, values in sequence.keypoints_xy.items()
    }
    confidence = {
        name: np.full(np.asarray(values).shape, np.nan, dtype=np.float64)
        for name, values in sequence.confidence.items()
    }
    applied: set[tuple[int, str]] = set()
    for annotation in annotations:
        validate_keypoint_annotation(annotation)
        frame = int(annotation["source_frame_index"])
        if frame not in frame_to_index:
            raise ValueError(f"annotation frame is absent from pose sequence: {frame}")
        index = frame_to_index[frame]
        if abs(int(sequence.timestamp_ms[index]) - int(annotation["timestamp_ms"])) > 2:
            raise ValueError(f"annotation timestamp does not match source frame {frame}")
        for name, joint in annotation["joints"].items():
            key = (frame, name)
            if key in applied:
                raise ValueError(
                    f"duplicate keypoint truth for frame {frame}, joint {name}; "
                    "adjudicate multiple annotators before evaluation"
                )
            applied.add(key)
            if name not in keypoints:
                keypoints[name] = np.full((sequence.timestamp_ms.size, 2), np.nan)
                confidence[name] = np.full(sequence.timestamp_ms.size, np.nan)
            if joint["visible"]:
                if (sequence.coordinate_metadata.get("contract_version") != COORDINATE_CONTRACT_VERSION
                        or sequence.frame_dimensions_px is None):
                    raise ValueError("normalized keypoint truth requires explicit source dimensions and coordinate contract")
                dimensions = np.asarray(sequence.frame_dimensions_px[index], dtype=np.float64)
                if not np.isfinite(dimensions).all() or np.any(dimensions <= 0):
                    raise ValueError("normalized keypoint truth requires valid source frame dimensions")
                point_px = np.asarray([
                    float(joint["x_normalized"]),
                    float(joint["y_normalized"]),
                ]) * dimensions
                transform = (sequence.frame_transforms_to_reference[index]
                             if sequence.frame_transforms_to_reference is not None else np.eye(2, 3))
                transform = validated_camera_transform({"compensation_valid": True, "matrix_to_reference": transform})
                if transform is None:
                    raise ValueError("manual keypoint truth requires a valid camera reference transform")
                keypoints[name][index] = (transform @ np.r_[point_px, 1.0]) / max(dimensions)
                confidence[name][index] = 1.0
            else:
                keypoints[name][index] = [math.nan, math.nan]
                confidence[name][index] = math.nan
    return PoseSequence(
        timestamp_ms=sequence.timestamp_ms.copy(),
        source_frames=sequence.source_frames.copy(),
        keypoints_xy=keypoints,
        confidence=confidence,
        primary_player_id=sequence.primary_player_id,
        frame_dimensions_px=(sequence.frame_dimensions_px.copy() if sequence.frame_dimensions_px is not None else None),
        coordinate_metadata={**sequence.coordinate_metadata, "observation_source": "manual_annotation_only"},
        camera_motion_status=sequence.camera_motion_status,
        frame_transforms_to_reference=(sequence.frame_transforms_to_reference.copy()
                                       if sequence.frame_transforms_to_reference is not None else None),
        camera_reference_epochs=sequence.camera_reference_epochs,
    )
