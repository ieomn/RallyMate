"""Frame-backed image-plane measurements for versioned card proxy rules.

The caller supplies the complete decoder timeline for a bound action window.
Missing detections remain in the denominator. These measurements do not establish
visibility, foot contact, gaze, force, or an original Word-card grade.
"""
from __future__ import annotations

from collections.abc import Mapping, Sequence
from fractions import Fraction
import math
import re
import statistics

VERSION = "card-kinematics-v1.0.0"
TORSO = ("left_shoulder", "right_shoulder", "left_hip", "right_hip")
HIPS = ("left_hip", "right_hip")
ANKLES = ("left_ankle", "right_ankle")
SPECS = {
    "trunk_tilt_deg": (TORSO, "deg", "trunk"),
    "shoulder_axis_deg": (TORSO[:2], "deg", "axis"),
    "hip_axis_deg": (HIPS, "deg", "axis"),
    "shoulder_hip_separation_deg": (TORSO, "deg", "separation"),
    "hip_inside_ankle_support_fraction": (HIPS + ANKLES, "fraction", "support"),
    "hip_to_ankle_midpoint_torso": (HIPS + ANKLES, "torso", "offset"),
    "ankle_distance_over_hip_width": (HIPS + ANKLES, "ratio", "stance"),
    "wrist_separation_torso": (("left_wrist", "right_wrist"), "torso", "distance"),
    "head_to_shoulders_distance_torso": (("nose",) + TORSO[:2], "torso", "head"),
    **{f"{side}_{joint}_{suffix}": ((f"{side}_{a}", f"{side}_{b}", f"{side}_{c}"), "deg", kind)
       for side in ("left", "right")
       for joint, suffix, a, b, c, kind in (
           ("knee", "flexion_deg", "hip", "knee", "ankle", "flexion"),
           ("elbow", "angle_deg", "shoulder", "elbow", "wrist", "angle"))},
}
DYNAMIC_SPECS = {
    "hip_displacement_torso": (TORSO, "torso"),
    "hip_lateral_displacement_torso": (TORSO, "torso"),
    "hip_downward_displacement_torso": (TORSO, "torso"),
    "hip_speed_std_torso_s": (TORSO, "torso/s"),
    "hip_speed_drop_torso_s": (TORSO, "torso/s"),
    "wrist_speed_increase_torso_s": (TORSO + ("left_wrist", "right_wrist"), "torso/s"),
    "ankle_order_exchange_count": (TORSO + ANKLES, "count"),
    "ankle_max_displacement_torso": (TORSO + ANKLES, "torso"),
    "ankle_width_contraction_torso": (TORSO + ANKLES, "torso"),
    "ankle_stability_ratio": (TORSO + ANKLES, "ratio"),
    "head_relative_motion_range_torso": (TORSO + ("nose",), "torso"),
}


def finite(value):
    try:
        return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)
    except OverflowError:
        return False


def _integer(value):
    return type(value) is int and value >= 0


def _source_times(timeline):
    if any(not _integer(row.get("pts")) or type(row.get("time_base_num")) is not int
           or type(row.get("time_base_den")) is not int or row["time_base_num"] <= 0
           or row["time_base_den"] <= 0 for row in timeline):
        return None
    if len({(r["time_base_num"], r["time_base_den"]) for r in timeline}) != 1:
        return None
    return [Fraction(r["pts"]*r["time_base_num"]*1000, r["time_base_den"]) for r in timeline]


def _get(sample, name, default=None):
    return sample.get(name, default) if isinstance(sample, Mapping) else getattr(sample, name, default)


def _point(sample, name):
    points, confidence = _get(sample, "points", {}), _get(sample, "point_confidences", {})
    point = points.get(name) if isinstance(points, Mapping) else None
    if (not isinstance(point, (list, tuple)) or len(point) != 2
            or not all(finite(v) and abs(v) <= 1e6 for v in point)):
        return None
    c = confidence.get(name) if isinstance(confidence, Mapping) else None
    model = _get(sample, "observation_semantics") == "monocular_model_estimate"
    if c is None and not model or c is not None and (not finite(c) or not .35 <= c <= 1):
        return None
    return tuple(float(v) for v in point)


def _middle(a, b):
    return ((a[0] + b[0]) / 2, (a[1] + b[1]) / 2)


def _axis(a, b):
    if math.dist(a, b) < .18:
        return None
    return (math.degrees(math.atan2(b[1] - a[1], b[0] - a[0])) + 90) % 180 - 90


