from __future__ import annotations

import json
import math
from collections import Counter
from collections.abc import Iterable, Mapping
from typing import Any

from rallymate_features.event_features import FEATURE_DEFINITIONS
from rallymate_features.validity import DEFAULT_KEYPOINT_CONFIDENCE_MIN


ROUTER_VERSION = "required-joint-validity-superset-v1.0.0"
EXPERIMENT_VERSION = "pose-observability-router-experiment-v1.0.0"
NON_POSE_SCORING_FEATURES = frozenset({"target_direction_alignment_error_deg"})


def pose_required_joints_from_registry(
    registry: Mapping[str, Any],
) -> tuple[str, ...]:
    """Return the authoritative Pose joints used by the registry feature set.

    Features that require externally supplied semantics are excluded explicitly;
    any other unknown feature fails closed so the router cannot silently ignore a
    newly added Pose dependency.
    """

    indicators = registry.get("indicators")
    if not isinstance(indicators, list) or not indicators:
        raise ValueError("feasibility registry must contain indicators")
    feature_names: set[str] = set()
    for indicator in indicators:
        required = indicator.get("required_features") if isinstance(indicator, Mapping) else None
        if not isinstance(required, list) or not required:
            raise ValueError("every indicator must declare required_features")
        for feature_name in required:
            if not isinstance(feature_name, str) or not feature_name:
                raise ValueError("required feature names must be non-empty strings")
            feature_names.add(feature_name)
    unknown = feature_names - set(FEATURE_DEFINITIONS) - NON_POSE_SCORING_FEATURES
    if unknown:
        raise ValueError(
            "router cannot ignore unknown feature dependencies: "
            + ",".join(sorted(unknown))
        )
    joints = {
        joint
        for feature_name in feature_names & set(FEATURE_DEFINITIONS)
        for joint in FEATURE_DEFINITIONS[feature_name]["required_joints"]
    }
    if not joints:
        raise ValueError("registry does not expose any Pose joint dependencies")
    return tuple(sorted(joints))


def _canonical(value: Any) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )


def _selected_pose(row: Mapping[str, Any], track_id: int) -> Mapping[str, Any] | None:
    matches = [
        pose
        for pose in row.get("poses", [])
        if isinstance(pose, Mapping) and pose.get("person_track_id") == track_id
    ]
    if len(matches) > 1:
        raise ValueError("frame contains duplicate poses for selected Track")
    return matches[0] if matches else None


def _point_valid(point: Mapping[str, Any] | None, confidence_min: float) -> bool:
    if point is None or point.get("in_frame") is False:
        return False
    try:
        return bool(
            math.isfinite(float(point["x_normalized"]))
            and math.isfinite(float(point["y_normalized"]))
            and math.isfinite(float(point["confidence"]))
            and float(point["confidence"]) >= confidence_min
        )
    except (KeyError, TypeError, ValueError, OverflowError):
        return False


def _valid_joint_set(
    pose: Mapping[str, Any] | None,
    required_joints: tuple[str, ...],
    confidence_min: float,
) -> set[str]:
    points = {
        point.get("name"): point
        for point in (pose or {}).get("keypoints", [])
        if isinstance(point, Mapping) and isinstance(point.get("name"), str)
    }
    return {
        joint
        for joint in required_joints
        if _point_valid(points.get(joint), confidence_min)
    }


