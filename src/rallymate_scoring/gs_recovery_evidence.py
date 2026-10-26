"""Source-bound M10 body observations, separate from unobserved racket control.

All angles are axial image-plane angles, not anatomical yaw. A ready reference
is an independently bound, earlier source window; the end of M10 never supplies
its own target. The policy is frozen engineering sensitivity, not source grades.
"""
from __future__ import annotations

from collections.abc import Mapping
from copy import deepcopy
import math
import statistics

from .card_kinematics import (
    TORSO, ANKLES, _axis, _get, _middle, _point, _reference_point, _source_times,
    finite, measure_card_window, validate_window,
)

VERSION = "gs-recovery-evidence-v1.0.0"
SCORING_REVISION_ID = "gs-m10-observability-v1.0.0"
POLICY_VERSION = "gs-m10-projected-recovery-v1.0.0"
REFERENCE_VERSION = "gs-ready-reference-v1.0.0"
REFERENCE_SEMANTICS = "independent_pre_action_body_ready_reference_not_technical_ground_truth"
AUXILIARY_VERSION = "gs-m10-auxiliary-v1.0.0"
ROTATION_CARDS = frozenset(("GS01-M10-02", "GS02-M10-02", "GS03-M10-03"))
STABILITY_CARDS = frozenset(("GS01-M10-03", "GS02-M10-03", "GS03-M10-04"))
MANUAL_REQUIREMENTS = {
    "GS01-M10-01": ("bilateral_hand_racket_control_source_required", (
        "source_bound_bilateral_hand_identity", "source_bound_racket_handle_or_contact_region", "bilateral_hand_racket_control_sequence")),
    "GS02-M10-01": ("continuous_two_hand_racket_control_source_required", (
        "source_bound_bilateral_hand_identity", "source_bound_racket_handle_geometry", "continuous_two_hand_racket_control_sequence")),
    "GS03-M10-01": ("non_racket_hand_to_throat_contact_source_required", (
        "source_bound_non_racket_hand_identity", "source_bound_racket_throat_RK013", "non_racket_hand_throat_recontact_sequence")),
    "GS03-M10-02": ("racket_hand_handle_control_source_required", (
        "source_bound_racket_hand_identity", "source_bound_racket_handle_and_axis", "racket_hand_handle_control_and_body_front_sequence")),
    "GS01-M10-04": ("complete_next_shot_ready_state_source_required", (
        "body_recovery_geometry", "bilateral_hand_racket_control_sequence", "source_bound_successor_split_or_first_step")),
    "GS02-M10-04": ("complete_two_hand_ready_posture_source_required", (
        "body_recovery_geometry", "continuous_two_hand_ready_grip_source", "independent_two_hand_ready_posture_reference")),
    "GS03-M10-05": ("complete_generic_ready_state_source_required", (
        "body_recovery_geometry", "bilateral_hand_racket_control_sequence", "source_bound_racket_in_front_of_body")),
}
SUPPORTED_CARDS = ROTATION_CARDS | STABILITY_CARDS | MANUAL_REQUIREMENTS.keys()
ROTATION_CONDITIONS = (
    ("shoulder_reference_error_reduction_deg", "gte", 8.0, "deg"),
    ("hip_reference_error_reduction_deg", "gte", 8.0, "deg"),
    ("terminal_max_reference_error_deg", "lte", 15.0, "deg"),
    ("terminal_max_axis_range_deg", "lte", 8.0, "deg"),
)
STABILITY_CONDITIONS = (
    ("terminal_hip_speed_torso_s", "lte", .6, "torso/s"),
    ("terminal_hip_inside_ankle_support_fraction", "gte", .8, "fraction"),
    ("terminal_trunk_tilt_range_deg", "lte", 15.0, "deg"),
)
MIN_RECOVERY_FRAMES = 7
MIN_REFERENCE_FRAMES = 3
MIN_WINDOW_MS = 80
REFERENCE_MAX_AXIS_RANGE_DEG = 8.0
MIN_AXIS_LENGTH_TORSO = .18
ENDPOINT_FRACTION = .2
MIN_ENDPOINT_FRAMES = 3
BODY_JOINTS = TORSO + ANKLES