def _value(sample, joints, kind):
    points = [_point(sample, name) for name in joints]
    if any(p is None for p in points):
        return None
    if kind in {"flexion", "angle"}:
        a, b, c = points
        u, v = (a[0]-b[0], a[1]-b[1]), (c[0]-b[0], c[1]-b[1])
        lengths = math.hypot(*u), math.hypot(*v)
        if min(lengths) < .1:
            return None
        cosine = (u[0]*v[0]+u[1]*v[1]) / (lengths[0]*lengths[1])
        angle = math.degrees(math.acos(max(-1, min(1, cosine))))
        return 180-angle if kind == "flexion" else angle
    if kind == "axis":
        return _axis(*points)
    if kind == "separation":
        a, b = _axis(*points[:2]), _axis(*points[2:])
        return None if a is None or b is None else abs((a-b+90) % 180-90)
    if kind == "distance":
        return math.dist(*points)
    if kind == "head":
        return math.dist(points[0], _middle(*points[1:]))
    if kind == "trunk":
        shoulder, hip = _middle(*points[:2]), _middle(*points[2:])
        if math.dist(shoulder, hip) < .1:
            return None
        return math.degrees(math.atan2(abs(hip[0]-shoulder[0]), abs(hip[1]-shoulder[1])))
    hip, ankle = _middle(*points[:2]), _middle(*points[2:])
    if kind == "support":
        return float(min(points[2][0], points[3][0]) <= hip[0] <= max(points[2][0], points[3][0]))
    if kind == "offset":
        return hip[0]-ankle[0]
    width = math.dist(*points[:2])
    return abs(points[2][0]-points[3][0])/width if width >= .18 else None


def validate_window(samples, window, timeline):
    """Validate exact frame/time/subject binding before calculating anything."""
    reasons = []
    if (not isinstance(window, Mapping) or not finite(window.get("start_ms"))
            or not finite(window.get("end_ms")) or window["start_ms"] < 0
            or window["start_ms"] >= window["end_ms"]
            or not _integer(window.get("person_track_id")) or not _integer(window.get("selection_epoch"))
            or not isinstance(window.get("video_sha256"), str)
            or re.fullmatch(r"[0-9a-fA-F]{64}", window["video_sha256"]) is None):
        return ["invalid_window"], []
    if (not isinstance(timeline, Sequence) or isinstance(timeline, (str, bytes)) or len(timeline) < 3
            or any(not isinstance(r, Mapping) or not _integer(r.get("frame_index"))
                   or not finite(r.get("timestamp_ms")) for r in timeline)):
        return ["decoder_timeline_missing"], []
    if (timeline[0]["timestamp_ms"] != window["start_ms"] or timeline[-1]["timestamp_ms"] != window["end_ms"]
            or any(b["frame_index"] != a["frame_index"]+1 or b["timestamp_ms"] <= a["timestamp_ms"]
                   for a, b in zip(timeline, timeline[1:]))):
        return ["decoder_timeline_not_complete_or_monotonic"], []
    exact = _source_times(timeline)
    if exact is None or any(b <= a for a, b in zip(exact, exact[1:])):
        return ["decoder_pts_missing_or_nonmonotonic"], []
    offsets = [value-Fraction(str(row["timestamp_ms"])) for value, row in zip(exact, timeline)]
    if max(offsets)-min(offsets) > Fraction(102, 100):
        return ["decoder_video_clock_origin_changed"], []
    times = [float(b-a) for a, b in zip(exact, exact[1:])]
    frames = [b["frame_index"]-a["frame_index"] for a, b in zip(timeline, timeline[1:])]
    if max(times) > min(160, statistics.median(times)*1.8) or max(frames) > statistics.median(frames)*1.8:
        reasons.append("decoder_timeline_discontinuous")
    if exact[-1]-exact[0] < 80:
        reasons.append("window_too_short")
    expected, observed = {r["frame_index"]: r["timestamp_ms"] for r in timeline}, {}
    source_rows = {r["frame_index"]: r for r in timeline}
    for sample in samples:
        frame = _get(sample, "frame_index")
        if not _integer(frame) or frame not in expected or frame in observed:
            reasons.append("unexpected_or_duplicate_source_frame")
            continue
        observed[frame] = sample
        if (_get(sample, "time") != expected[frame] or _get(sample, "track") != window["person_track_id"]
                or _get(sample, "selection_epoch") != window["selection_epoch"]):
            reasons.append("frame_time_or_identity_mismatch")
        if (_get(sample, "timestamp_source") != "decoder_pts"
                or _get(sample, "normalization_basis") != "full_torso"):
            reasons.append("source_time_or_normalization_unverified")
        if (_get(sample, "camera_reference_epoch") != window.get("camera_reference_epoch")
                or _get(sample, "camera_compensated", False) != window.get("camera_compensated", False)):
            reasons.append("camera_reference_changed")
        binding = _get(sample, "source_binding", {})
        if (not isinstance(binding, Mapping) or not isinstance(binding.get("video_sha256"), str)
                or binding["video_sha256"].lower() != window["video_sha256"].lower()):
            reasons.append("video_identity_mismatch")
        if _get(sample, "observation_semantics") == "monocular_model_estimate":
            if (not isinstance(binding, Mapping) or not isinstance(binding.get("video_sha256"), str)
                    or len(binding["video_sha256"]) != 64
                    or any(binding.get(key) != value for key, value in {
                        "frame_index": frame, "person_track_id": window["person_track_id"],
                        "selection_epoch": window["selection_epoch"],
                        "camera_reference_epoch": window.get("camera_reference_epoch")}.items())
                    or not _integer(binding.get("pts"))
                    or type(binding.get("time_base_num")) is not int or binding["time_base_num"] <= 0
                    or type(binding.get("time_base_den")) is not int or binding["time_base_den"] <= 0):
                reasons.append("model_source_binding_unverified")
            elif any(binding[key] != source_rows[frame][key] for key in ("pts", "time_base_num", "time_base_den")):
                reasons.append("model_decoder_pts_mismatch")
    return sorted(set(reasons)), [observed.get(row["frame_index"]) for row in timeline]


