"""Source-bound image direction measurements for small adjustment windows.

A manually observed line is an image-plane reference, not a court homography,
ball prediction, intention, foot contact or an original-card technical grade.
No supplied completion flag, measurement or score is accepted by this module.
"""
from __future__ import annotations

from collections.abc import Mapping
from copy import deepcopy
import math
import statistics

from rallymate_features.coordinates import validated_camera_transform
from .card_kinematics import (
    TORSO, ANKLES, _endpoint, _get, _integer, _middle, _point,
    _reference_point, _source_times, finite, validate_window,
)
from .step_motion_evidence import _phases, _source_geometry, validate_step_motion_observation

VERSION = "direction-reference-observables-v1.0.0"
REFERENCE_VERSION = "manual-image-direction-reference-v1.0.0"
REFERENCE_SEMANTICS = "visible_line_image_projection_not_metric_court_or_target_truth"
REQUIRED_JOINTS = TORSO + ANKLES
AXIS_ROLES = frozenset(("longitudinal_projection", "transverse_projection"))


def validate_direction_reference(reference):
    """Check syntax only; the compute function binds it to the original frame."""
    required = {"version", "method", "semantics", "axis_role", "video_sha256", "person_track_id",
        "selection_epoch", "derived_selection_epoch", "camera_reference_epoch", "source_frame",
        "axis_points_px", "uncertainty_px", "source_description"}
    if not isinstance(reference, Mapping) or not required <= set(reference) or set(reference)-required-{"target_point_px", "target_kind"}:
        raise ValueError("direction_reference_fields_invalid")
    if (reference["version"] != REFERENCE_VERSION or reference["method"] != "manual_visible_line"
            or reference["semantics"] != REFERENCE_SEMANTICS or not isinstance(reference["axis_role"], str) or reference["axis_role"] not in AXIS_ROLES
            or not isinstance(reference["source_description"], str)
            or not 1 <= len(reference["source_description"].strip()) <= 512
            or not finite(reference["uncertainty_px"]) or reference["uncertainty_px"] < 0):
        raise ValueError("direction_reference_semantics_invalid")
    if (("target_point_px" in reference) != ("target_kind" in reference)
            or "target_kind" in reference and reference["target_kind"] != "visible_ball_center"):
        raise ValueError("direction_reference_target_kind_invalid")
    if any(not _integer(reference[key]) for key in
           ("person_track_id", "selection_epoch", "derived_selection_epoch", "camera_reference_epoch")):
        raise ValueError("direction_reference_identity_invalid")
    source = reference["source_frame"]
    if (not isinstance(source, Mapping) or set(source) != {"frame_index", "pts", "time_base_num", "time_base_den"}
            or any(not _integer(source[key]) for key in source)
            or source["time_base_num"] == 0 or source["time_base_den"] == 0):
        raise ValueError("direction_reference_source_clock_invalid")
    points = reference["axis_points_px"]
    if (not isinstance(points, list) or len(points) != 2
            or any(not isinstance(point, list) or len(point) != 2 or not all(finite(v) for v in point) for point in points)
            or "target_point_px" in reference and (not isinstance(reference["target_point_px"], list)
                or len(reference["target_point_px"]) != 2 or not all(finite(v) for v in reference["target_point_px"]))):
        raise ValueError("direction_reference_points_invalid")
    return deepcopy(reference)


