"""Conservative 2D low/move/low evidence for four manual FS card windows.

All thresholds here are uncalibrated engineering observation policies. A complete
visual motion is neither foot contact nor a verified technical step or direction.
The declared moving foot comes from source-frame review, never fastest-foot choice.
"""
from __future__ import annotations

from collections.abc import Mapping
from copy import deepcopy
import math
import statistics

from .card_kinematics import (
    ANKLES, TORSO, _endpoint, _get, _middle, _point, _reference_point,
    _source_times, finite, validate_window,
)

VERSION = "step-motion-evidence-v1.0.1"
POLICY_VERSION = "single-low-move-low-2d-v1.0.0"
SUPPORTED_CARDS = frozenset(("FS02-M04", "FS07-M02", "FS08-M02", "FS08-M03"))
ANNOTATION_VERSION = "manual-fs-step-motion-v1.0.0"
ANNOTATION_SEMANTICS = "observed_moving_foot_not_front_foot_target_direction_or_contact_truth"
REQUIRED_JOINTS = TORSO + ANKLES
LOW_SPEED_TORSO_S = .5
MIN_PHASE_MS = 80
MIN_PHASE_FRAMES = 3


def supported_step_window(window):
    stage = window.get("stage_code")
    if isinstance(stage, str) and stage.startswith("M"):
        stage = str(window.get("event_code")) + "-" + stage
    return stage in SUPPORTED_CARDS


def _source_geometry(sample, frame_index):
    """Keep the original normalized plane separate from camera-reference points.

    Low finite confidences are retained, so readers can reconstruct the common
    mask. Nonfinite/invalid point or confidence values become null geometry,
    never a missing-confidence exception for monocular model observations.
    """
    raw_points = _get(sample, "points", {}) if sample is not None else {}
    raw_confidence = _get(sample, "point_confidences", {}) if sample is not None else {}
    raw_points = raw_points if isinstance(raw_points, Mapping) else {}
    raw_confidence = raw_confidence if isinstance(raw_confidence, Mapping) else {}
    points, confidence = {}, {}
    for joint in REQUIRED_JOINTS:
        point, value = raw_points.get(joint), raw_confidence.get(joint)
        valid_point = (isinstance(point, (tuple, list)) and len(point) == 2
            and all(finite(coordinate) and abs(coordinate) <= 1e6 for coordinate in point)
            and (value is None or finite(value)))
        points[joint] = list(point) if valid_point else None
        confidence[joint] = value if finite(value) else None
    semantics = _get(sample, "observation_semantics") if sample is not None else None
    return {"frame_index": frame_index, "normalized_points": points,
        "point_confidences": confidence, "observation_semantics": semantics if isinstance(semantics, str) else None}


def validate_step_motion_observation(observation, *, first_frame, last_frame):
    keys = {"version", "method", "moving_foot", "source_description", "source_frames", "semantics"}
    if not isinstance(observation, Mapping) or set(observation) != keys:
        raise ValueError("step_motion_observation_fields_invalid")
    if (observation["version"] != ANNOTATION_VERSION
            or observation["method"] != "manual_visual_observation"
            or observation["semantics"] != ANNOTATION_SEMANTICS
            or not isinstance(observation["moving_foot"], str) or observation["moving_foot"] not in {"left", "right"}
            or not isinstance(observation["source_description"], str)
            or not 1 <= len(observation["source_description"].strip()) <= 512):
        raise ValueError("step_motion_observation_semantics_invalid")
    frames = observation["source_frames"]
    if not isinstance(frames, list) or not frames or len(frames) > last_frame-first_frame+1:
        raise ValueError("step_motion_observation_source_frames_invalid")
    previous = first_frame-1
    for row in frames:
        if (not isinstance(row, Mapping) or set(row) != {"frame_index", "pts", "time_base_num", "time_base_den"}
                or any(type(row[key]) is not int for key in row)
                or not previous < row["frame_index"] <= last_frame or row["pts"] < 0
                or row["time_base_num"] <= 0 or row["time_base_den"] <= 0):
            raise ValueError("step_motion_observation_source_frames_invalid")
        previous = row["frame_index"]
    return deepcopy(observation)