def _reference_point(sample, name):
    if _point(sample, name) is None:
        return None
    points = _get(sample, "reference_points", {})
    point = points.get(name) if isinstance(points, Mapping) else None
    return tuple(point) if (isinstance(point, (tuple, list)) and len(point) == 2
                            and all(finite(v) for v in point)) else None


def _endpoint(points, count):
    return tuple(statistics.median(p[axis] for p in points[:count]) for axis in (0, 1)), \
           tuple(statistics.median(p[axis] for p in points[-count:]) for axis in (0, 1))


def _speed(points, times, scale):
    return [math.dist(a, b)*1000/(dt*scale) for a, b, dt in zip(points, points[1:], times)]


def _dynamic_value(name, samples, window, exact_times):
    if len(samples) < 7 or any(s is None for s in samples):
        return None, "continuous_endpoint_window_missing"
    joints = DYNAMIC_SPECS[name][0]
    if name == "head_relative_motion_range_torso":
        points = [[_point(s, j) for j in joints] for s in samples]
        if any(p is None for row in points for p in row):
            return None, "required_joint_missing_in_continuous_window"
        local = [(row[-1][0]-_middle(*row[:2])[0], row[-1][1]-_middle(*row[:2])[1]) for row in points]
        return max(math.dist(a, b) for i, a in enumerate(local) for b in local[i:]), None
    if not window.get("camera_compensated") or not _integer(window.get("camera_reference_epoch")):
        return None, "fixed_camera_reference_required"
    points = [{j: _reference_point(s, j) for j in joints} for s in samples]
    if any(p is None for row in points for p in row.values()):
        return None, "reference_joint_missing_in_continuous_window"
    scales = [math.dist(_middle(*(r[j] for j in TORSO[:2])), _middle(*(r[j] for j in HIPS))) for r in points]
    scale = statistics.median(scales)
    if not finite(scale) or scale <= 0:
        return None, "fixed_torso_scale_unavailable"
    count = max(3, math.ceil(len(samples)*.2))
    times = [float(b-a) for a, b in zip(exact_times, exact_times[1:])]
    if name.startswith("hip_"):
        path = [_middle(*(r[j] for j in HIPS)) for r in points]
        a, b = _endpoint(path, count)
        speeds = _speed(path, times, scale)
        if name == "hip_displacement_torso":
            return math.dist(a, b)/scale, None
        if name == "hip_lateral_displacement_torso":
            return abs(b[0]-a[0])/scale, None
        if name == "hip_downward_displacement_torso":
            return (b[1]-a[1])/scale, None
        if name == "hip_speed_std_torso_s":
            return statistics.pstdev(speeds), None
        return statistics.median(speeds[:count])-statistics.median(speeds[-count:]), None
    if name == "wrist_speed_increase_torso_s":
        side = window.get("racket_side")
        if side not in {"left", "right"}:
            return None, "racket_side_unresolved"
        speeds = _speed([r[f"{side}_wrist"] for r in points], times, scale)
        return statistics.median(speeds[-count:])-statistics.median(speeds[:count]), None
    cycle = window.get("step_cycle_binding", {})
    if (not isinstance(cycle, Mapping) or cycle.get("method") not in {"manual_anchor", "versioned_detector"}
            or not isinstance(cycle.get("version"), str) or not cycle["version"]
            or not all(finite(cycle.get(k)) for k in ("depart_ms", "widest_ms", "return_ms"))
            or not cycle["depart_ms"] == window["start_ms"] < cycle["widest_ms"] < window["end_ms"] == cycle["return_ms"]):
        return None, "complete_step_cycle_not_bound"
    left, right = [[r[j] for r in points] for j in ANKLES]
    if name == "ankle_order_exchange_count":
        signs = [1 if a[0]-b[0] > .08*scale else -1 if a[0]-b[0] < -.08*scale else 0 for a, b in zip(left, right)]
        visible = [v for v in signs if v]
        return sum(a != b for a, b in zip(visible, visible[1:])), None
    if name == "ankle_max_displacement_torso":
        return max(math.dist(*_endpoint(p, count)) for p in (left, right))/scale, None
    if name == "ankle_width_contraction_torso":
        widths = [math.dist(a, b)/scale for a, b in zip(left, right)]
        interior = [w for s, w in zip(samples, widths) if cycle["depart_ms"] < _get(s, "time") < cycle["return_ms"]]
        return (max(interior)-statistics.median(widths[-count:]), None) if interior else (None, "step_interior_missing")
    paths = [sum(math.dist(a, b) for a, b in zip(p, p[1:]))/scale for p in (left, right)]
    return (min(paths)/max(paths), None) if max(paths) >= .1 else (None, "ankle_path_too_small")


