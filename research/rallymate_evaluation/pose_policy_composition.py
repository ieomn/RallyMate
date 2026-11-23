from __future__ import annotations

import copy
import json
import math
from collections import Counter
from typing import Any

from rallymate_features.validity import DEFAULT_KEYPOINT_CONFIDENCE_MIN


def _pose_for_track(row: dict[str, Any], track_id: int) -> dict[str, Any] | None:
    matches = [
        pose
        for pose in row.get("poses", [])
        if pose.get("person_track_id") == track_id
    ]
    if len(matches) > 1:
        raise ValueError("frame contains duplicate poses for the selected track")
    return matches[0] if matches else None


def _canonical(value: Any) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )


def _valid_keypoint(point: Any, confidence_min: float) -> bool:
    if not isinstance(point, dict) or point.get("in_frame") is False:
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


def _keypoints_by_name(pose: dict[str, Any], label: str) -> dict[str, dict[str, Any]]:
    points = pose.get("keypoints")
    if not isinstance(points, list) or not points:
        raise ValueError(f"{label} pose must contain keypoints")
    by_name: dict[str, dict[str, Any]] = {}
    for point in points:
        if not isinstance(point, dict) or not isinstance(point.get("name"), str):
            raise ValueError(f"{label} pose contains an invalid keypoint")
        name = str(point["name"])
        if not name or name in by_name:
            raise ValueError(f"{label} pose contains duplicate/empty keypoint names")
        by_name[name] = point
    return by_name


