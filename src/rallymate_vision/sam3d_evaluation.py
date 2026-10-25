"""Uncalibrated SAM 3D diagnostics on source-bound frames.

The official estimator returns projected and 3D keypoints, but no keypoint
confidence. These functions deliberately do not construct PoseBackendOutput or
feed its confidence-gated scoring path. Stability and agreement are diagnostics,
not pose accuracy, coach scores, or permission to promote a model.
"""
from __future__ import annotations

from collections import Counter
from collections.abc import Iterable, Mapping
from fractions import Fraction
import math
import statistics
from typing import Any


VERSION = "sam3d-evaluation-v1"
FRAME_VERSION = "sam3d-frame-v1"
COORDINATES_2D = "image_pixels_x_right_y_down"
COORDINATES_3D = "sam3d_pred_keypoints_3d"
RTMPOSE_RAW_SCORE_SEMANTICS = "rtmpose_native_heatmap_score_not_calibrated_probability"
RTMPOSE_SCORE_NORMALIZATION = "finite_nonnegative_response_clipped_0_1_for_threshold_only_raw_unchanged"
CORE_JOINTS = tuple(f"{side}_{joint}" for side in ("left", "right")
                    for joint in ("shoulder", "elbow", "wrist", "hip", "knee", "ankle"))
BONES = {f"{side}_{part}": (f"{side}_{a}", f"{side}_{b}")
         for side in ("left", "right")
         for part, a, b in (("upper_arm", "shoulder", "elbow"),
                           ("forearm", "elbow", "wrist"),
                           ("thigh", "hip", "knee"), ("shin", "knee", "ankle"))}
MEASUREMENTS = {
    **{f"{side}_knee_flexion_deg": (f"{side}_hip", f"{side}_knee", f"{side}_ankle")
       for side in ("left", "right")},
    **{f"{side}_elbow_angle_deg": (f"{side}_shoulder", f"{side}_elbow", f"{side}_wrist")
       for side in ("left", "right")},
    "shoulder_hip_axis_angle_deg": ("left_shoulder", "right_shoulder", "left_hip", "right_hip"),
    "ankle_distance_over_hip_width": ("left_ankle", "right_ankle", "left_hip", "right_hip"),
}
_BINDING_FIELDS = ("video_sha256", "frame_index", "pts", "time_base_num", "time_base_den",
                   "timestamp_ms", "person_track_id", "selection_epoch", "camera_reference_epoch")


def _number(value: Any) -> bool:
    try:
        return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)
    except OverflowError:
        return False


def _integer(value: Any, minimum: int | None = None) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and (minimum is None or value >= minimum)


def _points(value: Any, dimensions: int) -> dict[str, tuple[float, ...]]:
    if not isinstance(value, Mapping):
        return {}
    return {name: tuple(float(v) for v in point) for name, point in value.items()
            if name in CORE_JOINTS and isinstance(point, (list, tuple)) and len(point) == dimensions
            and all(_number(v) for v in point)}


def _stats(values: Iterable[float]) -> dict:
    values = sorted(float(value) for value in values if _number(value))
    if not values:
        return {"count": 0, "mean": None, "median": None, "p95": None, "max": None}
    position = (len(values) - 1) * .95
    lower = math.floor(position)
    upper = math.ceil(position)
    return {"count": len(values), "mean": statistics.fmean(values),
            "median": statistics.median(values), "p95": values[lower] +
            (values[upper] - values[lower]) * (position - lower), "max": values[-1]}


def _time(record: Mapping) -> Fraction:
    return Fraction(record["pts"] * record["time_base_num"], record["time_base_den"])


def _binding_error(record: Any) -> str | None:
    if not isinstance(record, Mapping) or record.get("schema_version") != FRAME_VERSION:
        return "invalid_frame_schema"
    sha = record.get("video_sha256")
    if not isinstance(sha, str) or len(sha) != 64 or any(c not in "0123456789abcdefABCDEF" for c in sha):
        return "invalid_video_identity"
    if not _integer(record.get("frame_index"), 0):
        return "invalid_source_frame_index"
    track = record.get("person_track_id")
    if (isinstance(track, bool) or not isinstance(track, (str, int)) or
            isinstance(track, str) and not track.strip() or isinstance(track, int) and track < 0):
        return "missing_subject_identity"
    if any(not _integer(record.get(field), 0) for field in ("selection_epoch", "camera_reference_epoch")):
        return "missing_reference_epoch"
    if not (_integer(record.get("pts")) and _integer(record.get("time_base_num"), 1)
            and _integer(record.get("time_base_den"), 1) and _number(record.get("timestamp_ms"))):
        return "original_pts_missing_or_invalid"
    try:
        matching_time = abs(float(_time(record) * 1000) - record["timestamp_ms"]) <= .51
    except OverflowError:
        matching_time = False
    if not matching_time:
        return "timestamp_does_not_match_original_pts"
    size = record.get("image_size")
    if not isinstance(size, (list, tuple)) or len(size) != 2 or not all(_integer(v, 1) for v in size):
        return "invalid_image_size"
    if record.get("status") not in {"ok", "failed", "skipped"}:
        return "invalid_inference_status"
    return None