def select_required_joint_superset_frames(
    *,
    baseline_rows: list[dict[str, Any]],
    candidate_rows: list[dict[str, Any]],
    primary_timeline: list[dict[str, Any]],
    target_processed_indexes: Iterable[int],
    required_joints: Iterable[str],
    confidence_min: float = DEFAULT_KEYPOINT_CONFIDENCE_MIN,
) -> tuple[set[int], dict[str, Any]]:
    """Select a candidate crop only when joint validity strictly dominates.

    The router does not read feature values, event outputs, grades, or action
    performance.  A candidate must retain every currently valid required joint
    and add at least one previously invalid required joint on the selected Track.
    Equal-validity alternatives remain on the baseline crop.
    """

    joints = tuple(sorted(set(required_joints)))
    if not joints or any(not isinstance(joint, str) or not joint for joint in joints):
        raise ValueError("required_joints must be non-empty strings")
    try:
        threshold = float(confidence_min)
    except (TypeError, ValueError, OverflowError) as exc:
        raise ValueError("confidence_min must be finite and within [0, 1]") from exc
    if not math.isfinite(threshold) or not 0.0 <= threshold <= 1.0:
        raise ValueError("confidence_min must be finite and within [0, 1]")
    if not baseline_rows or len(baseline_rows) != len(candidate_rows):
        raise ValueError("baseline and candidate frames must be non-empty and aligned")
    if len(primary_timeline) != len(baseline_rows):
        raise ValueError("primary timeline must align with frame rows")

    baseline = {
        int(row["frame"]["processed_index"]): row for row in baseline_rows
    }
    candidate = {
        int(row["frame"]["processed_index"]): row for row in candidate_rows
    }
    timeline = {
        int(row["processed_index"]): row for row in primary_timeline
    }
    if (
        len(baseline) != len(baseline_rows)
        or len(candidate) != len(candidate_rows)
        or len(timeline) != len(primary_timeline)
        or set(baseline) != set(candidate)
        or set(baseline) != set(timeline)
    ):
        raise ValueError("frame/timeline processed indexes are not one-to-one aligned")
    targets = {int(index) for index in target_processed_indexes}
    if not targets or not targets <= set(baseline):
        raise ValueError("target indexes must be a non-empty subset of frame indexes")

    selected: set[int] = set()
    reasons: Counter[str] = Counter()
    added_joint_counts: Counter[str] = Counter()
    baseline_valid_total = candidate_valid_total = 0
    for index in sorted(targets):
        baseline_row = baseline[index]
        candidate_row = candidate[index]
        baseline_non_pose = {
            key: value for key, value in baseline_row.items() if key != "poses"
        }
        candidate_non_pose = {
            key: value for key, value in candidate_row.items() if key != "poses"
        }
        if _canonical(baseline_non_pose) != _canonical(candidate_non_pose):
            raise ValueError("candidate changed non-Pose frame data")
        track_id = timeline[index].get("source_track_id")
        if not isinstance(track_id, int) or track_id < 1:
            reasons["selected_track_unavailable"] += 1
            continue
        baseline_pose = _selected_pose(baseline_row, track_id)
        candidate_pose = _selected_pose(candidate_row, track_id)
        if baseline_pose is None:
            reasons["baseline_pose_unavailable_outside_router_scope"] += 1
            continue
        if candidate_pose is None:
            reasons["candidate_pose_unavailable"] += 1
            continue
        if baseline_pose.get("keypoint_format") != candidate_pose.get("keypoint_format"):
            raise ValueError("candidate keypoint format differs from baseline")
        baseline_other = [
            pose
            for pose in baseline_row.get("poses", [])
            if pose.get("person_track_id") != track_id
        ]
        candidate_other = [
            pose
            for pose in candidate_row.get("poses", [])
            if pose.get("person_track_id") != track_id
        ]
        if _canonical(baseline_other) != _canonical(candidate_other):
            raise ValueError("candidate changed non-selected poses")

        baseline_valid = _valid_joint_set(baseline_pose, joints, threshold)
        candidate_valid = _valid_joint_set(candidate_pose, joints, threshold)
        baseline_valid_total += len(baseline_valid)
        candidate_valid_total += len(candidate_valid)
        lost = baseline_valid - candidate_valid
        added = candidate_valid - baseline_valid
        if lost:
            reasons["required_joint_validity_regression"] += 1
            continue
        if not added:
            reasons["no_additional_required_joint"] += 1
            continue
        selected.add(index)
        reasons["selected_strict_validity_superset"] += 1
        added_joint_counts.update(added)

    return selected, {
        "router_version": ROUTER_VERSION,
        "selection_policy": "candidate_required_joint_valid_set_strictly_contains_baseline",
        "confidence_min": threshold,
        "required_joints": list(joints),
        "target_frame_count": len(targets),
        "selected_frame_count": len(selected),
        "rejected_frame_count": len(targets) - len(selected),
        "decision_reason_counts": dict(sorted(reasons.items())),
        "added_valid_joint_counts": dict(sorted(added_joint_counts.items())),
        "baseline_valid_required_joint_observation_count": baseline_valid_total,
        "candidate_valid_required_joint_observation_count": candidate_valid_total,
        "selection_uses_feature_values": False,
        "selection_uses_event_outcomes": False,
        "selection_uses_grades_or_thresholds": False,
        "equal_validity_keeps_baseline": True,
        "required_joint_validity_regression_allowed": False,
    }