def _local_full_torso(samples, window, timeline, source_records):
    """Re-express observed local geometry without inheriting a run-wide origin.

    First close the original normalized coordinates against their declared basis
    and the raw record. Only then make copied full-torso samples. Missing joints
    are never recovered, and the original model binding is never rewritten.
    """
    from .stroke_candidates import _normalization, _sample
    originals = {r.get("frame", {}).get("index"): r for r in source_records
        if isinstance(r, Mapping)} if isinstance(source_records, (list, tuple)) else {}
    rows = {r.get("frame_index"): r for r in timeline if isinstance(r, Mapping)}
    canonical, proof, reasons = [], [], []
    close = lambda a, b: all(math.isclose(x, y, rel_tol=1e-8, abs_tol=1e-8) for x, y in zip(a, b))
    for sample in samples:
        frame_index = _get(sample, "frame_index")
        raw, row = originals.get(frame_index, {}), rows.get(frame_index, {})
        frame, camera = raw.get("frame", {}), raw.get("camera_motion")
        poses = [p for p in raw.get("poses", []) if isinstance(p, Mapping)
            and p.get("person_track_id") == window.get("person_track_id")]
        original = _sample(poses[0], {**frame, "camera_motion": camera}) if len(poses) == 1 else None
        points = {joint: _reference_point(sample, joint) for joint in REQUIRED_JOINTS}
        basis = _get(sample, "normalization_basis")
        side = {"left_shoulder_left_torso": "left", "right_shoulder_right_torso": "right"}.get(basis)
        valid = (basis == "full_torso" or side is not None) and all(points.values())
        old = _normalization(points, side) if valid else None
        full = _normalization(points) if valid else None
        entry = {"frame_index": frame_index, "input_geometry": _source_geometry(sample, frame_index),
            "input_center": deepcopy(_get(sample, "center")), "input_scale": _get(sample, "scale"),
            "output_basis": None, "source_geometry_verified": False}
        proof.append(entry)
        if (old is None or full is None or original is None
                or raw.get("job_id") != window.get("video_id")
                or frame.get("timestamp_source") != "decoder_pts"
                or frame.get("timestamp_ms") != row.get("timestamp_ms")
                or any(k in frame and frame[k] != row.get(k) for k in ("pts", "time_base_num", "time_base_den"))
                or not isinstance(camera, Mapping) or camera.get("reference_epoch") != window.get("camera_reference_epoch")
                or not finite(_get(sample, "scale")) or not isinstance(_get(sample, "center"), (tuple, list))
                or len(_get(sample, "center")) != 2 or not all(finite(v) for v in _get(sample, "center"))
                or not close(_get(sample, "center"), old[0]) or not math.isclose(_get(sample, "scale"), old[1], rel_tol=1e-8)
                or any(_reference_point(original, j) is None or not close(points[j], _reference_point(original, j))
                    or not close(_point(sample, j), tuple((points[j][axis]-old[0][axis])/old[1] for axis in (0, 1))) for j in REQUIRED_JOINTS)):
            reasons.append("direction_original_geometry_or_normalization_unverified")
            canonical.append(sample)
            continue
        cloned = deepcopy(sample)
        changes = {"center": full[0], "scale": full[1], "normalization_basis": "full_torso",
            "points": {name: tuple((point[i]-full[0][i])/full[1] for i in (0, 1))
                for name, point in _get(sample, "reference_points").items()}}
        if isinstance(cloned, dict):
            cloned.update(changes)
        else:
            for key, value in changes.items():
                setattr(cloned, key, value)
        canonical.append(cloned)
        entry.update(output_basis="full_torso", output_center=list(full[0]), output_scale=full[1], source_geometry_verified=True)
    return canonical, proof, reasons