def _reference(record: Mapping) -> dict:
    return {"video_sha256": record["video_sha256"].lower(),
            **{field: record[field] for field in ("person_track_id", "selection_epoch", "camera_reference_epoch")},
            "image_size": list(record["image_size"]),
            "coordinate_system_2d": record.get("coordinate_system_2d"),
            "coordinate_system_3d": record.get("coordinate_system_3d"),
            "units_3d": record.get("units_3d")}


def normalized_baseline_score(value: Any, *, raw_rtmpose_response: bool = False) -> float | None:
    """Follow the RTMPose adapter's score clipping only for its declared response contract.

    This is threshold bookkeeping, not probability calibration or a SAM score.
    Unknown scores retain the bounded legacy contract; negative/nonfinite scores
    remain invalid in either contract. The source value is never modified.
    """
    if not _number(value) or value < 0 or (value > 1 and not raw_rtmpose_response):
        return None
    return min(1.0, float(value))


def _raw_rtmpose_baseline(baseline: Mapping) -> bool:
    # benchmark_official's original embedded baseline omitted score semantics;
    # this exact model label plus its verified source binding identifies that
    # export contract. Explicit contradictory declarations never inherit it.
    if baseline.get("model") != "rtmpose_rerun_on_same_manifest" or not isinstance(baseline.get("source_binding"), Mapping):
        return False
    return baseline.get("confidence_semantics", RTMPOSE_RAW_SCORE_SEMANTICS) == RTMPOSE_RAW_SCORE_SEMANTICS


def _baseline(record: Mapping, min_confidence: float) -> tuple[dict, str | None]:
    baseline = record.get("baseline")
    if not isinstance(baseline, Mapping):
        return {}, "baseline_missing"
    if baseline.get("provenance") != "same_frame_existing_pose":
        return {}, "baseline_same_frame_provenance_missing"
    # An embedded baseline inherits this envelope. If separately supplied source
    # fields exist, every field must match rather than silently trusting the label.
    binding = baseline.get("source_binding")
    if binding is not None:
        if not isinstance(binding, Mapping) or any(binding.get(field) != record.get(field) for field in _BINDING_FIELDS):
            return {}, "baseline_source_binding_mismatch"
        if "image_size" in binding and binding["image_size"] != record["image_size"]:
            return {}, "baseline_source_binding_mismatch"
    if baseline.get("coordinate_system_2d", COORDINATES_2D) != COORDINATES_2D:
        return {}, "baseline_coordinates_not_image_pixels"
    points = _points(baseline.get("keypoints_2d"), 2)
    scores = baseline.get("keypoint_scores")
    if not isinstance(scores, Mapping):
        return {}, "baseline_keypoint_scores_missing"
    raw_response = _raw_rtmpose_baseline(baseline)
    points = {name: point for name, point in points.items()
              if (score := normalized_baseline_score(scores.get(name), raw_rtmpose_response=raw_response)) is not None
              and score >= min_confidence}
    return points, None if points else "baseline_no_eligible_keypoints"


def _vector(a, b):
    return tuple(x - y for x, y in zip(a, b))


def _norm(vector):
    return math.hypot(*vector)


def _angle(u, v):
    a, b = _norm(u), _norm(v)
    if not math.isfinite(a + b) or min(a, b) <= 1e-9:
        return None
    cosine = sum((x / a) * (y / b) for x, y in zip(u, v))
    return math.degrees(math.acos(max(-1, min(1, cosine))))