def select_required_joint_dominating_candidates(
    *,
    baseline_rows: list[dict[str, Any]],
    candidate_rows_by_name: Mapping[str, list[dict[str, Any]]],
    primary_timeline: list[dict[str, Any]],
    target_processed_indexes: Iterable[int],
    required_joints: Iterable[str],
    confidence_min: float = DEFAULT_KEYPOINT_CONFIDENCE_MIN,
) -> tuple[dict[int, str], dict[str, Any]]:
    """Choose a unique joint-validity-dominating candidate per frame.

    Each eligible candidate must strictly contain the baseline valid required
    joint set.  Among eligible candidates, the largest valid set wins.  If two
    top candidates expose the same valid set but different Pose coordinates,
    the frame stays on the baseline so crop/profile preference cannot silently
    become an accuracy claim.
    """

    if not candidate_rows_by_name:
        raise ValueError("at least one named candidate is required")
    names = tuple(sorted(candidate_rows_by_name))
    if any(not isinstance(name, str) or not name for name in names):
        raise ValueError("candidate names must be non-empty strings")
    if not baseline_rows:
        raise ValueError("baseline frames must be non-empty")
    joints = tuple(sorted(set(required_joints)))
    if not joints or any(not isinstance(joint, str) or not joint for joint in joints):
        raise ValueError("required_joints must be non-empty strings")
    try:
        threshold = float(confidence_min)
    except (TypeError, ValueError, OverflowError) as exc:
        raise ValueError("confidence_min must be finite and within [0, 1]") from exc
    if not math.isfinite(threshold) or not 0.0 <= threshold <= 1.0:
        raise ValueError("confidence_min must be finite and within [0, 1]")

    baseline = {int(row["frame"]["processed_index"]): row for row in baseline_rows}
    timeline = {int(row["processed_index"]): row for row in primary_timeline}
    candidates = {
        name: {int(row["frame"]["processed_index"]): row for row in rows}
        for name, rows in candidate_rows_by_name.items()
    }
    if (
        len(baseline) != len(baseline_rows)
        or len(timeline) != len(primary_timeline)
        or set(baseline) != set(timeline)
        or any(
            len(rows) != len(candidate_rows_by_name[name])
            or set(rows) != set(baseline)
            for name, rows in candidates.items()
        )
    ):
        raise ValueError("baseline, candidates, and timeline must align one-to-one")
    targets = {int(index) for index in target_processed_indexes}
    if not targets or not targets <= set(baseline):
        raise ValueError("target indexes must be a non-empty subset of frames")

    selected: dict[int, str] = {}
    reasons: Counter[str] = Counter()
    selected_by_candidate: Counter[str] = Counter()
    added_joint_counts: Counter[str] = Counter()
    candidate_eligible_counts: Counter[str] = Counter()
    for index in sorted(targets):
        base = baseline[index]
        track_id = timeline[index].get("source_track_id")
        if not isinstance(track_id, int) or track_id < 1:
            reasons["selected_track_unavailable"] += 1
            continue
        base_pose = _selected_pose(base, track_id)
        if base_pose is None:
            reasons["baseline_pose_unavailable_outside_router_scope"] += 1
            continue
        base_non_pose = {key: value for key, value in base.items() if key != "poses"}
        base_other = [
            pose for pose in base.get("poses", [])
            if pose.get("person_track_id") != track_id
        ]
        base_valid = _valid_joint_set(base_pose, joints, threshold)
        eligible: list[tuple[str, Mapping[str, Any], set[str]]] = []
        for name in names:
            row = candidates[name][index]
            if _canonical({key: value for key, value in row.items() if key != "poses"}) != _canonical(base_non_pose):
                raise ValueError("candidate changed non-Pose frame data")
            other = [
                pose for pose in row.get("poses", [])
                if pose.get("person_track_id") != track_id
            ]
            if _canonical(other) != _canonical(base_other):
                raise ValueError("candidate changed non-selected poses")
            pose = _selected_pose(row, track_id)
            if pose is None:
                continue
            if pose.get("keypoint_format") != base_pose.get("keypoint_format"):
                raise ValueError("candidate keypoint format differs from baseline")
            valid = _valid_joint_set(pose, joints, threshold)
            if base_valid <= valid and base_valid != valid:
                eligible.append((name, pose, valid))
                candidate_eligible_counts[name] += 1
        if not eligible:
            reasons["no_strict_validity_superset_candidate"] += 1
            continue
        max_count = max(len(item[2]) for item in eligible)
        leaders = [item for item in eligible if len(item[2]) == max_count]
        leader_pose_values = {_canonical(item[1]) for item in leaders}
        if len(leader_pose_values) != 1:
            reasons["ambiguous_equal_valid_joint_count"] += 1
            continue
        name, _, valid = leaders[0]
        selected[index] = name
        selected_by_candidate[name] += 1
        added_joint_counts.update(valid - base_valid)
        reasons["selected_unique_maximum_validity_superset"] += 1

    return selected, {
        "router_version": "required-joint-multicandidate-dominance-v1.0.0",
        "selection_policy": (
            "unique_maximum_required_joint_validity_strict_superset_else_baseline"
        ),
        "confidence_min": threshold,
        "required_joints": list(joints),
        "candidate_names": list(names),
        "target_frame_count": len(targets),
        "selected_frame_count": len(selected),
        "rejected_frame_count": len(targets) - len(selected),
        "selected_frame_count_by_candidate": dict(sorted(selected_by_candidate.items())),
        "eligible_frame_count_by_candidate": dict(sorted(candidate_eligible_counts.items())),
        "decision_reason_counts": dict(sorted(reasons.items())),
        "added_valid_joint_counts": dict(sorted(added_joint_counts.items())),
        "selection_uses_feature_values": False,
        "selection_uses_event_outcomes": False,
        "selection_uses_grades_or_thresholds": False,
        "equal_validity_different_pose_keeps_baseline": True,
        "required_joint_validity_regression_allowed": False,
    }