def _phases(path, exact, scale, indices, endpoint_count):
    intervals = []
    for index, (a, b) in enumerate(zip(path, path[1:])):
        dt = exact[index+1]-exact[index]
        speed = math.dist(a, b)*1000/(float(dt)*scale)
        intervals.append({"source_frames": indices[index:index+2], "duration_ms": float(dt),
            "speed_torso_s": speed, "moving": speed > LOW_SPEED_TORSO_S})
    runs = []
    for index, pair in enumerate(intervals):
        if not runs or runs[-1]["moving"] != pair["moving"]:
            runs.append({"moving": pair["moving"], "first_interval": index, "last_interval": index})
        else:
            runs[-1]["last_interval"] = index
    for run in runs:
        a, b = run["first_interval"], run["last_interval"]+1
        run.update(source_frames=indices[a:b+1], duration_ms=float(exact[b]-exact[a]),
            enough_frames=b-a+1 >= MIN_PHASE_FRAMES,
            enough_duration=exact[b]-exact[a] >= MIN_PHASE_MS)
    before, after = _endpoint(path, endpoint_count)
    reasons = []
    layout = len(runs) == 3 and [r["moving"] for r in runs] == [False, True, False]
    radii = None
    if not layout:
        reasons.append("step_motion_requires_single_low_move_low")
    else:
        if not all(r["enough_frames"] and r["enough_duration"] for r in runs):
            reasons.append("step_motion_phase_too_short")
        if (endpoint_count > runs[0]["last_interval"]+2
                or len(path)-endpoint_count < runs[2]["first_interval"]):
            reasons.append("step_motion_endpoint_window_overlaps_movement")
        start_cloud = path[:runs[0]["last_interval"]+2]
        end_cloud = path[runs[2]["first_interval"]:]
        radii = [max(math.dist(p, before) for p in start_cloud)/scale,
            max(math.dist(p, after) for p in end_cloud)/scale]
        if math.dist(before, after)/scale <= sum(radii):
            reasons.append("step_motion_endpoint_positions_not_distinguishable")
    return {"intervals": intervals, "phases": runs, "endpoint_count": endpoint_count,
        "endpoint_medians": {"start": list(before), "end": list(after)},
        "endpoint_low_cloud_radii_torso": radii,
        "net_displacement_torso": math.dist(before, after)/scale,
        "path_length_torso": sum(math.dist(a, b) for a, b in zip(path, path[1:]))/scale,
        "complete_visual_motion_cycle": not reasons, "reason_codes": reasons}


