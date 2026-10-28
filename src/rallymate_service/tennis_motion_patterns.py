"""Source-aligned motion requirements and measurable engineering contributions.

This module never classifies a stroke from its score. A verified technique is
required for technique-specific rules. Camera-plane motion requires separately
validated, fixed-reference source observations; native monocular 3D translation
is deliberately not a substitute. Ratios are engineering measurements, not
expert-calibrated grades or evidence of force transmission.
"""
from __future__ import annotations

from collections.abc import Mapping
import math

import numpy as np

VERSION = "tennis-motion-patterns-v1.2.1"
PATTERN_METRICS = {
    "movement": (("pattern_center_transfer", "人体中心前移", "ratio", "观察髋中心沿已核验击球方向的连续前移"),),
    "preparation": (("pattern_preparation_coupling", "转体引拍进程配合", "ratio", "观察肩线转动与手腕引拍的进程配合"),),
    "coordination": (
        ("pattern_projected_sequence", "髋肩腕运动时序", "phase_fraction", "比较固定画面参考下髋线、肩线和手腕的运动峰先后"),
        ("pattern_two_hand_coupling", "双腕运动配合", "ratio", "双腕在挥出时同向运动且进程接近"),
        ("pattern_serve_arm_sequence", "发球上下臂衔接", "phase_fraction", "比较上臂绕肩与前臂相对上臂转动的运动峰先后"),
    ),
    "striking": (
        ("pattern_forward_swing", "向前挥出轨迹", "ratio", "持拍手沿独立核验的击球方向挥出"),
        ("pattern_slice_path", "切削前下延展", "ratio", "观察前下延展，反手切削保留前向运动"),
        ("pattern_serve_upward_path", "发球上挥轨迹", "ratio", "持拍手沿独立核验的画面向上方向连续挥出"),
    ),
    "follow_through": (
        ("pattern_phase_continuity", "挥出与随挥连续性", "ratio", "逐阶段保持连续运动，允许随挥中继续加速"),
        ("pattern_one_hand_reversal", "单反前后收拍衔接", "ratio", "在完整恢复窗口观察先向前延展再向后收拍"),
    ),
}