def compute_direction_reference_observables(samples, window, timeline, reference, source_record, *, source_records=()):
    """Compute a full window using an explicit line from one of its source frames.

    ``source_record`` is the original frames.jsonl record selected by the adapter;
    its camera matrix/dimensions are never taken from the annotation. The adapter
    must also verify artifact/video hashes and the unchanged decoder inventory.
    """
    rows = list(timeline) if isinstance(timeline, (list, tuple)) else []
    canonical, normalization, geometry_reasons = _local_full_torso(samples, window, rows, source_records)
    reasons, aligned = validate_window(canonical, window, timeline)
    reasons.extend(geometry_reasons)
    result = {"version": VERSION, "status": "unavailable", "reason_codes": list(reasons),
        "required_joint_names": list(REQUIRED_JOINTS), "total_frame_count": len(rows),
        "source_frames": [row.get("frame_index") for row in rows], "source_frame_bindings": deepcopy(rows),
        "source_geometry_series": [], "reference_series": [], "measurements": {}, "feet": {},
        "source_grade_supported": False, "contact_verified": False, "target_reasonableness_verified": False,
        "court_metric_direction_verified": False, "interpolation_used": False,
        "denominator": "all_decoder_samples_in_bound_window", "geometry_is_visibility": False,
        "local_normalization": normalization, "geometry_gate_passed": False}
    if len(aligned) != len(rows):
        aligned = [None]*len(rows)
    result["source_geometry_series"] = [_source_geometry(sample, row.get("frame_index")) for row, sample in zip(rows, aligned)]
    try:
        annotation = validate_direction_reference(reference)
    except (ValueError, TypeError, KeyError):
        result["reason_codes"].append("direction_reference_invalid")
        return result
    result["direction_reference"] = annotation
    if (not _integer(window.get("derived_selection_epoch")) or not _integer(window.get("camera_reference_epoch"))
            or window.get("camera_compensated") is not True
            or any(row.get("derived_selection_epoch") != window.get("derived_selection_epoch")
                or row.get("selected_person_track_id") != window.get("person_track_id")
                or row.get("camera_reference_epoch") != window.get("camera_reference_epoch")
                or row.get("selection_epoch") != window.get("selection_epoch")
                or row.get("video_sha256") != window.get("video_sha256") for row in rows)):
        result["reason_codes"].append("direction_reference_complete_source_ledger_required")
    if any(annotation[key] != window.get(key) for key in
           ("video_sha256", "person_track_id", "selection_epoch", "derived_selection_epoch", "camera_reference_epoch")):
        result["reason_codes"].append("direction_reference_window_identity_mismatch")
    source = annotation["source_frame"]
    matched = [row for row in rows if row.get("frame_index") == source["frame_index"]]
    frame = source_record.get("frame", {}) if isinstance(source_record, Mapping) else {}
    camera = source_record.get("camera_motion") if isinstance(source_record, Mapping) else None
    transform = validated_camera_transform(camera)
    if (len(matched) != 1 or any(matched[0].get(k) != v for k, v in source.items())
            or not isinstance(source_record, Mapping) or source_record.get("job_id") != window.get("video_id")
            or frame.get("index") != source["frame_index"]
            or frame.get("timestamp_ms") != matched[0].get("timestamp_ms")
            or frame.get("timestamp_source") != "decoder_pts"
            or any(k in frame and frame[k] != source[k] for k in ("pts", "time_base_num", "time_base_den"))
            or not isinstance(camera, Mapping) or camera.get("reference_epoch") != window.get("camera_reference_epoch")
            or transform is None):
        result["reason_codes"].append("direction_reference_original_frame_mismatch")
    dimensions = (frame.get("width"), frame.get("height"))
    if not all(type(value) is int and value > 0 for value in dimensions):
        result["reason_codes"].append("direction_reference_source_dimensions_missing")
    else:
        raw_points = annotation["axis_points_px"] + ([annotation["target_point_px"]] if "target_point_px" in annotation else [])
        if any(not 0 <= point[0] < dimensions[0] or not 0 <= point[1] < dimensions[1] for point in raw_points):
            result["reason_codes"].append("direction_reference_point_outside_original_frame")
        if math.dist(*annotation["axis_points_px"]) <= 2*annotation["uncertainty_px"]:
            result["reason_codes"].append("direction_reference_axis_unresolved")
    try:
        moving = validate_step_motion_observation(window.get("step_motion_observation"),
            first_frame=rows[0]["frame_index"], last_frame=rows[-1]["frame_index"])
        by_frame = {row["frame_index"]: row for row in rows}
        if any(any(by_frame.get(proof["frame_index"], {}).get(k) != v for k, v in proof.items()) for proof in moving["source_frames"]):
            result["reason_codes"].append("direction_moving_foot_source_mismatch")
    except (ValueError, TypeError, IndexError, KeyError):
        moving = None
        result["reason_codes"].append("direction_moving_foot_observation_missing")
    result["moving_foot_observation"] = moving
    points = [{joint: _reference_point(sample, joint) for joint in REQUIRED_JOINTS}
        if sample is not None else {} for sample in aligned]
    common = [row["frame_index"] for row, point in zip(rows, points) if all(point.get(joint) is not None for joint in REQUIRED_JOINTS)]
    result.update(valid_frame_count=len(common), valid_source_frames=common)
    if len(rows) < 7 or len(common) != len(rows):
        result["reason_codes"].append("direction_common_geometry_not_continuous")
    # Reconstruct the producer's full-torso coordinates, not original-image axes.
    for sample, point in zip(aligned, points):
        if not all(point.get(joint) is not None for joint in REQUIRED_JOINTS):
            continue
        shoulder, hip = _middle(*(point[j] for j in TORSO[:2])), _middle(*(point[j] for j in TORSO[2:]))
        scale = max(math.dist(shoulder, hip), math.dist(*(point[j] for j in TORSO[:2]))*.75)
        if (scale <= 0 or _get(sample, "observation_semantics") == "monocular_model_estimate" and _get(sample, "pose_source") != "sam3d"
                or any(not math.isclose(_point(sample, joint)[axis], (point[joint][axis]-shoulder[axis])/scale,
                    rel_tol=1e-8, abs_tol=1e-8) for joint in REQUIRED_JOINTS for axis in (0, 1))):
            result["reason_codes"].append("direction_normalized_reference_geometry_mismatch")
    if result["reason_codes"]:
        result["reason_codes"] = sorted(set(result["reason_codes"]))
        return result
    # The annotation cannot supply a substitute camera matrix: check that its
    # original frame reproduces the same pose reference pixels as the window.
    from .stroke_candidates import _sample
    original_poses = source_record.get("poses", [])
    selected_poses = [pose for pose in original_poses if isinstance(pose, Mapping)
        and pose.get("person_track_id") == window["person_track_id"]] if isinstance(original_poses, list) else []
    raw_sample = _sample(selected_poses[0], {**frame, "camera_motion": camera}) if len(selected_poses) == 1 else None
    anchor = next(i for i, row in enumerate(rows) if row["frame_index"] == source["frame_index"])
    if raw_sample is None or any(_reference_point(raw_sample, joint) is None
            or not all(math.isclose(a, b, rel_tol=1e-8, abs_tol=1e-8)
                for a, b in zip(_reference_point(raw_sample, joint), points[anchor][joint])) for joint in REQUIRED_JOINTS):
        result["reason_codes"] = ["direction_reference_original_geometry_mismatch"]
        return result
    result["geometry_gate_passed"] = True
    convert = lambda p: tuple(float(transform[axis, 0]*p[0]+transform[axis, 1]*p[1]+transform[axis, 2]) for axis in (0, 1))
    axis_points = [convert(p) for p in annotation["axis_points_px"]]
    length = math.dist(*axis_points)
    axis = tuple((axis_points[1][i]-axis_points[0][i])/length for i in (0, 1))
    perpendicular = (-axis[1], axis[0])  # Mathematical image perpendicular only.
    hips = [_middle(*(point[j] for j in TORSO[2:])) for point in points]
    scales = [math.dist(_middle(*(point[j] for j in TORSO[:2])), hip) for point, hip in zip(points, hips)]
    scale = statistics.median(scales)
    if not finite(scale) or scale <= 0:
        result["reason_codes"] = ["direction_fixed_torso_scale_unavailable"]
        return result
    exact, count = _source_times(rows), max(3, math.ceil(len(rows)*.2))
    before, after = _endpoint(hips, count)
    delta = tuple(after[i]-before[i] for i in (0, 1))
    dot = lambda p, direction: sum(a*b for a, b in zip(p, direction))
    net = math.hypot(*delta)/scale
    values = {"hip_axis_displacement_torso": dot(delta, axis)/scale,
        "hip_image_perpendicular_displacement_torso": dot(delta, perpendicular)/scale,
        "hip_net_displacement_torso": net,
        "hip_axis_alignment_fraction": abs(dot(delta, axis))/math.hypot(*delta) if net > 0 else 0.0}
    if "target_point_px" in annotation:
        target = convert(annotation["target_point_px"])
        values["hip_axis_target_error_reduction_torso"] = (abs(dot(tuple(target[i]-before[i] for i in (0, 1)), axis))
            - abs(dot(tuple(target[i]-after[i] for i in (0, 1)), axis)))/scale
    for side in ("left", "right"):
        result["feet"][side] = _phases([point[side+"_ankle"] for point in points], exact, scale, result["source_frames"], count)
    declared = result["feet"][moving["moving_foot"]]
    values.update(moving_ankle_net_displacement_torso=declared["net_displacement_torso"],
        moving_ankle_path_length_torso=declared["path_length_torso"],
        both_ankles_max_net_displacement_torso=max(foot["net_displacement_torso"] for foot in result["feet"].values()),
        both_ankles_max_path_length_torso=max(foot["path_length_torso"] for foot in result["feet"].values()))
    result.update(fixed_torso_scale=scale, endpoint_count=count, measurements=values,
        reference_series=[{"frame_index": row["frame_index"], "points": {joint: list(point[joint]) for joint in REQUIRED_JOINTS}}
            for row, point in zip(rows, points)],
        projected_reference={"axis_points_reference_px": [list(p) for p in axis_points], "unit_axis": list(axis),
            "image_perpendicular": list(perpendicular), "perpendicular_is_court_transverse": False,
            "matrix_to_reference": transform.tolist(), "frame_dimensions_px": list(dimensions)},
        source_record_frame={key: frame.get(key) for key in ("index", "processed_index", "timestamp_ms", "timestamp_source", "width", "height")})
    result["reason_codes"].extend(declared["reason_codes"])
    result["reason_codes"] = sorted(set(result["reason_codes"]))
    result["status"] = "measured_directional_motion" if not result["reason_codes"] else "unavailable"
    return result