def recovery_metadata(rule):
    evaluation = rule.get("evaluation", {})
    if rule.get("indicator_id") not in SUPPORTED_CARDS or evaluation.get("scoring_revision_id") != SCORING_REVISION_ID:
        return {}
    return {"scoring_revision_id": SCORING_REVISION_ID,
            "missing_required_observables": list(evaluation.get("required_observables", ()))
            if rule["indicator_id"] in MANUAL_REQUIREMENTS else []}


def _unwrap(values):
    result = [values[0]]
    for a, b in zip(values, values[1:]):
        result.append(result[-1] + (b-a+90) % 180 - 90)
    return result


def _spread(values):
    return max(values)-min(values)


def _geometry_row(sample, frame):
    """Keep both coordinate series and their provenance; never infer confidence."""
    row = {"frame_index": frame["frame_index"]}
    fields = ("track", "time", "selection_epoch", "camera_reference_epoch", "camera_compensated",
        "timestamp_source", "normalization_basis", "center", "scale", "pose_source", "observation_semantics",
        "source_binding", "source_model_selection_epoch", "model_epoch_semantics")
    for key in fields:
        value = _get(sample, key) if sample is not None else None
        if value is not None:
            row[key] = deepcopy(value)
    for key in ("points", "reference_points", "point_confidences"):
        value = _get(sample, key, {}) if sample is not None else {}
        row[key] = deepcopy(dict(value)) if isinstance(value, Mapping) else {}
    return row


def _trajectory(samples, window, timeline, joints, *, minimum):
    reasons, aligned = validate_window(samples, window, timeline)
    proof = {"window": deepcopy(window), "source_frame_bindings": deepcopy(list(timeline)),
        "source_geometry_series": [_geometry_row(sample, frame) for sample, frame in zip(aligned, timeline)],
        "total_frame_count": len(timeline), "valid_frame_count": 0,
        "required_joint_names": list(joints), "reason_codes": [],
        "points_semantics": "camera_reference_points_not_to_be_transformed_again",
        "normalization_semantics": "fixed_median_torso_for_this_complete_window",
        "interpolation_used": False}
    # Early validator failures may not align samples; still keep every source row.
    if len(proof["source_geometry_series"]) != len(timeline):
        observed = {_get(s, "frame_index"): s for s in samples}
        proof["source_geometry_series"] = [_geometry_row(observed.get(r["frame_index"]), r) for r in timeline]
    if len(timeline) < minimum:
        reasons.append("recovery_continuous_window_too_short" if minimum == MIN_RECOVERY_FRAMES else "ready_reference_too_short")
    if not window.get("camera_compensated"):
        reasons.append("fixed_camera_reference_required")
    values = []
    for sample in aligned:
        points = {j: _reference_point(sample, j) if sample is not None else None for j in joints}
        if any(p is None for p in points.values()):
            values.append(None)
        else:
            values.append(points)
    for sample, row in zip(aligned, values):
        if row is None:
            continue
        center = _middle(*(row[j] for j in TORSO[:2]))
        local_scale = max(math.dist(center, _middle(*(row[j] for j in TORSO[2:]))),
            math.dist(row[TORSO[0]], row[TORSO[1]])*.75)
        supplied_center, supplied_scale = _get(sample, "center"), _get(sample, "scale")
        if (not isinstance(supplied_center, (list, tuple)) or len(supplied_center) != 2
                or not all(finite(v) for v in supplied_center) or not finite(supplied_scale)
                or local_scale <= 0 or abs(supplied_scale-local_scale) > 1e-8*max(1, local_scale)
                or math.dist(supplied_center, center) > 1e-8*max(1, local_scale)
                or any(math.dist(_point(sample, j), tuple((row[j][i]-center[i])/local_scale for i in (0, 1))) > 1e-8 for j in joints)):
            reasons.append("recovery_source_geometry_normalization_mismatch")
    proof["valid_frame_count"] = sum(row is not None for row in values)
    if len(values) != len(timeline) or any(row is None for row in values):
        reasons.append("recovery_joint_missing_in_continuous_window")
    proof["reason_codes"] = sorted(set(reasons))
    if reasons:
        return proof, None
    scales = [math.dist(_middle(*(r[j] for j in TORSO[:2])), _middle(*(r[j] for j in TORSO[2:]))) for r in values]
    scale = statistics.median(scales)
    if not finite(scale) or scale <= 0:
        proof["reason_codes"] = ["fixed_torso_scale_unavailable"]
        return proof, None
    times = _source_times(timeline)
    dt = [float(b-a)/1000 for a, b in zip(times, times[1:])]
    hips = [_middle(*(r[j] for j in TORSO[2:])) for r in values]
    displacements = [[b[axis]-a[axis] for axis in (0, 1)] for a, b in zip(hips, hips[1:])]
    hip_speeds = [math.hypot(*d)/(t*scale) for d, t in zip(displacements, dt)]
    axes = {name: [_axis(tuple(v/scale for v in r[pair[0]]), tuple(v/scale for v in r[pair[1]])) for r in values]
        for name, pair in (("shoulder", TORSO[:2]), ("hip", TORSO[2:]))}
    unwrapped = {name: _unwrap(v) if all(a is not None for a in v) else None for name, v in axes.items()}
    trunk = []
    support = []
    for r, hip in zip(values, hips):
        shoulder = _middle(*(r[j] for j in TORSO[:2]))
        trunk.append(math.degrees(math.atan2(abs(hip[0]-shoulder[0]), abs(hip[1]-shoulder[1]))))
        if all(j in r for j in ANKLES):
            support.append(float(min(r[ANKLES[0]][0], r[ANKLES[1]][0]) <= hip[0] <= max(r[ANKLES[0]][0], r[ANKLES[1]][0])))
    proof.update(fixed_torso_scale=scale, source_times_ms=[float(t) for t in times], dt_seconds=dt,
        hip_reference_positions=hips, hip_displacement_vectors=displacements, hip_speeds_torso_s=hip_speeds,
        axis_degrees=axes, unwrapped_axis_degrees=unwrapped,
        axis_angular_speeds_deg_s={name: [(b-a)/t for a, b, t in zip(v, v[1:], dt)] if v is not None else None for name, v in unwrapped.items()},
        trunk_tilt_degrees=trunk,
        hip_inside_ankle_support=support)
    return proof, values