def compute_step_motion_evidence(samples, window, timeline):
    """Recompute every gate from the entire original timeline and raw points."""
    reasons, aligned = validate_window(samples, window, timeline)
    if len(aligned) != len(timeline):
        # Invalid binding/clock checks can return before alignment. Preserve
        # explicit null rows for that entire original timeline as well.
        aligned = [None] * len(timeline)
    indices = [row["frame_index"] for row in timeline]
    result = {"version": VERSION, "policy_version": POLICY_VERSION,
        "policy_basis": "engineering_observation_policy_uncalibrated",
        "status": "unavailable", "complete_visual_motion_cycle": False,
        "source_grade_supported": False, "contact_verified": False,
        "target_direction_verified": False, "technical_step_verified": False,
        "interpolation_used": False, "required_joint_names": list(REQUIRED_JOINTS),
        "total_frame_count": len(timeline), "source_frames": indices,
        "source_pts": [{key: row[key] for key in ("frame_index", "pts", "time_base_num", "time_base_den")
            if key in row} for row in timeline], "reason_codes": list(reasons),
        "low_speed_threshold_torso_s": LOW_SPEED_TORSO_S,
        "minimum_phase_duration_ms": MIN_PHASE_MS, "minimum_phase_frame_count": MIN_PHASE_FRAMES,
        "minimum_geometry_fraction": .7, "geometry_is_visibility": False,
        "denominator": "all_decoder_samples_in_bound_stage", "feet": {}}
    if not supported_step_window(window):
        result["reason_codes"].append("step_motion_card_not_supported")
    observation = window.get("step_motion_observation")
    try:
        observation = validate_step_motion_observation(observation,
            first_frame=indices[0] if indices else 0, last_frame=indices[-1] if indices else 0)
    except (ValueError, TypeError):
        result["reason_codes"].append("complete_step_cycle_not_bound")
        observation = None
    result["moving_foot_observation"] = observation
    common = [row["frame_index"] for row, sample in zip(timeline, aligned)
        if sample is not None and all(_point(sample, joint) is not None for joint in REQUIRED_JOINTS)]
    result["source_geometry_series"] = [_source_geometry(sample, row["frame_index"])
        for row, sample in zip(timeline, aligned)]
    result.update(valid_source_frames=common, valid_frame_count=len(common),
        geometry_gate_passed=len(timeline) >= 3 and len(common)*10 >= len(timeline)*7)
    if len(timeline) < 7:
        result["reason_codes"].append("continuous_endpoint_window_missing")
    if len(common) != len(timeline):
        result["reason_codes"].append("step_motion_common_joints_not_continuous")
    if not window.get("camera_compensated") or type(window.get("camera_reference_epoch")) is not int:
        result["reason_codes"].append("fixed_camera_reference_required")
    if observation:
        by_frame = {row["frame_index"]: (row, sample) for row, sample in zip(timeline, aligned)}
        for binding in observation["source_frames"]:
            row, sample = by_frame.get(binding["frame_index"], ({}, None))
            if any(row.get(key) != value for key, value in binding.items()):
                result["reason_codes"].append("step_motion_observation_pts_mismatch")
            if sample is None or _point(sample, observation["moving_foot"]+"_ankle") is None:
                result["reason_codes"].append("step_motion_observed_ankle_missing")
    points = [{joint: _reference_point(sample, joint) for joint in REQUIRED_JOINTS}
        if sample is not None else {} for sample in aligned]
    if any(any(row.get(joint) is None for joint in REQUIRED_JOINTS) for row in points):
        result["reason_codes"].append("reference_joint_missing_in_continuous_window")
    if result["reason_codes"]:
        result["reason_codes"] = sorted(set(result["reason_codes"]))
        return result
    scales = [math.dist(_middle(*(row[j] for j in TORSO[:2])),
        _middle(*(row[j] for j in TORSO[2:]))) for row in points]
    scale = statistics.median(scales)
    if not finite(scale) or scale <= 0:
        result["reason_codes"] = ["fixed_torso_scale_unavailable"]
        return result
    count = max(3, math.ceil(len(points)*.2))
    exact = _source_times(timeline)
    result.update(fixed_torso_scale=scale, source_torso_scales=scales, endpoint_count=count,
        reference_series=[{"frame_index": index, "points": {joint: list(point) for joint, point in row.items()}}
            for index, row in zip(indices, points)])
    for side in ("left", "right"):
        result["feet"][side] = _phases([row[side+"_ankle"] for row in points], exact, scale, indices, count)
    side = observation["moving_foot"]
    declared, other = result["feet"][side], result["feet"]["right" if side == "left" else "left"]
    result["reason_codes"].extend(declared["reason_codes"])
    if other["complete_visual_motion_cycle"]:
        result["reason_codes"].append("step_motion_competing_ankle_cycle")
    # Both registered formulas take a max across feet. A longer curved path on
    # the declared side must not authorize the other side's larger unbound net
    # displacement (or the converse for the path-ratio denominator).
    if (other["net_displacement_torso"] > declared["net_displacement_torso"]
            or other["path_length_torso"] > declared["path_length_torso"]):
        result["reason_codes"].append("step_motion_declared_side_conflicts_with_paths")
    result["reason_codes"] = sorted(set(result["reason_codes"]))
    result["complete_visual_motion_cycle"] = not result["reason_codes"]
    result["status"] = "measured_visual_motion" if not result["reason_codes"] else "unavailable"
    return result
