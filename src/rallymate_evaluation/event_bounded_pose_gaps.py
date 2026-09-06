from __future__ import annotations

import math
import hashlib
from collections.abc import Iterable
from dataclasses import replace
from pathlib import Path
from typing import Any

import numpy as np

from rallymate_features.schemas import EventInterval, PoseSequence
from rallymate_features.validity import DEFAULT_KEYPOINT_CONFIDENCE_MIN, valid_point_mask


EVENT_BOUNDED_POSE_GAP_VERSION = "event-bounded-pose-gap-counterfactual-v1.0.0"
EVENT_BOUNDED_POSE_GAP_REPORT_VERSION = "multivideo-event-bounded-pose-gap-audit-v1.0.0"


def _validate_contract(
    *,
    joint_names: Iterable[str],
    confidence_min: float,
    max_gap_ms: int,
) -> tuple[tuple[str, ...], float, int]:
    joints = tuple(dict.fromkeys(joint_names))
    if not joints or any(not isinstance(name, str) or not name for name in joints):
        raise ValueError("joint_names must contain non-empty unique names")
    try:
        threshold = float(confidence_min)
    except (TypeError, ValueError, OverflowError) as exc:
        raise ValueError("confidence_min must be finite and within [0, 1]") from exc
    if not math.isfinite(threshold) or not 0.0 <= threshold <= 1.0:
        raise ValueError("confidence_min must be finite and within [0, 1]")
    if isinstance(max_gap_ms, bool) or not isinstance(max_gap_ms, int) or max_gap_ms <= 0:
        raise ValueError("max_gap_ms must be a positive integer")
    return joints, threshold, max_gap_ms


def _missing_runs(mask: np.ndarray) -> list[tuple[int, int]]:
    runs: list[tuple[int, int]] = []
    start: int | None = None
    for index, value in enumerate(np.append(np.asarray(mask, dtype=bool), True)):
        if not value and start is None:
            start = index
        elif value and start is not None:
            runs.append((start, index - 1))
            start = None
    return runs