def _measurement(points: Mapping, name: str, joints: tuple[str, ...]):
    if any(joint not in points for joint in joints):
        return None, "required_joint_not_finite"
    p = [points[joint] for joint in joints]
    if name == "ankle_distance_over_hip_width":
        width = math.dist(p[2], p[3])
        value = math.dist(p[0], p[1]) / width if width > 1e-9 else None
    elif name == "shoulder_hip_axis_angle_deg":
        value = _angle(_vector(p[1], p[0]), _vector(p[3], p[2]))
    else:
        value = _angle(_vector(p[0], p[1]), _vector(p[2], p[1]))
        if value is not None and "knee_flexion" in name:
            value = 180 - value
    return (value, None) if _number(value) else (None, "degenerate_geometry")


def measure_sam3d_frame(record: Mapping) -> dict:
    """Return joint-derived 3D estimates with identity; never emit technique scores."""
    reason = _binding_error(record)
    if reason is None and record["status"] != "ok":
        reason = "inference_not_successful"
    if reason is None and (record.get("coordinate_system_3d") != COORDINATES_3D or record.get("units_3d") != "model_units"):
        reason = "unsupported_3d_coordinate_reference"
    points = _points(record.get("keypoints_3d"), 3) if not reason else {}
    values = {}
    for name, joints in MEASUREMENTS.items():
        value, missing = _measurement(points, name, joints) if not reason else (None, reason)
        values[name] = {"value": value, "unit": "ratio" if name.endswith("hip_width") else "deg",
                        "status": "estimated" if value is not None else "unavailable", "reason": missing,
                        "joint_names": list(joints), "score": None, "grade": None}
    return {**{field: record.get(field) for field in _BINDING_FIELDS}, "measurements": values,
            "confidence": None, "confidence_semantics": "not_provided_by_sam3d",
            "coordinate_system_3d": record.get("coordinate_system_3d"), "units_3d": record.get("units_3d"),
            "measurement_scope": "monocular_model_estimate_not_ground_truth",
            "word_grade_eligible": False, "source_stage_confirmed": False}


def _break_reason(previous: Mapping, current: Mapping, max_gap_ms: float, max_frame_gap: int):
    if _reference(previous) != _reference(current):
        return "subject_video_or_coordinate_reference_changed"
    gap = current["frame_index"] - previous["frame_index"]
    if not 0 < gap <= max_frame_gap:
        return "source_frame_gap_or_nonincreasing_order"
    delta_ms = float((_time(current) - _time(previous)) * 1000)
    if not 0 < delta_ms <= max_gap_ms:
        return "pts_gap_or_nonincreasing_order"
    return None


def _temporal(windows: list[list[dict]], field: str, *, reversal_min: float, reversal_cosine: float) -> dict:
    """Per-joint, time-weighted diagnostics; a missing joint breaks its own series."""
    result = {}
    for joint in CORE_JOINTS:
        speeds, accelerations, residuals = [], [], []
        eligible_reversals, reversal_count = 0, 0
        for window in windows:
            for previous, current in zip(window, window[1:]):
                a, b = previous[field].get(joint), current[field].get(joint)
                if a is not None and b is not None:
                    dt = float(_time(current["record"]) - _time(previous["record"]))
                    speeds.append(math.dist(a, b) / dt)
            for first, middle, last in zip(window, window[1:], window[2:]):
                a, b, c = (item[field].get(joint) for item in (first, middle, last))
                if any(point is None for point in (a, b, c)):
                    continue
                dt1 = float(_time(middle["record"]) - _time(first["record"]))
                dt2 = float(_time(last["record"]) - _time(middle["record"]))
                before, after = _vector(b, a), _vector(c, b)
                speed1, speed2 = tuple(v / dt1 for v in before), tuple(v / dt2 for v in after)
                accelerations.append(_norm(_vector(speed2, speed1)) / ((dt1 + dt2) / 2))
                prediction = tuple(x + (z - x) * dt1 / (dt1 + dt2) for x, z in zip(a, c))
                residuals.append(math.dist(b, prediction))
                length1, length2 = _norm(before), _norm(after)
                if min(length1, length2) >= reversal_min and min(length1, length2) > 0:
                    eligible_reversals += 1
                    cosine = sum((x / length1) * (y / length2) for x, y in zip(before, after))
                    reversal_count += cosine <= reversal_cosine
        result[joint] = {"speed": _stats(speeds), "acceleration": _stats(accelerations),
                         "linear_interpolation_residual": _stats(residuals),
                         "reversal_eligible_triples": eligible_reversals, "reversal_count": reversal_count}
    return {"joint_statistics": result,
            "interpretation": "motion_variation_includes_real_actions_not_estimation_error",
            "residual_definition": "middle_point_distance_from_time_weighted_neighbor_interpolation",
            "reversal_definition": {"cosine_at_most": reversal_cosine, "both_displacements_at_least": reversal_min},
            "score": None}