# Internal traceability only. Public results contain semantic requirement names,
# never document paragraph identifiers. Tests check these locators against the
# immutable source contract; source material supplies no numerical cut points.
SOURCE_REQUIREMENTS = {
    "preparation_coupling": ("转体与引拍配合", "pattern_preparation_coupling", ("VIS-GS:P0073", "VIS-GS:P0127", "VIS-GS:P0173", "VIS-RT:P0036")),
    "projected_sequence": ("髋、肩、手臂的可见运动顺序", "pattern_projected_sequence", ("VIS-GS:P0042", "VIS-GS:P0097")),
    "center_transfer": ("挥出时人体中心前移", "pattern_center_transfer", ("VIS-GS:P0097", "VIS-GS:P0145", "VIS-GS:P0237", "VIS-SV:P0048")),
    "forward_swing": ("持拍手向前挥出", "pattern_forward_swing", ("VIS-GS:P0097", "VIS-GS:P0145", "VIS-GS:P0191")),
    "phase_continuity": ("挥出和随挥连续，不强制击后立即减速", "pattern_phase_continuity", ("VIS-GS:P0106", "VIS-GS:P0107", "VIS-RT:P0041")),
    "two_hand_coupling": ("双手共同运动与加速", "pattern_two_hand_coupling", ("VIS-GS:P0127", "VIS-GS:P0145", "VIS-GS:P0154")),
    "one_hand_reversal": ("单反随挥先向前再向后", "pattern_one_hand_reversal", ("VIS-GS:P0200",)),
    "slice_path": ("切削高位向前下延展", "pattern_slice_path", ("VIS-GS:P0237", "VIS-GS:P0283")),
    "serve_arm_sequence": ("发球上臂与前臂依次转动", "pattern_serve_arm_sequence", ("VIS-SV:P0048",)),
    "serve_upward_path": ("发球向上挥出", "pattern_serve_upward_path", ("VIS-SV:P0037", "VIS-SV:P0039")),
    "physical_chain": ("蹬地、真实重心和力的逐级传递", None, ("VIS-GS:P0042", "VIS-GS:P0097")),
    "racket_and_contact": ("球拍轨迹、握拍与真实触球", None, ("VIS-GS:P0145", "VIS-RT:P0044")),
    "return_compactness": ("按来球压力调整短引拍", None, ("VIS-RT:P0032", "VIS-RT:P0036")),
    "serve_ball_toss": ("抛球、触球和落地衔接", None, ("VIS-SV:P0020", "VIS-SV:P0048")),
}
BASE_PROFILES = {
    "baseline_forehand": ("preparation_coupling", "projected_sequence", "center_transfer", "forward_swing"),
    "baseline_two_hand_backhand": ("preparation_coupling", "projected_sequence", "center_transfer", "forward_swing", "two_hand_coupling"),
    "baseline_one_hand_backhand": ("preparation_coupling", "projected_sequence", "center_transfer", "forward_swing", "one_hand_reversal"),
    "backhand_slice": ("preparation_coupling", "center_transfer", "slice_path"),
    "forehand_slice": ("preparation_coupling", "slice_path"),
    "serve": ("center_transfer", "serve_upward_path", "serve_arm_sequence"),
}
RETURN_BASE = {
    "return_forehand": "baseline_forehand", "return_two_hand_backhand": "baseline_two_hand_backhand",
    "return_one_hand_backhand": "baseline_one_hand_backhand", "return_backhand_slice": "backhand_slice",
    "return_forehand_slice": "forehand_slice",
}
TECHNIQUES = tuple(BASE_PROFILES) + tuple(RETURN_BASE)
RUBRIC = {
    "version": VERSION, "threshold_origin": "engineering_initial_values_not_source_numeric_grades",
    "classification_required": "verified_technique_not_inferred_from_metric_scores",
    "camera_motion_semantics": "fixed_reference_image_plane_proxy_not_world_3d_or_force",
    "native_3d_translation_used": False, "racket_or_ball_contact_assumed": False,
    "minimum_path_torso": .03, "minimum_axis_rotation_deg": 3.,
    "minimum_projected_axis_span_torso": {"hip": .18, "shoulder": .3},
    "max_gap_ms": 160., "phase_motion_floor": "max_0.03_torso_per_second_and_0.1_phase_p95_speed",
    "phase_continuity": "equal_phase_mean_of_elapsed_time_above_motion_floor_no_deceleration_requirement",
    "preparation_coupling": "100_times_one_minus_time_mean_absolute_difference_of_normalized_cumulative_paths",
    "projected_sequence": "100_times_one_minus_reverse_peak_delays_beyond_one_interval_divided_by_window_duration",
    "axis_observation_scope": "metric_phase_plus_fixed_derivative_support_not_entire_episode",
    "sequence_peak_support": "search_only_declared_phase_three_original_observations_each_side_for_derivative_and_peak_bracketing",
    "two_hand_coupling": "100_times_equal_mean_direction_agreement_and_normalized_progress_agreement",
    "directional_path": "100_times_positive_projected_travel_divided_by_absolute_projected_travel",
    "slice_path": "geometric_mean_forward_and_downward_progress_backhand_multiplied_by_min_1_forward_over_downward_travel",
    "slice_direction_observability": {"maximum_unit_basis_condition_number": 4.,
                                      "numerical_boundary_relative_tolerance": 1e-12,
                                      "semantics": "fixed_2d_projected_direction_error_amplification_bound_not_world_components"},
    "one_hand_reversal": "100_times_correct_signed_travel_before_and_after_observed_forward_apex_divided_by_total_signed_travel",
    "missing_policy": "no_score_for_missing_required_evidence_keep_requirement_in_coverage_denominator",
}


def _finite(value):
    return isinstance(value, (int, float, np.integer, np.floating)) and not isinstance(value, (bool, np.bool_)) and math.isfinite(value)


def _point(value, dimensions):
    try:
        value = np.asarray(value, dtype=float)
        return value if value.shape == (dimensions,) and np.isfinite(value).all() else None
    except (ValueError, TypeError):
        return None


def _series(rows, name, field="local", dimensions=3):
    points = [_point(row[field].get(name), dimensions) if isinstance(row.get(field), Mapping) else None for row in rows]
    return np.asarray(points) if points and all(p is not None for p in points) else None


