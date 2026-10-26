"""Local GS descent measurements for manual review, never a technical grade."""
from __future__ import annotations

import math
import statistics

from .card_kinematics import (
    _get, _point, _reference_point, _source_times, _value,
    measure_card_window, validate_window,
)

VERSION = "gs-preload-evidence-v1.0.0"
ANNOTATION_VERSION = "manual-gs-local-preload-v1.0.0"
ANNOTATION_SEMANTICS = "manual_local_hip_descent_not_confirmed_split_step"
SUPPORTED_CARDS = frozenset({"GS01-M01-03", "GS02-M01-04", "GS03-M01-03", "GS04-M01-03", "GS05-M01-03"})
REQUIRED_JOINTS = ("left_shoulder", "right_shoulder", "left_hip", "right_hip",
                   "left_knee", "right_knee", "left_ankle", "right_ankle")
AUXILIARY_REFERENCES = (
    ("hip_downward_displacement_torso", "torso", .03),
    ("left_knee_flexion_deg.delta", "deg", 4.),
    ("right_knee_flexion_deg.delta", "deg", 4.),
)


def compute_gs_preload_evidence(samples, window, timeline):
    """Use one closed original-frame window, with no filling or re-selection.

    Source annotations and files are verified by the GS adapter. This function
    computes raw geometry only; even a complete descent cannot prove a split
    step, force, centre of mass, balance or the original card's technical grade.
    """
    reasons, aligned = validate_window(samples, window, timeline)
    total = len(timeline)
    if total < 7:
        reasons.append("local_preload_requires_seven_continuous_source_frames")
    if not window.get("camera_compensated"):
        reasons.append("local_preload_fixed_camera_reference_required")
    if any(row.get("selected_person_track_id") != window.get("person_track_id")
           or row.get("selection_epoch") != window.get("selection_epoch")
           or row.get("camera_reference_epoch") != window.get("camera_reference_epoch") for row in timeline):
        reasons.append("local_preload_source_identity_or_reference_changed")
    # Preserve every expected source row even when validate_window cannot align
    # a structurally invalid window. Missing geometry never shrinks the mask.
    if len(aligned) != total:
        by_frame = {_get(sample, "frame_index"): sample for sample in samples}
        aligned = [by_frame.get(row.get("frame_index")) for row in timeline]
    valid_by_joint = {joint: [sample is not None and _point(sample, joint) is not None
                             and _reference_point(sample, joint) is not None for sample in aligned]
                      for joint in REQUIRED_JOINTS}
    jointly_valid = [all(valid_by_joint[joint][i] for joint in REQUIRED_JOINTS) for i in range(total)]
    valid_count = sum(jointly_valid)
    if valid_count != total or not total:
        reasons.append("local_preload_joint_gap_in_continuous_window")
    if not total or valid_count * 10 < total * 7:
        reasons.append("local_preload_joint_union_below_70_percent")
    times = _source_times(timeline)
    if times is None or any(b <= a for a, b in zip(times, times[1:])):
        reasons.append("local_preload_decoder_pts_invalid")
    hip_y, knees, scales = [], {"left": [], "right": []}, []
    for sample in aligned:
        hips = [_reference_point(sample, joint) for joint in ("left_hip", "right_hip")]
        shoulders = [_reference_point(sample, joint) for joint in ("left_shoulder", "right_shoulder")]
        hip = tuple(statistics.fmean(p[axis] for p in hips) for axis in (0, 1)) if all(p is not None for p in hips) else None
        shoulder = tuple(statistics.fmean(p[axis] for p in shoulders) for axis in (0, 1)) if all(p is not None for p in shoulders) else None
        hip_y.append(hip[1] if hip is not None else None)
        scales.append(math.dist(hip, shoulder) if hip is not None and shoulder is not None else None)
        for side in knees:
            knees[side].append(_value(sample, (side + "_hip", side + "_knee", side + "_ankle"), "flexion"))
    simultaneous = []
    for index in range(max(0, total - 1)):
        if (not jointly_valid[index] or not jointly_valid[index + 1]
                or any(knees[side][j] is None for side in knees for j in (index, index + 1))):
            simultaneous.append(None)
        else:
            simultaneous.append(hip_y[index + 1] > hip_y[index] and all(
                knees[side][index + 1] > knees[side][index] for side in knees))
    measured = measure_card_window(samples, window, timeline, required_joint_names=REQUIRED_JOINTS)
    reasons.extend(measured["reason_codes"])
    values = {}
    for metric, _, _ in AUXILIARY_REFERENCES:
        name, _, reducer = metric.rpartition(".")
        name, reducer = (name, reducer) if "." in metric else (metric, "value")
        evidence = measured["measurements"][name]
        value = evidence["statistics"].get(reducer)
        if value is None:
            reasons.extend(evidence["reason_codes"] or ["local_preload_auxiliary_value_unavailable"])
        values[metric] = value
    reasons = sorted(set(reasons))
    complete = not reasons
    fraction = (float(sum((b - a for a, b, valid in zip(times, times[1:], simultaneous) if valid), start=0)
                      / (times[-1] - times[0])) if complete else None)
    return {"version": VERSION, "status": "measured_auxiliary" if complete else "unavailable",
        "semantics": ANNOTATION_SEMANTICS, "window": dict(window), "reason_codes": reasons,
        "original_grade": None, "proxy_grade": None, "score": None, "source_grade_supported": False,
        "split_step_confirmed": False, "interpolation_used": False,
        "measurement_version": measured["version"],
        "auxiliary_observations": [{"metric": metric, "unit": unit, "value": values[metric] if complete else None,
            "engineering_reference": {"operator": "gte", "value": threshold}, "passed": None,
            "scoring_eligible": False} for metric, unit, threshold in AUXILIARY_REFERENCES],
        "joint_descent_fraction": fraction,
        "joint_descent_semantics": "duration_weighted_adjacent_intervals_with_hip_y_and_both_knee_flexions_increasing",
        "quality": {"required_joint_names": list(REQUIRED_JOINTS), "valid_frame_count": valid_count,
            "total_frame_count": total, "fraction": valid_count / total if total else None,
            "geometry_gate_passed": bool(total >= 3 and valid_count * 10 >= total * 7),
            "continuous_joint_geometry_complete": bool(total and valid_count == total),
            "joint_valid_frames": {joint: [row["frame_index"] for row, valid in zip(timeline, mask) if valid]
                                   for joint, mask in valid_by_joint.items()},
            "visibility_verified": False, "image_bounds_verified": False,
            "denominator": "all_expected_decoder_frames_in_closed_local_window"},
        "source_frame_bindings": [dict(row) for row in timeline],
        "series": {"coordinate_basis": "camera_reference_pixels", "source_frames": [row["frame_index"] for row in timeline],
            "source_timestamps_ms": [float(t) for t in times] if times is not None else [],
            "hip_y_camera_reference": hip_y, "left_knee_flexion_deg": knees["left"],
            "right_knee_flexion_deg": knees["right"], "torso_scale_by_frame": scales,
            "fixed_torso_scale": statistics.median(scales) if complete else None,
            "endpoint_frame_count": max(3, math.ceil(total * .2)) if total >= 7 else None,
            "pair_source_frames": [[a["frame_index"], b["frame_index"]] for a, b in zip(timeline, timeline[1:])],
            "simultaneous_descent_intervals": simultaneous},
        "limitations": ["manual_window_not_expert_stage_or_split_step_truth",
            "local_dip_may_be_standing_adjustment", "image_hip_midpoint_not_physical_centre_of_mass",
            "no_force_ground_contact_balance_or_gaze_claim", "engineering_references_are_not_source_grades"]}