def _bone_stability(windows: list[list[dict]]) -> list[dict]:
    output = []
    for window in windows:
        for name, joints in BONES.items():
            # No bridging missing joints, even if other points make the frame usable.
            segments, current = [], []
            for item in window:
                points = item["points3d"]
                length = math.dist(points[joints[0]], points[joints[1]]) if all(j in points for j in joints) else None
                if length is None or not math.isfinite(length) or length <= 1e-9:
                    if current:
                        segments.append(current)
                    current = []
                else:
                    current.append((item["record"], length))
            if current:
                segments.append(current)
            for segment in segments:
                if len(segment) < 2:
                    continue
                lengths = [pair[1] for pair in segment]
                output.append({"bone": name, "joint_names": list(joints),
                               "reference": _reference(segment[0][0]),
                               "start_frame_index": segment[0][0]["frame_index"],
                               "end_frame_index": segment[-1][0]["frame_index"],
                               "sample_count": len(lengths), "unit": "model_units",
                               "length": _stats(lengths),
                               "coefficient_of_variation": statistics.pstdev(lengths) / statistics.fmean(lengths),
                               "interpretation": "model_skeleton_consistency_not_anatomical_accuracy"})
    return output


def _normalized_3d(windows: list[list[dict]]) -> list[dict]:
    """Remove estimated root translation, using one fixed scale per window."""
    scales = []
    for window in windows:
        torso_lengths = []
        for item in window:
            points = item["points3d"]
            if all(name in points for name in ("left_shoulder", "right_shoulder", "left_hip", "right_hip")):
                shoulders = tuple((a + b) / 2 for a, b in zip(points["left_shoulder"], points["right_shoulder"]))
                hips = tuple((a + b) / 2 for a, b in zip(points["left_hip"], points["right_hip"]))
                length = math.dist(shoulders, hips)
                if math.isfinite(length) and length > 1e-9:
                    torso_lengths.append(length)
        scale = statistics.median(torso_lengths) if torso_lengths else None
        for item in window:
            points = item["points3d"]
            item["sam3d_normalized"] = {}
            if scale is not None and all(name in points for name in ("left_hip", "right_hip")):
                hip = tuple((a + b) / 2 for a, b in zip(points["left_hip"], points["right_hip"]))
                item["sam3d_normalized"] = {name: tuple((x - y) / scale for x, y in zip(point, hip))
                                            for name, point in points.items()}
        scales.append({"reference": _reference(window[0]["record"]),
                       "start_frame_index": window[0]["record"]["frame_index"],
                       "end_frame_index": window[-1]["record"]["frame_index"],
                       "median_torso_model_units": scale, "scale_sample_count": len(torso_lengths)})
    return scales