def _velocity(points, times):
    # Common scalar coefficients for every vector component preserve rotations.
    output = np.empty_like(np.asarray(points, dtype=float))
    for i in range(len(times)):
        lo, hi = max(0, i - 2), min(len(times), i + 3)
        t = (times[lo:hi] - times[lo:hi].mean()) / 1000
        output[i] = (t[:, None] * points[lo:hi]).sum(axis=0) / float(t @ t)
    return output


def _progress(points):
    distance = np.r_[0., np.cumsum(np.linalg.norm(np.diff(points, axis=0), axis=1))]
    return distance / distance[-1] if distance[-1] >= .03 else None


def _time_mean(values, times):
    return float(np.sum(np.diff(times) * (values[:-1] + values[1:]) / 2) / (times[-1] - times[0]))


def _axis(points, joint, scale):
    left, right = _series(points, "left_" + joint, "points", 2), _series(points, "right_" + joint, "points", 2)
    if left is None or right is None:
        return None
    vector = (right - left) / scale
    if np.any(np.linalg.norm(vector, axis=1) < {"hip": .18, "shoulder": .3}[joint]):
        return None
    return np.degrees(np.unwrap(2 * np.arctan2(vector[:, 1], vector[:, 0])) / 2)


def _observed_peak(speed, times, search=None):
    """A phase peak needs both sides; support frames never choose a new peak.

    A wrist-speed phase can end at its peak. Fixed neighboring observations
    establish its falling side without moving the search boundary into recovery.
    The existing 95% / two-interval plateau rule remains unchanged.
    """
    first, stop = search or (0, len(times))
    detail = {"search_range_ms": [float(times[first]), float(times[stop - 1])],
              "support_range_ms": [float(times[0]), float(times[-1])]}
    def missing(reason):
        return None, {**detail, "status": "not_measured", "reason": reason}
    maximum = float(np.max(speed[first:stop]))
    selected = np.flatnonzero(speed[first:stop] >= maximum * .95) + first
    if maximum <= 1e-8 or not len(selected):
        return missing("peak_motion_unobservable")
    left, right = int(selected[0]), int(selected[-1])
    while left > 0 and speed[left - 1] >= maximum * .95:
        left -= 1
    while right + 1 < len(times) and speed[right + 1] >= maximum * .95:
        right += 1
    detail["near_peak_range_ms"] = [float(times[left]), float(times[right])]
    if np.any(speed[left:right + 1] > maximum + 1e-8):
        return missing("peak_continues_outside_phase_search")
    if left == 0 or right == len(times) - 1:
        return missing("peak_two_sided_support_missing")
    if times[right] - times[left] > 2 * np.median(np.diff(times[first:stop])) + 1e-7:
        return missing("peak_plateau_or_multiple_maxima_unresolved")
    peak = float(times[int(selected[np.argmax(speed[selected])])])
    return peak, {**detail, "status": "measured", "reason": None, "peak_ms": peak}


def _peak(speed, times):
    return _observed_peak(speed, times)[0]


def _sequence(peaks, times):
    tolerance = float(np.median(np.diff(times)))
    # Decoder PTS division can put the same one-frame interval a few ulps on
    # either side of the median. Preserve the one-interval uncertainty gate.
    if any(abs(earlier - later) <= tolerance + 1e-7 for earlier, later in zip(peaks, peaks[1:])):
        return None
    disorder = sum(max(0., earlier - later - tolerance) for earlier, later in zip(peaks, peaks[1:]))
    ratio = min(1., disorder / (times[-1] - times[0]))
    return ratio, 100 * (1 - ratio)


def _direction(context, name):
    direction = _point(context.get(name), 2)
    if direction is None or context.get(name + "_verified") is not True or np.linalg.norm(direction) < 1e-8:
        return None
    return direction / np.linalg.norm(direction)


def _direction_share(points, direction):
    projected = np.diff(points, axis=0) @ direction
    total = float(np.abs(projected).sum())
    return float(np.maximum(projected, 0).sum() / total) if total >= .03 else None