def interpolate_event_bounded_pose_gaps(
    sequence: PoseSequence,
    event: EventInterval,
    joint_names: Iterable[str],
    *,
    confidence_min: float = DEFAULT_KEYPOINT_CONFIDENCE_MIN,
    max_gap_ms: int = 160,
) -> tuple[PoseSequence, dict[str, Any]]:
    """Interpolate only short Pose gaps bounded by observations inside one event.

    This is an experimental measurement counterfactual, not a production Pose
    observation.  It never extrapolates at an event boundary, never crosses an
    event boundary, never fills with zero, and never creates an event phase.
    The effective confidence of a filled sample is the smaller confidence of
    its two observed boundary samples, rather than a fabricated model score.
    """

    joints, threshold, gap_limit = _validate_contract(
        joint_names=joint_names,
        confidence_min=confidence_min,
        max_gap_ms=max_gap_ms,
    )
    event_indexes = np.flatnonzero(
        (sequence.timestamp_ms >= event.start_ms)
        & (sequence.timestamp_ms <= event.end_ms)
    )
    if event_indexes.size == 0:
        raise ValueError("event does not overlap the Pose sequence")

    xy_by_joint = {
        name: np.asarray(values, dtype=np.float64).copy()
        for name, values in sequence.keypoints_xy.items()
    }
    confidence_by_joint = {
        name: np.asarray(values, dtype=np.float64).copy()
        for name, values in sequence.confidence.items()
    }
    joint_audits: list[dict[str, Any]] = []
    filled_pairs: list[tuple[str, int]] = []
    interpolated_points: list[dict[str, Any]] = []

    for joint in joints:
        if joint not in xy_by_joint or joint not in confidence_by_joint:
            joint_audits.append(
                {
                    "joint_name": joint,
                    "topology_available": False,
                    "observed_valid_count": 0,
                    "event_frame_count": int(event_indexes.size),
                    "interpolated_count": 0,
                    "remaining_invalid_count": int(event_indexes.size),
                    "runs": [],
                }
            )
            continue

        xy = xy_by_joint[joint]
        scores = confidence_by_joint[joint]
        global_valid = valid_point_mask(xy, scores, confidence_min=threshold)
        local_valid = global_valid[event_indexes]
        run_audits: list[dict[str, Any]] = []
        interpolated_count = 0
        for local_start, local_end in _missing_runs(local_valid):
            leading = local_start == 0
            trailing = local_end == event_indexes.size - 1
            bounded = not leading and not trailing
            left_global = int(event_indexes[local_start - 1]) if bounded else None
            right_global = int(event_indexes[local_end + 1]) if bounded else None
            span_ms = (
                int(sequence.timestamp_ms[right_global] - sequence.timestamp_ms[left_global])
                if bounded and left_global is not None and right_global is not None
                else None
            )
            eligible = bool(bounded and span_ms is not None and span_ms <= gap_limit)
            run_payload = {
                "kind": (
                    "whole_event_missing"
                    if leading and trailing
                    else "leading_boundary"
                    if leading
                    else "trailing_boundary"
                    if trailing
                    else "internal"
                ),
                "first_timestamp_ms": int(sequence.timestamp_ms[event_indexes[local_start]]),
                "last_timestamp_ms": int(sequence.timestamp_ms[event_indexes[local_end]]),
                "first_source_frame": int(sequence.source_frames[event_indexes[local_start]]),
                "last_source_frame": int(sequence.source_frames[event_indexes[local_end]]),
                "missing_sample_count": int(local_end - local_start + 1),
                "left_boundary_timestamp_ms": (
                    int(sequence.timestamp_ms[left_global]) if left_global is not None else None
                ),
                "right_boundary_timestamp_ms": (
                    int(sequence.timestamp_ms[right_global]) if right_global is not None else None
                ),
                "bounding_observation_span_ms": span_ms,
                "eligible_for_bounded_interpolation": eligible,
            }
            if eligible and left_global is not None and right_global is not None:
                denominator = float(
                    sequence.timestamp_ms[right_global]
                    - sequence.timestamp_ms[left_global]
                )
                effective_confidence = float(min(scores[left_global], scores[right_global]))
                for local_index in range(local_start, local_end + 1):
                    global_index = int(event_indexes[local_index])
                    alpha = float(
                        (sequence.timestamp_ms[global_index] - sequence.timestamp_ms[left_global])
                        / denominator
                    )
                    xy[global_index] = (
                        xy[left_global] + alpha * (xy[right_global] - xy[left_global])
                    )
                    scores[global_index] = effective_confidence
                    filled_pairs.append((joint, global_index))
                    interpolated_points.append(
                        {
                            "joint_name": joint,
                            "timestamp_ms": int(sequence.timestamp_ms[global_index]),
                            "source_frame": int(sequence.source_frames[global_index]),
                            "x_normalized": float(xy[global_index, 0]),
                            "y_normalized": float(xy[global_index, 1]),
                            "effective_confidence": effective_confidence,
                            "left_boundary_timestamp_ms": int(
                                sequence.timestamp_ms[left_global]
                            ),
                            "left_boundary_source_frame": int(
                                sequence.source_frames[left_global]
                            ),
                            "left_boundary_confidence": float(scores[left_global]),
                            "right_boundary_timestamp_ms": int(
                                sequence.timestamp_ms[right_global]
                            ),
                            "right_boundary_source_frame": int(
                                sequence.source_frames[right_global]
                            ),
                            "right_boundary_confidence": float(scores[right_global]),
                            "bounding_observation_span_ms": int(denominator),
                        }
                    )
                    interpolated_count += 1
            run_audits.append(run_payload)

        post_valid = valid_point_mask(xy, scores, confidence_min=threshold)[event_indexes]
        joint_audits.append(
            {
                "joint_name": joint,
                "topology_available": True,
                "observed_valid_count": int(local_valid.sum()),
                "event_frame_count": int(event_indexes.size),
                "interpolated_count": interpolated_count,
                "interpolated_source_frames": [
                    item["source_frame"]
                    for item in interpolated_points
                    if item["joint_name"] == joint
                ],
                "remaining_invalid_count": int((~post_valid).sum()),
                "runs": run_audits,
            }
        )

    filled_sequence = replace(
        sequence,
        keypoints_xy=xy_by_joint,
        confidence=confidence_by_joint,
    )
    return filled_sequence, {
        "algorithm_version": EVENT_BOUNDED_POSE_GAP_VERSION,
        "event_id": event.event_id,
        "event_code": event.event_code,
        "start_ms": int(event.start_ms),
        "end_ms": int(event.end_ms),
        "event_frame_count": int(event_indexes.size),
        "confidence_min": threshold,
        "max_bounding_observation_span_ms": gap_limit,
        "joint_names": list(joints),
        "interpolated_joint_observation_count": len(filled_pairs),
        "interpolated_source_frames": sorted(
            {int(sequence.source_frames[index]) for _, index in filled_pairs}
        ),
        "interpolated_points": interpolated_points,
        "joint_audits": joint_audits,
        "safety": {
            "event_boundary_extrapolation_allowed": False,
            "cross_event_interpolation_allowed": False,
            "zero_fill_allowed": False,
            "event_phase_creation_allowed": False,
            "interpolated_values_are_model_observations": False,
            "confidence_semantics": "minimum_of_two_observed_boundary_confidences_not_model_score",
            "production_scoring_allowed": False,
        },
    }