def evaluate_sam3d_records(records: Iterable[Mapping], *, max_gap_ms: float = 160,
                          max_frame_gap: int = 1, baseline_min_confidence: float = .35,
                          reversal_min_displacement_image_diagonal: float = .002,
                          reversal_min_displacement_torso: float = .02,
                          reversal_cosine: float = -.5) -> dict:
    """Evaluate paired records without changing production model or scoring rules.

    ``baseline`` is the existing pose on the very same decoded frame, in pixels.
    Reversal cutoffs are explicit diagnostic parameters, not Word thresholds.
    Warm-up/model-load timing must be reported separately by the caller, never
    silently mixed into ``inference_ms``. No pass/fail accuracy verdict exists.
    """
    if not _number(max_gap_ms) or max_gap_ms <= 0 or not _integer(max_frame_gap, 1):
        raise ValueError("continuity limits must be positive")
    if not _number(baseline_min_confidence) or not 0 <= baseline_min_confidence <= 1:
        raise ValueError("baseline confidence threshold must be in [0,1]")
    if (not _number(reversal_min_displacement_image_diagonal) or reversal_min_displacement_image_diagonal < 0
            or not _number(reversal_min_displacement_torso) or reversal_min_displacement_torso < 0
            or not _number(reversal_cosine) or not -1 <= reversal_cosine <= 1):
        raise ValueError("reversal diagnostic parameters are invalid")
    records = list(records)
    errors = [_binding_error(record) for record in records]
    # Repeated source frame under the same identity cannot be counted twice or
    # selected opportunistically for a more favorable comparison.
    identities = Counter((record["video_sha256"].lower(), record["frame_index"], record["person_track_id"],
                          record["selection_epoch"], record["camera_reference_epoch"])
                         for record, error in zip(records, errors) if error is None)
    for index, (record, error) in enumerate(zip(records, errors)):
        if error is None and identities[(record["video_sha256"].lower(), record["frame_index"], record["person_track_id"],
                                        record["selection_epoch"], record["camera_reference_epoch"])] > 1:
            errors[index] = "duplicate_source_frame_binding"
    counts, reasons, boundaries = Counter(), Counter(), Counter()
    coverage2d, coverage3d, baseline_coverage = Counter(), Counter(), Counter()
    distances_px, distances_normalized, performance, memory = [], [], [], []
    per_joint_distances = {joint: [] for joint in CORE_JOINTS}
    windows, current, measurements = [], [], []
    for index, (record, error) in enumerate(zip(records, errors)):
        if isinstance(record, Mapping):
            counts[f"status_{record.get('status', 'invalid')}"] += 1
            for field, target in (("inference_ms", performance), ("peak_memory_bytes", memory)):
                if _number(record.get(field)) and record[field] >= 0:
                    target.append(float(record[field]))
        if error:
            reasons[error] += 1
            counts["invalid_binding"] += 1
            if current:
                windows.append(current)
                current = []
            boundaries[error] += 1
            continue
        counts["valid_binding"] += 1
        # Baseline coverage is independent of SAM success. Restricting it to
        # paired outputs would hide SAM dropouts and undercount the baseline.
        baseline, baseline_error = _baseline(record, baseline_min_confidence)
        if baseline_error:
            reasons[baseline_error] += 1
        baseline_coverage.update(baseline.keys())
        counts["baseline_frames_with_eligible_points"] += bool(baseline)
        if record["status"] != "ok":
            reasons[f"inference_{record['status']}"] += 1
            if current:
                windows.append(current)
                current = []
            boundaries[f"inference_{record['status']}"] += 1
            continue
        points2d = _points(record.get("keypoints_2d"), 2) if record.get("coordinate_system_2d") == COORDINATES_2D else {}
        points3d = (_points(record.get("keypoints_3d"), 3)
                    if record.get("coordinate_system_3d") == COORDINATES_3D and record.get("units_3d") == "model_units" else {})
        if record.get("coordinate_system_2d") != COORDINATES_2D:
            reasons["unsupported_2d_coordinate_reference"] += 1
        if record.get("coordinate_system_3d") != COORDINATES_3D or record.get("units_3d") != "model_units":
            reasons["unsupported_3d_coordinate_reference"] += 1
        if not points2d and not points3d:
            reasons["no_finite_supported_keypoints"] += 1
            if current:
                windows.append(current)
                current = []
            boundaries["no_finite_supported_keypoints"] += 1
            continue
        counts["frames_with_any_model_geometry"] += 1
        counts["frames_with_all_core_2d"] += len(points2d) == len(CORE_JOINTS)
        counts["frames_with_all_core_3d"] += len(points3d) == len(CORE_JOINTS)
        coverage2d.update(points2d.keys())
        coverage3d.update(points3d.keys())
        width, height = record["image_size"]
        counts["core_2d_inside_image"] += sum(0 <= x < width and 0 <= y < height for x, y in points2d.values())
        counts["core_2d_finite"] += len(points2d)
        diagonal = math.hypot(width, height)
        common = points2d.keys() & baseline.keys()
        counts["paired_frames"] += bool(common)
        for joint in common:
            distance = math.dist(points2d[joint], baseline[joint])
            distances_px.append(distance)
            distances_normalized.append(distance / diagonal)
            per_joint_distances[joint].append(distance / diagonal)
        measurements.append(measure_sam3d_frame(record))
        item = {"record": record, "points3d": points3d,
                "sam2d": {name: tuple(v / diagonal for v in point) for name, point in points2d.items()},
                "sam2d_paired": {name: tuple(v / diagonal for v in points2d[name]) for name in common},
                "baseline2d_paired": {name: tuple(v / diagonal for v in baseline[name]) for name in common}}
        if current:
            reason = _break_reason(current[-1]["record"], record, max_gap_ms, max_frame_gap)
            if reason:
                boundaries[reason] += 1
                windows.append(current)
                current = []
        current.append(item)
    if current:
        windows.append(current)
    total = len(records)
    coverage = {name: {"finite_2d": coverage2d[name], "finite_3d": coverage3d[name],
                       "baseline_confidence_eligible_2d": baseline_coverage[name],
                       "baseline_confidence_eligible_2d_fraction": baseline_coverage[name] / total if total else None,
                       "finite_2d_fraction": coverage2d[name] / total if total else None,
                       "finite_3d_fraction": coverage3d[name] / total if total else None}
                for name in CORE_JOINTS}
    temporal = {name: _temporal(windows, field, reversal_min=reversal_min_displacement_image_diagonal,
                               reversal_cosine=reversal_cosine)
                for name, field in (("sam_2d_all_finite", "sam2d"), ("sam_2d_paired", "sam2d_paired"),
                                    ("baseline_2d_paired", "baseline2d_paired"))}
    scales3d = _normalized_3d(windows)
    temporal3d = _temporal(windows, "sam3d_normalized", reversal_min=reversal_min_displacement_torso,
                           reversal_cosine=reversal_cosine)
    return {"schema_version": VERSION,
            "status": "diagnostic_only" if counts["frames_with_any_model_geometry"] else "not_executed_no_valid_outputs",
            "promotion_allowed": False, "accuracy_validated": False, "ground_truth_used": False,
            "technical_score": None, "word_grade": None,
            "limitations_zh": ["无人工或测量真值，覆盖率、模型一致性和稳定性不能证明准确度。",
                               "SAM 3D 未提供逐点置信度；有限坐标只表示有模型估计，不表示该关节在画面可见。",
                               "单目三维为模型估计；未建立球场坐标、重力、接触时刻或 Word 阶段，不生成等级或百分制。",
                               "骨长稳定也可能来自人体模型约束，不能单独证明关节位置正确。",
                               "真实挥拍也会产生加速度及反向动作，诊断计数不能直接认定为跟踪错误。"],
            "counts": {"input_records": total, **{name: counts[name] for name in (
                "status_ok", "status_failed", "status_skipped", "invalid_binding", "valid_binding",
                "frames_with_any_model_geometry", "frames_with_all_core_2d", "frames_with_all_core_3d",
                "core_2d_finite", "core_2d_inside_image", "paired_frames", "baseline_frames_with_eligible_points")}},
            "coverage": {"denominator": total, "scope": "all_requested_records_including_failed_skipped_invalid",
                         "semantics": "finite_model_output_not_visibility_or_accuracy", "joints": coverage},
            "reason_counts": dict(reasons),
            "same_frame_2d_comparison": {"interpretation": "model_disagreement_not_accuracy_error",
                "binding": "embedded_same_source_frame_subject_envelope_with_optional_exact_source_binding",
                "baseline_min_confidence": baseline_min_confidence, "sam_confidence": None,
                "rtmpose_raw_score_normalization": RTMPOSE_SCORE_NORMALIZATION,
                "baseline_raw_response_scope": "only_source_bound_rtmpose_rerun_on_same_manifest_export",
                "distance_px": _stats(distances_px), "distance_image_diagonal": _stats(distances_normalized),
                "per_joint_distance_image_diagonal": {name: _stats(values) for name, values in per_joint_distances.items()}},
            "continuity": {"max_gap_ms": max_gap_ms, "max_frame_gap": max_frame_gap, "boundary_counts": dict(boundaries),
                "windows": [{"reference": _reference(window[0]["record"]), "sample_count": len(window),
                             "start_frame_index": window[0]["record"]["frame_index"],
                             "end_frame_index": window[-1]["record"]["frame_index"],
                             "start_pts": window[0]["record"]["pts"], "end_pts": window[-1]["record"]["pts"],
                             "start_timestamp_ms": window[0]["record"]["timestamp_ms"],
                             "end_timestamp_ms": window[-1]["record"]["timestamp_ms"]} for window in windows]},
            "temporal_2d": {"position_unit": "image_diagonal", "speed_unit": "image_diagonal/s",
                            "acceleration_unit": "image_diagonal/s^2", **temporal},
            "temporal_3d": {"position_unit": "window_median_torso", "speed_unit": "window_median_torso/s",
                            "acceleration_unit": "window_median_torso/s^2",
                            "normalization": "midhip_relative_with_fixed_median_torso_scale_per_continuous_window",
                            "window_scales": scales3d, **temporal3d},
            "bone_stability_3d": _bone_stability(windows),
            "measurements_3d": measurements,
            "runtime": {"inference_ms": _stats(performance), "peak_memory_bytes": _stats(memory),
                        "scope": "reported_per_frame_measurements_only_including_reported_failed_attempts",
                        "missing_inference_records": total - len(performance),
                        "missing_memory_records": total - len(memory),
                        "warmup_or_model_load_included": False}}