def _measurement(name, value, unit, joints, proof, reasons=()):
    available = finite(value) and not reasons
    return {"feature_name": name, "feature_version": VERSION, "unit": unit,
        "required_joint_names": list(joints), "total_frame_count": proof["total_frame_count"],
        "valid_frame_count": proof["valid_frame_count"], "geometry_gate_passed": available,
        "source_frames": [r["frame_index"] for r in proof["source_frame_bindings"]],
        "statistics": {"value": value} if available else {}, "reason_codes": sorted(set(reasons)),
        "visibility_verified": False, "image_bounds_verified": False,
        "denominator": "all_decoder_samples_in_bound_stage", "semantics": "image_plane_application_observation"}


def _reference_cross_binding(window, timeline, reference):
    """Both endpoints AND the full intervening source ledger must stay bound."""
    if not isinstance(reference, Mapping):
        return ["independent_ready_reference_missing"]
    rw, rt, bridge = reference.get("window", {}), reference.get("timeline", []), reference.get("continuity_timeline", [])
    binding = reference.get("binding", {})
    if (binding.get("version") != REFERENCE_VERSION or binding.get("semantics") != REFERENCE_SEMANTICS
            or binding.get("method") != "manual_visual_observation"):
        return ["ready_reference_source_binding_invalid"]
    keys = ("video_sha256", "video_id", "person_track_id", "selection_epoch", "derived_selection_epoch",
        "camera_reference_epoch", "camera_compensated")
    if any(rw.get(k) != window.get(k) for k in keys):
        return ["ready_reference_identity_or_camera_mismatch"]
    if not rt or not timeline or not finite(reference.get("action_start_ms")):
        return ["ready_reference_source_binding_invalid"]
    reference_times, stage_times = _source_times(rt), _source_times(timeline)
    if reference_times is None or stage_times is None:
        return ["ready_reference_source_binding_invalid"]
    if not reference_times[-1] < reference["action_start_ms"] <= stage_times[0]:
        return ["ready_reference_not_independent_pre_action"]
    if (binding.get("person_track_id") != window["person_track_id"]
            or binding.get("video_sha256") != window["video_sha256"]
            or not isinstance(binding.get("action_id"), str) or not binding["action_id"]):
        return ["ready_reference_source_binding_invalid"]
    for key, row in (("start_frame", rt[0]), ("end_frame", rt[-1])):
        endpoint = binding.get(key)
        if (not isinstance(endpoint, Mapping) or set(endpoint) != {"frame_index", "pts", "time_base_num", "time_base_den"}
                or any(type(v) is not int or v < 0 or row.get(k) != v for k, v in endpoint.items())):
            return ["ready_reference_source_binding_invalid"]
    if (type(window.get("derived_selection_epoch")) is not int or window["derived_selection_epoch"] < 0
            or type(window.get("selection_epoch")) is not int or window["selection_epoch"] < 0):
        return ["ready_reference_source_binding_invalid"]
    expected = list(range(rt[0]["frame_index"], timeline[-1]["frame_index"]+1))
    if [r.get("frame_index") for r in bridge] != expected:
        return ["ready_reference_intervening_source_incomplete"]
    exact = _source_times(bridge)
    if exact is None or any(b <= a for a, b in zip(exact, exact[1:])):
        return ["ready_reference_intervening_pts_invalid"]
    gaps = [float(b-a) for a, b in zip(exact, exact[1:])]
    if gaps and max(gaps) > min(160, statistics.median(gaps)*1.8):
        return ["ready_reference_intervening_pts_discontinuous"]
    if any(bridge[i] != r for i, r in enumerate(rt)) or bridge[-len(timeline):] != list(timeline):
        return ["ready_reference_intervening_source_copy_mismatch"]
    for r in bridge:
        if (r.get("selected_person_track_id") != window["person_track_id"]
                or r.get("selection_epoch") != window["selection_epoch"]
                or r.get("derived_selection_epoch") != window.get("derived_selection_epoch")
                or r.get("camera_reference_epoch") != window.get("camera_reference_epoch")
                or r.get("video_sha256") != window.get("video_sha256")):
            return ["ready_reference_intervening_identity_or_camera_changed"]
        if r.get("rejected_pose_person_track_id") == window["person_track_id"]:
            return ["selected_pose_person_association_conflict"]
        if r.get("rejected_model_person_track_id") == window["person_track_id"]:
            return ["selected_model_observation_rejected"]
    bridge_window = {**window, "start_ms": bridge[0]["timestamp_ms"], "end_ms": bridge[-1]["timestamp_ms"]}
    bridge_samples = reference.get("continuity_samples")
    if not isinstance(bridge_samples, (list, tuple)):
        return ["ready_reference_intervening_source_samples_missing"]
    bridge_reasons, aligned = validate_window(bridge_samples, bridge_window, bridge)
    # A complete clock/selection ledger alone does not prove that every
    # intervening source observation was retained. validate_window deliberately
    # allows absent samples for other cards' coverage policies; this paired
    # reference contract requires one bound sample for every bridge row.
    if any(sample is None for sample in aligned):
        bridge_reasons.append("ready_reference_intervening_source_sample_missing")
    if bridge_reasons:
        return sorted(set(bridge_reasons + ["ready_reference_intervening_source_binding_invalid"]))
    model_rows = [r for r in bridge if "source_model_selection_epoch" in r or "model_epoch_semantics" in r]
    if any(type(r.get("source_model_selection_epoch")) is not int or r["source_model_selection_epoch"] < 0
            or r.get("model_epoch_semantics") != "detector_track_epoch_primary_selection_applied_downstream" for r in model_rows):
        return ["ready_reference_intervening_model_epoch_invalid"]
    model_epochs = {r["source_model_selection_epoch"] for r in model_rows}
    if len(model_epochs) > 1:
        return ["ready_reference_intervening_model_epoch_changed"]
    return []