def _camera_observations(rows, observations, context):
    if (context.get("coordinate_system") != "fixed_camera_image_plane" or context.get("camera_reference_verified") is not True
            or context.get("source_binding_verified") is not True):
        return None, "fixed_camera_source_reference_unverified"
    if not _finite(context.get("scale")) or context["scale"] <= 0:
        return None, "fixed_torso_scale_missing"
    sha = context.get("video_sha256")
    if not isinstance(sha, str) or len(sha) != 64 or any(c not in "0123456789abcdefABCDEF" for c in sha):
        return None, "camera_source_identity_unverified"
    if len(observations) != len(rows):
        return None, "camera_window_coverage_incomplete"
    for row, observation in zip(rows, observations):
        if not isinstance(observation, Mapping) or not _finite(observation.get("time")) or abs(observation["time"] - row["time"]) > 1.01:
            return None, "camera_source_time_mismatch"
        if observation.get("frame") != row.get("frame"):
            return None, "camera_source_frame_mismatch"
        for key in ("track", "selection_epoch", "camera_reference_epoch"):
            if type(context.get(key)) is not int or observation.get(key) != context[key]:
                return None, "camera_source_identity_or_reference_mismatch"
        source_sha = observation.get("video_sha256", sha)
        if not isinstance(source_sha, str) or source_sha.lower() != sha.lower():
            return None, "camera_source_video_mismatch"
    return list(observations), None