def add_missing_keypoints_from_same_topology_candidate_rows(
    *,
    baseline_rows: list[dict[str, Any]],
    candidate_rows: list[dict[str, Any]],
    timeline_rows: list[dict[str, Any]],
    target_processed_indexes: set[int],
    allowed_joint_names: set[str],
    confidence_min: float = DEFAULT_KEYPOINT_CONFIDENCE_MIN,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Fill only baseline-invalid joints from a same-topology candidate.

    This is an observability experiment, not a coordinate ensemble.  Every
    baseline-valid keypoint remains byte-identical.  A candidate point may be
    copied only when the baseline point is invalid and both poses expose the
    same ordered keypoint topology on the selected Track.  No feature value,
    event outcome, grade, or threshold participates in the decision.
    """

    if not baseline_rows or len(baseline_rows) != len(candidate_rows):
        raise ValueError("baseline and candidate rows must be non-empty and aligned")
    if len(timeline_rows) != len(baseline_rows):
        raise ValueError("timeline rows must align with frame rows")
    try:
        threshold = float(confidence_min)
    except (TypeError, ValueError, OverflowError) as exc:
        raise ValueError("confidence_min must be finite and within [0, 1]") from exc
    if not math.isfinite(threshold) or not 0.0 <= threshold <= 1.0:
        raise ValueError("confidence_min must be finite and within [0, 1]")
    allowed = {str(name) for name in allowed_joint_names}
    if not allowed or any(not name for name in allowed):
        raise ValueError("allowed_joint_names must be non-empty strings")

    baseline = {int(row["frame"]["processed_index"]): row for row in baseline_rows}
    candidate = {int(row["frame"]["processed_index"]): row for row in candidate_rows}
    timeline = {int(row["processed_index"]): row for row in timeline_rows}
    if (
        len(baseline) != len(baseline_rows)
        or len(candidate) != len(candidate_rows)
        or len(timeline) != len(timeline_rows)
        or set(baseline) != set(candidate)
        or set(baseline) != set(timeline)
    ):
        raise ValueError("frame/timeline processed indexes must align one-to-one")
    targets = {int(index) for index in target_processed_indexes}
    if not targets or not targets <= set(baseline):
        raise ValueError("target indexes must be a non-empty subset of frame indexes")

    rows: list[dict[str, Any]] = []
    reasons: Counter[str] = Counter()
    additions: Counter[str] = Counter()
    changed_frames = 0
    baseline_valid_preserved = 0
    for baseline_row in baseline_rows:
        index = int(baseline_row["frame"]["processed_index"])
        if index not in targets:
            rows.append(baseline_row)
            continue
        candidate_row = candidate[index]
        baseline_non_pose = {k: v for k, v in baseline_row.items() if k != "poses"}
        candidate_non_pose = {k: v for k, v in candidate_row.items() if k != "poses"}
        if _canonical(baseline_non_pose) != _canonical(candidate_non_pose):
            raise ValueError("candidate changed non-Pose frame data")
        track_id = timeline[index].get("source_track_id")
        if not isinstance(track_id, int) or track_id < 1:
            reasons["selected_track_unavailable"] += 1
            rows.append(baseline_row)
            continue
        baseline_pose = _pose_for_track(baseline_row, track_id)
        candidate_pose = _pose_for_track(candidate_row, track_id)
        if baseline_pose is None or candidate_pose is None:
            reasons["baseline_or_candidate_pose_unavailable"] += 1
            rows.append(baseline_row)
            continue
        if baseline_pose.get("keypoint_format") != candidate_pose.get("keypoint_format"):
            raise ValueError("candidate keypoint format differs from baseline")
        baseline_other = [
            pose for pose in baseline_row.get("poses", [])
            if pose.get("person_track_id") != track_id
        ]
        candidate_other = [
            pose for pose in candidate_row.get("poses", [])
            if pose.get("person_track_id") != track_id
        ]
        if _canonical(baseline_other) != _canonical(candidate_other):
            raise ValueError("candidate changed non-selected poses")
        base_points = _keypoints_by_name(baseline_pose, "baseline")
        cand_points = _keypoints_by_name(candidate_pose, "candidate")
        base_order = [str(point["name"]) for point in baseline_pose["keypoints"]]
        cand_order = [str(point["name"]) for point in candidate_pose["keypoints"]]
        if base_order != cand_order or set(base_points) != set(cand_points):
            raise ValueError("candidate keypoint topology/order differs from baseline")
        unknown = allowed - set(base_points)
        if unknown:
            raise ValueError("allowed joints are absent from the shared topology")

        fused_pose = copy.deepcopy(baseline_pose)
        fused_points = _keypoints_by_name(fused_pose, "fused")
        frame_additions = 0
        for name in sorted(allowed):
            base_point = base_points[name]
            cand_point = cand_points[name]
            if _valid_keypoint(base_point, threshold):
                baseline_valid_preserved += 1
                if _canonical(fused_points[name]) != _canonical(base_point):
                    raise AssertionError("baseline-valid keypoint changed during fusion")
                continue
            if _valid_keypoint(cand_point, threshold):
                replacement = copy.deepcopy(cand_point)
                point_index = base_order.index(name)
                fused_pose["keypoints"][point_index] = replacement
                additions[name] += 1
                frame_additions += 1
        if frame_additions == 0:
            reasons["no_missing_allowed_joint_recovered"] += 1
            rows.append(baseline_row)
            continue
        reasons["filled_one_or_more_missing_allowed_joints"] += 1
        changed_frames += 1
        row = copy.deepcopy(baseline_row)
        row["poses"] = baseline_other + [fused_pose]
        rows.append(row)

    return rows, {
        "fusion_version": "same-topology-missing-keypoint-addition-v1.0.0",
        "selection_policy": (
            "copy_candidate_point_only_when_baseline_invalid_and_candidate_valid"
        ),
        "confidence_min": threshold,
        "allowed_joint_names": sorted(allowed),
        "target_frame_count": len(targets),
        "changed_frame_count": changed_frames,
        "unchanged_target_frame_count": len(targets) - changed_frames,
        "added_valid_joint_observation_count": sum(additions.values()),
        "added_valid_joint_counts": dict(sorted(additions.items())),
        "decision_reason_counts": dict(sorted(reasons.items())),
        "baseline_valid_joint_observations_preserved": baseline_valid_preserved,
        "baseline_valid_coordinates_overwritten": 0,
        "non_target_frames_preserved": True,
        "non_pose_frame_data_preserved": True,
        "non_selected_poses_preserved": True,
        "same_topology_required": True,
        "selection_uses_feature_values": False,
        "selection_uses_event_outcomes": False,
        "selection_uses_grades_or_thresholds": False,
    }


def add_missing_keypoints_from_mapped_topology_candidate_rows(
    *,
    baseline_rows: list[dict[str, Any]],
    candidate_rows: list[dict[str, Any]],
    timeline_rows: list[dict[str, Any]],
    target_processed_indexes: set[int],
    target_to_candidate_joint_names: dict[str, str],
    baseline_keypoint_format: str,
    candidate_keypoint_format: str,
    confidence_min: float = DEFAULT_KEYPOINT_CONFIDENCE_MIN,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Fill baseline-invalid points through an explicit semantic topology map.

    Unlike a whole-pose adapter, this function never changes the baseline
    topology.  A valid candidate point may supply coordinates only for one
    explicitly registered baseline point.  The baseline point identity
    (index, name and downstream joint ID) is retained and every baseline-valid
    point remains byte-identical.  This is an observability experiment; it does
    not establish cross-model coordinate accuracy.
    """

    if not baseline_rows or len(baseline_rows) != len(candidate_rows):
        raise ValueError("baseline and candidate rows must be non-empty and aligned")
    if len(timeline_rows) != len(baseline_rows):
        raise ValueError("timeline rows must align with frame rows")
    if (
        not isinstance(baseline_keypoint_format, str)
        or not baseline_keypoint_format
        or not isinstance(candidate_keypoint_format, str)
        or not candidate_keypoint_format
        or baseline_keypoint_format == candidate_keypoint_format
    ):
        raise ValueError("mapped fusion requires two distinct keypoint formats")
    try:
        threshold = float(confidence_min)
    except (TypeError, ValueError, OverflowError) as exc:
        raise ValueError("confidence_min must be finite and within [0, 1]") from exc
    if not math.isfinite(threshold) or not 0.0 <= threshold <= 1.0:
        raise ValueError("confidence_min must be finite and within [0, 1]")
    if not isinstance(target_to_candidate_joint_names, dict) or not target_to_candidate_joint_names:
        raise ValueError("target_to_candidate_joint_names must be non-empty")
    mapping = {
        str(target): str(source)
        for target, source in target_to_candidate_joint_names.items()
    }
    if any(not target or not source for target, source in mapping.items()):
        raise ValueError("joint mapping names must be non-empty strings")
    if len(set(mapping.values())) != len(mapping):
        raise ValueError("candidate joint mapping must be one-to-one")

    baseline = {int(row["frame"]["processed_index"]): row for row in baseline_rows}
    candidate = {int(row["frame"]["processed_index"]): row for row in candidate_rows}
    timeline = {int(row["processed_index"]): row for row in timeline_rows}
    if (
        len(baseline) != len(baseline_rows)
        or len(candidate) != len(candidate_rows)
        or len(timeline) != len(timeline_rows)
        or set(baseline) != set(candidate)
        or set(baseline) != set(timeline)
    ):
        raise ValueError("frame/timeline processed indexes must align one-to-one")
    targets = {int(index) for index in target_processed_indexes}
    if not targets or not targets <= set(baseline):
        raise ValueError("target indexes must be a non-empty subset of frame indexes")

    rows: list[dict[str, Any]] = []
    reasons: Counter[str] = Counter()
    additions: Counter[str] = Counter()
    source_additions: Counter[str] = Counter()
    changed_frames = 0
    baseline_valid_preserved = 0
    for baseline_row in baseline_rows:
        index = int(baseline_row["frame"]["processed_index"])
        if index not in targets:
            rows.append(baseline_row)
            continue
        candidate_row = candidate[index]
        baseline_non_pose = {k: v for k, v in baseline_row.items() if k != "poses"}
        candidate_non_pose = {k: v for k, v in candidate_row.items() if k != "poses"}
        if _canonical(baseline_non_pose) != _canonical(candidate_non_pose):
            raise ValueError("candidate changed non-Pose frame data")
        track_id = timeline[index].get("source_track_id")
        if not isinstance(track_id, int) or track_id < 1:
            reasons["selected_track_unavailable"] += 1
            rows.append(baseline_row)
            continue
        baseline_pose = _pose_for_track(baseline_row, track_id)
        candidate_pose = _pose_for_track(candidate_row, track_id)
        if baseline_pose is None or candidate_pose is None:
            reasons["baseline_or_candidate_pose_unavailable"] += 1
            rows.append(baseline_row)
            continue
        if baseline_pose.get("keypoint_format") != baseline_keypoint_format:
            raise ValueError("baseline keypoint format differs from the registered map")
        if candidate_pose.get("keypoint_format") != candidate_keypoint_format:
            raise ValueError("candidate keypoint format differs from the registered map")
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
        base_points = _keypoints_by_name(baseline_pose, "baseline")
        cand_points = _keypoints_by_name(candidate_pose, "candidate")
        missing_targets = set(mapping) - set(base_points)
        missing_sources = set(mapping.values()) - set(cand_points)
        if missing_targets or missing_sources:
            raise ValueError("registered joint map is absent from a Pose topology")

        fused_pose = copy.deepcopy(baseline_pose)
        fused_points = _keypoints_by_name(fused_pose, "fused")
        base_order = [str(point["name"]) for point in baseline_pose["keypoints"]]
        frame_additions = 0
        for target_name, source_name in sorted(mapping.items()):
            base_point = base_points[target_name]
            candidate_point = cand_points[source_name]
            if _valid_keypoint(base_point, threshold):
                baseline_valid_preserved += 1
                if _canonical(fused_points[target_name]) != _canonical(base_point):
                    raise AssertionError("baseline-valid keypoint changed during mapped fusion")
                continue
            if not _valid_keypoint(candidate_point, threshold):
                continue
            replacement = copy.deepcopy(candidate_point)
            replacement["index"] = base_point.get("index")
            replacement["name"] = base_point.get("name")
            replacement["downstream_joint_id"] = base_point.get(
                "downstream_joint_id"
            )
            point_index = base_order.index(target_name)
            fused_pose["keypoints"][point_index] = replacement
            additions[target_name] += 1
            source_additions[source_name] += 1
            frame_additions += 1
        if frame_additions == 0:
            reasons["no_missing_mapped_joint_recovered"] += 1
            rows.append(baseline_row)
            continue
        reasons["filled_one_or_more_missing_mapped_joints"] += 1
        changed_frames += 1
        row = copy.deepcopy(baseline_row)
        row["poses"] = baseline_other + [fused_pose]
        rows.append(row)

    return rows, {
        "fusion_version": "mapped-topology-missing-keypoint-addition-v1.0.0",
        "selection_policy": (
            "copy_explicitly_mapped_candidate_point_only_when_baseline_invalid_and_candidate_valid"
        ),
        "confidence_min": threshold,
        "baseline_keypoint_format": baseline_keypoint_format,
        "candidate_keypoint_format": candidate_keypoint_format,
        "target_to_candidate_joint_names": dict(sorted(mapping.items())),
        "mapping_is_one_to_one": True,
        "target_frame_count": len(targets),
        "changed_frame_count": changed_frames,
        "unchanged_target_frame_count": len(targets) - changed_frames,
        "added_valid_joint_observation_count": sum(additions.values()),
        "added_valid_joint_counts": dict(sorted(additions.items())),
        "candidate_source_joint_counts": dict(sorted(source_additions.items())),
        "decision_reason_counts": dict(sorted(reasons.items())),
        "baseline_valid_joint_observations_preserved": baseline_valid_preserved,
        "baseline_valid_coordinates_overwritten": 0,
        "baseline_topology_preserved": True,
        "non_target_frames_preserved": True,
        "non_pose_frame_data_preserved": True,
        "non_selected_poses_preserved": True,
        "explicit_topology_map_required": True,
        "selection_uses_feature_values": False,
        "selection_uses_event_outcomes": False,
        "selection_uses_grades_or_thresholds": False,
    }


def compose_disjoint_pose_policy_rows(
    *,
    baseline_rows: list[dict[str, Any]],
    timeline_rows: list[dict[str, Any]],
    experiments: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Compose same-model experimental Pose rows on disjoint frame indexes.

    Each experiment must supply ``name``, full-length ``frame_rows`` and a
    non-empty ``target_processed_indexes`` set.  Only the selected Track pose
    may differ at a target.  All non-target rows and every non-Pose frame field
    must remain byte-equivalent under canonical JSON.  The helper rejects
    overlapping targets instead of silently choosing an experiment.
    """

    if not baseline_rows or len(baseline_rows) != len(timeline_rows):
        raise ValueError("baseline frames and timeline must be non-empty and aligned")
    baseline_by_index = {
        int(row["frame"]["processed_index"]): row for row in baseline_rows
    }
    timeline_by_index = {
        int(row["processed_index"]): row for row in timeline_rows
    }
    if len(baseline_by_index) != len(baseline_rows):
        raise ValueError("baseline frames contain duplicate processed indexes")
    if set(timeline_by_index) != set(baseline_by_index):
        raise ValueError("timeline indexes do not match baseline frames")

    owners: dict[int, str] = {}
    experimental_rows_by_name: dict[str, dict[int, dict[str, Any]]] = {}
    targets_by_name: dict[str, set[int]] = {}
    for experiment in experiments:
        name = experiment.get("name")
        rows = experiment.get("frame_rows")
        raw_targets = experiment.get("target_processed_indexes")
        if not isinstance(name, str) or not name:
            raise ValueError("experiment name must be non-empty")
        if not isinstance(rows, list) or len(rows) != len(baseline_rows):
            raise ValueError(f"experiment {name} does not align with baseline rows")
        targets = {int(value) for value in raw_targets or []}
        if not targets:
            raise ValueError(f"experiment {name} has no target indexes")
        unknown = targets - set(baseline_by_index)
        if unknown:
            raise ValueError(f"experiment {name} targets unknown frames")
        overlap = targets & set(owners)
        if overlap:
            raise ValueError(
                f"pose policy targets overlap at processed index {min(overlap)}"
            )
        for index in targets:
            owners[index] = name
        indexed = {int(row["frame"]["processed_index"]): row for row in rows}
        if set(indexed) != set(baseline_by_index):
            raise ValueError(f"experiment {name} frame indexes differ from baseline")
        experimental_rows_by_name[name] = indexed
        targets_by_name[name] = targets

    combined: list[dict[str, Any]] = []
    changed_counts: dict[str, int] = {name: 0 for name in experimental_rows_by_name}
    for baseline in baseline_rows:
        index = int(baseline["frame"]["processed_index"])
        owner = owners.get(index)
        if owner is None:
            combined.append(baseline)
            continue
        source = experimental_rows_by_name[owner][index]
        baseline_without_pose = {key: value for key, value in baseline.items() if key != "poses"}
        source_without_pose = {key: value for key, value in source.items() if key != "poses"}
        if _canonical(baseline_without_pose) != _canonical(source_without_pose):
            raise ValueError(f"experiment {owner} changed non-Pose frame data")
        track_id = timeline_by_index[index].get("source_track_id")
        if not isinstance(track_id, int) or track_id < 1:
            raise ValueError(f"target frame {index} has no selected source track")
        baseline_pose = _pose_for_track(baseline, track_id)
        source_pose = _pose_for_track(source, track_id)
        baseline_other = [
            pose
            for pose in baseline.get("poses", [])
            if pose.get("person_track_id") != track_id
        ]
        source_other = [
            pose
            for pose in source.get("poses", [])
            if pose.get("person_track_id") != track_id
        ]
        if _canonical(baseline_other) != _canonical(source_other):
            raise ValueError(f"experiment {owner} changed a non-selected pose")
        row = dict(baseline)
        row["poses"] = list(baseline_other)
        if source_pose is not None:
            row["poses"].append(source_pose)
        combined.append(row)
        if _canonical(baseline_pose) != _canonical(source_pose):
            changed_counts[owner] += 1

    return combined, {
        "experiment_count": len(experiments),
        "target_frame_count": len(owners),
        "target_sets_disjoint": True,
        "target_frame_count_by_experiment": {
            name: len(targets) for name, targets in sorted(targets_by_name.items())
        },
        "changed_selected_pose_frame_count_by_experiment": dict(
            sorted(changed_counts.items())
        ),
        "non_target_frame_count": len(baseline_rows) - len(owners),
        "non_pose_frame_data_preserved": True,
        "non_selected_poses_preserved": True,
    }


__all__ = [
    "add_missing_keypoints_from_mapped_topology_candidate_rows",
    "add_missing_keypoints_from_same_topology_candidate_rows",
    "compose_disjoint_pose_policy_rows",
]