def measure_card_window(samples, window, timeline, *, required_joint_names=()):
    """Compute registered metrics; partial frames never create endpoint deltas."""
    reasons, aligned = validate_window(samples, window, timeline)
    # A compound rule uses the same valid physical frames for every condition.
    # The original decoder timeline remains the denominator after this mask.
    aligned = [s if s is not None and all(_point(s, name) is not None for name in required_joint_names) else None
               for s in aligned]
    output = {}
    for name, (joints, unit, kind) in SPECS.items():
        values = [_value(s, joints, kind) if s is not None else None for s in aligned] if not reasons else []
        valid = [v for v in values if v is not None]
        total, count = len(timeline), len(valid)
        complete = count == total and total >= 3
        passes = count*10 >= total*7 and total >= 3 and not reasons
        row = {"feature_name": name, "feature_version": VERSION, "unit": unit,
               "required_joint_names": list(joints), "total_frame_count": total,
               "valid_frame_count": count, "fraction": count/total if total else None,
               "geometry_gate_passed": passes, "visibility_verified": False,
               "image_bounds_verified": False,
               "denominator": "all_decoder_samples_in_bound_stage",
               "source_frames": [r["frame_index"] for r in timeline],
               "valid_source_frames": [r["frame_index"] for r, v in zip(timeline, values) if v is not None],
               "reason_codes": list(reasons), "statistics": {}, "semantics": "image_plane_visual_proxy"}
        if not passes:
            row["reason_codes"].append("required_joint_geometry_below_70_percent")
        elif kind == "axis" and not complete:
            row["reason_codes"].append("axis_unwrap_requires_continuous_window")
        else:
            if kind == "axis":
                unwrapped = [valid[0]]
                for a, b in zip(valid, valid[1:]):
                    unwrapped.append(unwrapped[-1]+(b-a+90) % 180-90)
                valid = unwrapped
            row["statistics"] = {"mean": statistics.fmean(valid), "median": statistics.median(valid),
                                 "range": max(valid)-min(valid), "min": min(valid), "max": max(valid)}
            if complete and total >= 6:
                endpoint_count = max(3, math.ceil(total*.2))
                start, end = statistics.median(valid[:endpoint_count]), statistics.median(valid[-endpoint_count:])
                row["statistics"].update(start=start, end=end, delta=end-start, delta_abs=abs(end-start))
            else:
                row["reason_codes"].append("partial_window_cannot_establish_change")
        output[name] = row
    for name, (joints, unit) in DYNAMIC_SPECS.items():
        value, reason = _dynamic_value(name, aligned, window, _source_times(timeline)) if not reasons else (None, reasons[0])
        if not finite(value):
            value = None
        valid = value is not None
        output[name] = {"feature_name": name, "feature_version": VERSION, "unit": unit,
                        "required_joint_names": list(joints), "geometry_gate_passed": valid,
                        "valid_frame_count": len(timeline) if valid else 0, "total_frame_count": len(timeline),
                        "source_frames": [r["frame_index"] for r in timeline],
                        "statistics": {"value": value} if valid else {}, "visibility_verified": False,
                        "image_bounds_verified": False,
                        "reason_codes": [] if valid else [reason or "invalid_computed_value"],
                        "semantics": "image_plane_visual_proxy"}
    names = {joint for spec in list(SPECS.values()) + list(DYNAMIC_SPECS.values()) for joint in spec[0]}
    valid_frames = {name: [r["frame_index"] for r, s in zip(timeline, aligned)
                          if not reasons and s is not None and _point(s, name) is not None] for name in names}
    return {"version": VERSION, "window": dict(window), "reason_codes": reasons, "measurements": output,
            "joint_valid_frames": valid_frames}