def analyze_motion_pattern(rows, *, prep_end, peak, side, technique=None, classification_verified=False,
                           source_observations=(), source_context=None):
    """Return score-ready metrics and explicit requirement coverage for one window.

    ``rows`` uses the assessment's time/frame/local geometry. Source observations
    must align one-for-one and contain fixed-reference 2D ``points`` plus source
    identities. Context additionally needs source_binding_verified, scale and
    camera_reference_verified. Directions each require their own *_verified flag.
    Recovery completeness is separate: recovery_end_verified never follows merely
    from detecting a follow-through phase. No file, model or report is consulted.
    """
    context = dict(source_context or {})
    verified = classification_verified is True and technique in TECHNIQUES
    base = RETURN_BASE.get(technique, technique) if verified else "generic"
    names = ["phase_continuity"]
    if verified:
        names += list(BASE_PROFILES[base]) + ["physical_chain", "racket_and_contact"]
        if technique in RETURN_BASE:
            names.append("return_compactness")
        if base == "serve":
            names.append("serve_ball_toss")
    result = {"version": VERSION, "profile_id": technique if verified else "generic",
              "classification_status": "verified" if verified else "technique_unverified",
              "metrics": {}, "metric_definitions": PATTERN_METRICS, "requirements": [], "metric_evidence": {},
              "limitations": ["固定画面内的运动投影和身体相对轨迹，不能证明真实受力、世界三维速度或触球。"]}
    reasons, evidence = {}, result["metric_evidence"]

    def put(name, value, score, detail=None):
        if _finite(float(value)) and _finite(float(score)):
            metric = SOURCE_REQUIREMENTS[name][1]
            result["metrics"][metric] = (float(value), float(np.clip(score, 0, 100)))
            evidence[metric] = detail or {}

    def finish():
        for name in names:
            label, metric, _ = SOURCE_REQUIREMENTS[name]
            measured = metric in result["metrics"] if metric else False
            result["requirements"].append({"id": name, "label": label, "status": "measured_proxy" if measured else "not_measured",
                                           "metric_ids": [metric] if metric else [],
                                           "reason": None if measured else reasons.get(name, "required_evidence_not_observed")})
        if not verified:
            result["requirements"].append({"id": "technique_classification", "label": "具体技术类型未确认", "status": "not_measured",
                                           "metric_ids": [], "reason": "no_technique_specific_rules_applied"})
        measured = sum(r["status"] == "measured_proxy" for r in result["requirements"])
        result["coverage"] = {"measured_requirements": measured, "applicable_requirements": len(result["requirements"]),
                              "fraction": measured / len(result["requirements"]), "full_source_technique_covered": False}
        return result

    if (side not in {"left", "right"} or type(prep_end) is not int or type(peak) is not int or len(rows) < 7
            or not 2 <= prep_end <= peak - 2 or not peak + 2 < len(rows)):
        reasons.update((name, "motion_phase_window_incomplete") for name in names)
        return finish()
    times = np.array([r.get("time", np.nan) for r in rows], dtype=float)
    if not np.isfinite(times).all() or np.any(np.diff(times) <= 0) or np.any(np.diff(times) > 160):
        reasons.update((name, "motion_time_discontinuous") for name in names)
        return finish()
    wrist = _series(rows, side + "_wrist")
    if wrist is not None:
        phases = []
        for start, stop in ((prep_end - 1, peak + 1), (peak, len(rows))):
            p, t = wrist[start:stop], times[start:stop]
            travel = np.linalg.norm(np.diff(p, axis=0), axis=1)
            if float(travel.sum()) < .03:
                phases.append({"fraction": 0., "duration_ms": float(t[-1] - t[0]), "active_ms": 0.})
                continue
            speed = travel / (np.diff(t) / 1000)
            floor = max(.03, .1 * float(np.quantile(speed, .95)))
            active = float(np.diff(t)[speed >= floor].sum())
            phases.append({"fraction": active / (t[-1] - t[0]), "duration_ms": float(t[-1] - t[0]), "active_ms": active})
        fraction = float(np.mean([p["fraction"] for p in phases]))
        put("phase_continuity", fraction, 100 * fraction, {"phases": dict(zip(("acceleration", "follow_through"), phases)), "deceleration_required": False})
    else:
        reasons["phase_continuity"] = "continuous_wrist_observations_missing"
    if not verified:
        return finish()
    reasons.update(physical_chain="force_ground_contact_and_true_center_of_mass_unobserved",
                   racket_and_contact="racket_ball_contact_and_grip_unobserved",
                   return_compactness="independent_incoming_ball_pressure_or_reference_unobserved",
                   serve_ball_toss="ball_release_contact_and_ground_contact_unobserved")
    camera, camera_reason = _camera_observations(rows, source_observations, context)
    if camera is None:
        reasons.update((name, camera_reason) for name in BASE_PROFILES[base])
        return finish()
    scale = context["scale"]
    hips = [_series(camera, s + "_hip", "points", 2) for s in ("left", "right")]
    center = (hips[0] + hips[1]) / (2 * scale) if all(h is not None for h in hips) else None
    hand = _series(camera, side + "_wrist", "points", 2)
    hand = hand / scale - center if hand is not None and center is not None else None
    forward, up = _direction(context, "forward_direction_2d"), _direction(context, "up_direction_2d")
    strike = slice(prep_end - 1, peak + 1)
    preparation_shoulder = _axis(camera[:prep_end], "shoulder", scale)
    # Three support observations are fixed: two for the centered five-point
    # derivative, and one more for the neighboring derivative at a phase edge.
    support_start, support_stop = max(0, prep_end - 4), min(len(rows), peak + 4)
    sequence_camera = camera[support_start:support_stop]
    shoulder, hip = _axis(sequence_camera, "shoulder", scale), _axis(sequence_camera, "hip", scale)
    for name in BASE_PROFILES[base]:
        if name != "serve_arm_sequence" and hand is None:
            reasons[name] = "body_relative_hand_or_hip_observations_missing"
    if preparation_shoulder is None:
        reasons["preparation_coupling"] = "preparation_shoulder_axis_missing_or_foreshortened"
    if "projected_sequence" in names and (shoulder is None or hip is None):
        reasons["projected_sequence"] = "acceleration_support_axes_missing_or_foreshortened"
        evidence["pattern_projected_sequence"] = {"search_range_ms": [float(times[prep_end - 1]), float(times[peak])],
            "support_range_ms": [float(times[support_start]), float(times[support_stop - 1])],
            "unobservable_axes": [name for name, value in (("hip", hip), ("shoulder", shoulder)) if value is None]}
    if context.get("preparation_start_clipped") is True:
        reasons["preparation_coupling"] = "preparation_start_clipped_by_source_boundary"
    elif "preparation_coupling" in names and hand is not None and preparation_shoulder is not None:
        angles = preparation_shoulder
        angular_path = np.r_[0., np.cumsum(np.abs(np.diff(angles)))]
        arm_progress = _progress(hand[:prep_end])
        if angular_path[-1] >= 3 and arm_progress is not None:
            agreement = 1 - _time_mean(np.abs(angular_path / angular_path[-1] - arm_progress), times[:prep_end])
            put("preparation_coupling", agreement, 100 * agreement)
        else:
            reasons["preparation_coupling"] = "preparation_rotation_or_arm_travel_below_measurement_floor"
    if "projected_sequence" in names and all(x is not None for x in (hand, hip, shoulder)):
        # Independent axes use the same fixed camera reference, without removing
        # body rotation. Only acceleration is searched; halos support derivatives.
        search = (prep_end - 1 - support_start, peak + 1 - support_start)
        domain = slice(*search)
        support_t = times[support_start:support_stop]
        t, h, s, p = times[strike], hip[domain], shoulder[domain], hand[strike]
        if np.ptp(h) >= 3 and np.ptp(s) >= 3 and _progress(p) is not None:
            speeds = [np.abs(_velocity(a[:, None], support_t)[:, 0]) for a in (hip, shoulder)]
            speeds.append(np.linalg.norm(_velocity(hand[support_start:support_stop], support_t), axis=1))
            observations = [_observed_peak(speed, support_t, search) for speed in speeds]
            peaks = [observation[0] for observation in observations]
            detail = {"peak_observations": dict(zip(("hip", "shoulder", "wrist"), (o[1] for o in observations))),
                      "timing_resolution_ms": float(np.median(np.diff(t)))}
            sequence = _sequence(peaks, t) if all(v is not None for v in peaks) else None
            if sequence is not None:
                value, score = sequence
                put("projected_sequence", value, score, {**detail, **dict(zip(("hip_peak_ms", "shoulder_peak_ms", "wrist_peak_ms"), peaks))})
            else:
                reasons["projected_sequence"] = "motion_peak_order_unresolved" if all(v is not None for v in peaks) else "motion_peak_two_sided_evidence_unresolved"
                evidence["pattern_projected_sequence"] = detail
        else:
            reasons["projected_sequence"] = "projected_motion_below_measurement_floor"
    for name, points in (("center_transfer", center), ("forward_swing", hand)):
        if name not in names:
            continue
        if forward is None:
            reasons[name] = "independent_forward_direction_unverified"
        elif points is not None:
            value = _direction_share(points[strike], forward)
            if value is not None:
                put(name, value, 100 * value)
            else:
                reasons[name] = "projected_forward_travel_below_measurement_floor"
    if "two_hand_coupling" in names and center is not None and hand is not None:
        other = _series(camera[strike], ("left" if side == "right" else "right") + "_wrist", "points", 2)
        if other is not None:
            first, second, t = hand[strike], other / scale - center[strike], times[strike]
            p, q = _progress(first), _progress(second)
            if p is not None and q is not None:
                a, b = np.diff(first, axis=0), np.diff(second, axis=0)
                norms = np.linalg.norm(a, axis=1) * np.linalg.norm(b, axis=1)
                valid = norms > 1e-10
                covered = float(np.diff(t)[valid].sum() / (t[-1] - t[0]))
                if covered >= .7:
                    cosine = np.clip(np.sum(a[valid] * b[valid], axis=1) / norms[valid], -1, 1)
                    direction = float(np.average((1 + cosine) / 2, weights=np.diff(t)[valid]))
                    progress = 1 - _time_mean(np.abs(p - q), t)
                    value = .5 * (direction + progress)
                    put("two_hand_coupling", value, 100 * value, {"direction_agreement": direction, "progress_agreement": progress, "grip_verified": False})
                else:
                    reasons["two_hand_coupling"] = "simultaneous_two_wrist_motion_coverage_insufficient"
            else:
                reasons["two_hand_coupling"] = "one_or_both_wrist_paths_below_measurement_floor"
        else:
            reasons["two_hand_coupling"] = "second_wrist_observations_missing"
    if "slice_path" in names:
        if forward is None or up is None:
            reasons["slice_path"] = "independent_forward_or_up_direction_unverified"
        elif hand is not None:
            points = hand[strike]
            f, d = _direction_share(points, forward), _direction_share(points, -up)
            singular = np.linalg.svd(np.column_stack((forward, -up)), compute_uv=False)
            condition = float(singular[0] / singular[-1]) if singular[-1] > 0 else None
            if condition is not None and not math.isfinite(condition):
                condition = None
            detail = {"forward_fraction": f, "downward_fraction": d, "racket_path_measured": False,
                      "direction_basis_condition_number": condition, "maximum_direction_basis_condition_number": 4.,
                      "direction_semantics": "fixed_image_plane_projections_not_world_components"}
            # A single projected direction cannot count twice as independently
            # observed forward and downward travel. Four is a predeclared
            # engineering error-amplification limit, not a coaching grade.
            evidence["pattern_slice_path"] = detail
            if condition is None or condition > 4. and not math.isclose(condition, 4., rel_tol=1e-12, abs_tol=0.):
                reasons["slice_path"] = "slice_joint_directions_ill_conditioned"
            elif f is not None and d is not None:
                delta = np.diff(points, axis=0)
                forward_travel = float(np.maximum(delta @ forward, 0).sum())
                down_travel = float(np.maximum(delta @ (-up), 0).sum())
                value = math.sqrt(f * d)
                if base == "backhand_slice":
                    value *= min(1., forward_travel / max(down_travel, .03))
                put("slice_path", value, 100 * value, detail)
    if "serve_upward_path" in names:
        if up is None:
            reasons["serve_upward_path"] = "independent_up_direction_unverified"
        elif hand is not None:
            value = _direction_share(hand[strike], up)
            if value is not None:
                put("serve_upward_path", value, 100 * value)
    if "serve_arm_sequence" in names:
        support_start = max(0, peak - 3)
        points = [_series(camera[support_start:], side + "_" + joint, "points", 2) for joint in ("shoulder", "elbow", "wrist")]
        reasons["serve_arm_sequence"] = "arm_points_missing_or_projected_segments_unobservable"
        if all(p is not None for p in points):
            upper, forearm = (points[1] - points[0]) / scale, (points[2] - points[1]) / scale
            if np.all(np.linalg.norm(upper, axis=1) >= .1) and np.all(np.linalg.norm(forearm, axis=1) >= .1):
                upper_angle = np.unwrap(np.arctan2(upper[:, 1], upper[:, 0]))
                elbow_angle = np.unwrap(np.arctan2(forearm[:, 1], forearm[:, 0]) - upper_angle)
                t, support_t = times[peak:], times[support_start:]
                search = (peak - support_start, len(support_t))
                angles = [np.degrees(a) for a in (upper_angle, elbow_angle)]
                reasons["serve_arm_sequence"] = "arm_rotation_below_measurement_floor"
                if all(np.ptp(a[search[0]:]) >= 3 for a in angles):
                    observations = [_observed_peak(np.abs(_velocity(a[:, None], support_t)[:, 0]), support_t, search) for a in angles]
                    peaks = [observation[0] for observation in observations]
                    detail = {"peak_observations": dict(zip(("upper_arm", "forearm_relative"), (o[1] for o in observations))),
                              "timing_resolution_ms": float(np.median(np.diff(t)))}
                    sequence = _sequence(peaks, t) if all(p is not None for p in peaks) else None
                    if sequence is not None:
                        value, score = sequence
                        put("serve_arm_sequence", value, score, {**detail, **dict(zip(("upper_arm_peak_ms", "forearm_relative_peak_ms"), peaks))})
                    else:
                        reasons["serve_arm_sequence"] = "arm_rotation_peak_order_unresolved"
                        evidence["pattern_serve_arm_sequence"] = detail
    if "one_hand_reversal" in names:
        if context.get("recovery_end_verified") is not True:
            reasons["one_hand_reversal"] = "complete_recovery_boundary_unverified"
        elif forward is None:
            reasons["one_hand_reversal"] = "independent_forward_direction_unverified"
        elif hand is not None:
            trajectory = hand[peak:] @ forward
            changes = np.diff(trajectory)
            total = float(np.abs(changes).sum())
            if total >= .03:
                turn = int(np.argmax(trajectory))
                outbound = max(0., float(trajectory[turn] - trajectory[0]))
                return_path = max(0., float(trajectory[turn] - trajectory[-1]))
                correct = float(np.maximum(changes[:turn], 0).sum() + np.maximum(-changes[turn:], 0).sum())
                value = correct / total if outbound >= .03 and return_path >= .03 else 0.
                put("one_hand_reversal", value, 100 * value, {"turn_ms": float(times[peak + turn]), "recovery_end_verified": True})
    return finish()