def compute_gs_recovery_evidence(samples, window, timeline, *, kind, reference=None):
    """Pure observed geometry. No external pass flags or grades are accepted."""
    if kind not in {"rotation", "stability"}:
        raise ValueError("unknown_recovery_measurement_kind")
    samples = list(samples)
    joints = TORSO if kind == "rotation" else BODY_JOINTS
    proof, points = _trajectory(samples, window, timeline, joints, minimum=MIN_RECOVERY_FRAMES)
    result = {"version": VERSION, "policy_version": POLICY_VERSION, "scoring_revision_id": SCORING_REVISION_ID,
        "semantics": "projected_axis_return_to_independent_reference" if kind == "rotation" else "terminal_body_projection_stability",
        "kind": kind, "status": "unavailable", "stage": proof, "reference": None,
        "measurements": {}, "reason_codes": list(proof["reason_codes"]),
        "source_grade_supported": False, "original_grade": None}
    specs = ROTATION_CONDITIONS if kind == "rotation" else STABILITY_CONDITIONS
    count = max(MIN_ENDPOINT_FRAMES, math.ceil(len(timeline)*ENDPOINT_FRACTION))
    proof["endpoint_count"] = count
    proof["endpoint_source_frames"] = {"start": [r["frame_index"] for r in timeline[:count]],
        "end": [r["frame_index"] for r in timeline[-count:]]}
    values = {}
    if kind == "stability" and points is not None:
        values = {"terminal_hip_speed_torso_s": statistics.median(proof["hip_speeds_torso_s"][-(count-1):]),
            "terminal_hip_inside_ankle_support_fraction": statistics.fmean(proof["hip_inside_ankle_support"][-count:]),
            "terminal_trunk_tilt_range_deg": _spread(proof["trunk_tilt_degrees"][-count:])}
    if kind == "rotation":
        reasons = _reference_cross_binding(window, timeline, reference)
        result["reason_codes"].extend(reasons)
        if isinstance(reference, Mapping):
            refproof, refpoints = _trajectory(reference.get("samples", []), reference.get("window", {}),
                reference.get("timeline", []), TORSO, minimum=MIN_REFERENCE_FRAMES)
            refproof["binding"] = deepcopy(reference.get("binding", {}))
            refproof["action_start_ms"] = reference.get("action_start_ms")
            refproof["continuity_timeline"] = deepcopy(reference.get("continuity_timeline", []))
            bridge_samples = {_get(sample, "frame_index"): sample for sample in reference.get("continuity_samples", [])}
            refproof["continuity_source_geometry_series"] = [_geometry_row(bridge_samples.get(row["frame_index"]), row)
                for row in reference.get("continuity_timeline", [])]
            result["reference"] = refproof
            result["reason_codes"].extend(refproof["reason_codes"])
            if refpoints is not None and points is not None:
                if any(proof["unwrapped_axis_degrees"][name] is None or refproof["unwrapped_axis_degrees"][name] is None
                       for name in ("shoulder", "hip")):
                    result["reason_codes"].append("projected_axis_degenerate")
                elif any(_spread(v) > REFERENCE_MAX_AXIS_RANGE_DEG for v in refproof["unwrapped_axis_degrees"].values()):
                    result["reason_codes"].append("ready_reference_axis_not_stable")
                elif not result["reason_codes"]:
                    refs = {name: statistics.median(v) for name, v in refproof["unwrapped_axis_degrees"].items()}
                    errors = {name: [abs((a-refs[name]+90) % 180-90) for a in proof["axis_degrees"][name]] for name in refs}
                    proof["reference_error_degrees"] = errors
                    refproof["reference_axis_degrees"] = refs
                    for name in refs:
                        values[f"{name}_reference_error_reduction_deg"] = statistics.median(errors[name][:count])-statistics.median(errors[name][-count:])
                    values["terminal_max_reference_error_deg"] = max(v for e in errors.values() for v in e[-count:])
                    values["terminal_max_axis_range_deg"] = max(_spread(v[-count:]) for v in proof["unwrapped_axis_degrees"].values())
    result["reason_codes"] = sorted(set(result["reason_codes"]))
    for name, _, _, unit in specs:
        result["measurements"][name] = _measurement(name, values.get(name), unit, joints, proof, result["reason_codes"])
    if not result["reason_codes"] and all(row["geometry_gate_passed"] for row in result["measurements"].values()):
        result["status"] = "measured"
    return result


def recovery_auxiliary(samples, window, timeline):
    """Measurable wrist/body data never establishes a grip or complete readiness."""
    measured = measure_card_window(samples, window, timeline)
    names = ("wrist_separation_torso", "shoulder_axis_deg", "hip_axis_deg", "hip_speed_std_torso_s",
        "trunk_tilt_deg", "hip_inside_ankle_support_fraction")
    return {"version": AUXILIARY_VERSION, "scoring_eligible": False,
        "semantics": "wrist_and_body_geometry_not_racket_contact_control_or_complete_readiness",
        "measurements": {name: deepcopy(measured["measurements"][name]) for name in names},
        "reason_codes": list(measured["reason_codes"])}