def validate_event_bounded_pose_gap_report(report: dict[str, Any]) -> None:
    if report.get("schema_version") != "1.0.0":
        raise ValueError("unsupported event-bounded Pose gap report schema")
    if report.get("report_version") != EVENT_BOUNDED_POSE_GAP_REPORT_VERSION:
        raise ValueError("unsupported event-bounded Pose gap report version")
    safety = report.get("safety")
    if not isinstance(safety, dict):
        raise ValueError("event-bounded Pose gap report safety is required")
    required_false = {
        "accuracy_claim",
        "ground_truth_provided",
        "grade_generated",
        "scoring_threshold_generated",
        "production_pose_artifact_generated",
        "production_scoring_allowed",
        "event_boundary_extrapolation_allowed",
        "cross_event_interpolation_allowed",
        "event_phase_creation_allowed",
    }
    if any(safety.get(key) is not False for key in required_false):
        raise ValueError("event-bounded Pose gap safety contract must fail closed")
    residual = report.get("residual_scope")
    items = report.get("items")
    summary = report.get("counterfactual_summary")
    if not isinstance(residual, dict) or not isinstance(items, list) or not isinstance(summary, dict):
        raise ValueError("event-bounded Pose gap report sections are missing")
    if residual.get("indicator_instance_count") != len(items):
        raise ValueError("residual indicator count does not match items")
    recovered = sum(bool(item.get("counterfactual_feature_vector_complete")) for item in items)
    if summary.get("counterfactual_recovered_indicator_instance_count") != recovered:
        raise ValueError("counterfactual recovery count does not match items")
    for item in items:
        audit = item.get("gap_audit")
        if not isinstance(audit, dict):
            raise ValueError("every item must include gap_audit")
        if audit.get("safety", {}).get("production_scoring_allowed") is not False:
            raise ValueError("per-event counterfactual cannot allow production scoring")


def validate_event_bounded_pose_gap_report_sources(report: dict[str, Any]) -> None:
    validate_event_bounded_pose_gap_report(report)
    sources = report.get("sources", {})
    records = [sources.get("m73_summary"), sources.get("registry")]
    for video in sources.get("per_video", []):
        records.extend(
            video.get(name)
            for name in ("m73_report", "frames", "primary_timeline", "events")
        )
    if not records or any(not isinstance(record, dict) for record in records):
        raise ValueError("event-bounded Pose gap source records are incomplete")
    for record in records:
        path = Path(str(record.get("path", ""))).resolve()
        expected = record.get("sha256")
        if (
            not path.is_file()
            or not isinstance(expected, str)
            or len(expected) != 64
        ):
            raise ValueError("event-bounded Pose gap source is missing")
        digest = hashlib.sha256()
        with path.open("rb") as handle:
            for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(chunk)
        if digest.hexdigest().upper() != expected.upper():
            raise ValueError(f"event-bounded Pose gap source SHA mismatch: {path}")