def validate_pose_observability_router_report(report: Mapping[str, Any]) -> None:
    if report.get("experiment_version") != EXPERIMENT_VERSION:
        raise ValueError("unsupported Pose observability router report")
    status = report.get("status")
    if status not in {
        "experimental_regression_free_observability_candidate_not_production",
        "experimental_router_rejected_due_to_regression",
    }:
        raise ValueError("unsafe Pose observability router status")
    audit = report.get("router_audit")
    if not isinstance(audit, Mapping) or audit.get("router_version") != ROUTER_VERSION:
        raise ValueError("router audit version is missing or stale")
    for field, expected in {
        "selection_uses_feature_values": False,
        "selection_uses_event_outcomes": False,
        "selection_uses_grades_or_thresholds": False,
        "equal_validity_keeps_baseline": True,
        "required_joint_validity_regression_allowed": False,
    }.items():
        if audit.get(field) is not expected:
            raise ValueError(f"unsafe router audit field: {field}")
    selected = audit.get("selected_frame_count")
    rejected = audit.get("rejected_frame_count")
    targets = audit.get("target_frame_count")
    if (
        not all(isinstance(value, int) and value >= 0 for value in (selected, rejected, targets))
        or selected + rejected != targets
    ):
        raise ValueError("router target accounting is inconsistent")
    vector = report.get("feature_vector_impact", {})
    operational = report.get("operational_measurement_impact", {})
    regressions = int(vector.get("regressed_indicator_instance_count", -1)) + int(
        operational.get("regressed_indicator_instance_count", -1)
    )
    if regressions < 0:
        raise ValueError("router regression accounting is missing")
    expected_status = (
        "experimental_regression_free_observability_candidate_not_production"
        if regressions == 0
        else "experimental_router_rejected_due_to_regression"
    )
    if status != expected_status:
        raise ValueError("router status does not match measured regressions")
    decision = report.get("decision", {})
    if decision.get("production_default_changed") is not False or decision.get(
        "candidate_promoted"
    ) is not False:
        raise ValueError("experimental router cannot change production")
    safety = report.get("safety", {})
    for field in (
        "accuracy_claim",
        "ground_truth_provided",
        "production_enabled",
        "automatic_fallback_enabled",
        "measurement_gate_modified",
        "scoring_gate_modified",
        "event_boundaries_modified",
        "grades_generated",
        "thresholds_generated",
        "maturity_promoted",
    ):
        if safety.get(field) is not False:
            raise ValueError(f"unsafe router report claim: {field}")


__all__ = [
    "EXPERIMENT_VERSION",
    "NON_POSE_SCORING_FEATURES",
    "ROUTER_VERSION",
    "pose_required_joints_from_registry",
    "select_required_joint_dominating_candidates",
    "select_required_joint_superset_frames",
    "validate_pose_observability_router_report",
]
