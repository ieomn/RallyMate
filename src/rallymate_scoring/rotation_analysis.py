"""Replayable image-plane body-axis measurements, never a technique score.

Inputs are the confidence-filtered, torso-normalized samples from
``stroke_candidates._sample``. No 3D rotation, depth, contact or kinetic-chain
order can be recovered from these angles. Missing/foreshortened axes and
temporal discontinuities split measurements; they are never interpolated.
"""
from __future__ import annotations

import math
import statistics
from collections import Counter
from collections.abc import Mapping
from typing import Any


MIN_COVERAGE = 0.8
MIN_SAMPLES = 7
MIN_DURATION_MS = 200
MAX_GAP_MS = 160
MIN_AXIS_SPAN = {"shoulder": 0.3, "hip": 0.18}
AXIS_FIELDS = ("shoulder", "hip", "separation")
ANALYSIS_VERSION = "image-plane-rotation-v1.1.0"
_REASONS = {
    "axis_points_missing": "所需肩或髋关键点缺测或置信度不足。",
    "axis_points_invalid": "所需轴端点坐标无效。",
    "axis_projection_too_short": "肩线或髋线投影过短，方向不可靠。",
    "insufficient_continuous_samples": "连续观测少于 7 帧。",
    "continuous_window_too_short": "连续观测时间不足 200 毫秒。",
    "episode_coverage_insufficient": "单个连续窗口不足以代表该片段；可展开独立局部窗口。",
    "phase_coverage_insufficient": "单个连续窗口尚未覆盖该动作阶段的 80%。",
    "phase_not_observed": "未观测到可靠的动作阶段边界，不补全阶段测量。",
}
LIMITATIONS = [
    "肩线、髋线和肩髋分离均为画面内无方向轴的二维代理，不是三维转体角。",
    "投影、相机运动和遮挡会影响测量；不能据此判定骨盆领先、动力链质量或转体是否合格。",
    "低置信点已在姿态样本入口剔除；缺测、主体变化、时间缺口和异常跳变不跨段计算。",
    "尚无教练真值标定，转体技术分保持为空；可用数值描述运动幅度和速度，不代表技术优劣。",
]


def _number(value: Any) -> bool:
    if not isinstance(value, (int, float)) or isinstance(value, bool):
        return False
    try:
        return math.isfinite(value)
    except OverflowError:
        return False


def _axis_observation(sample: Any, joint: str) -> tuple[float | None, str | None]:
    left, right = (sample.points.get(f"{side}_{joint}") for side in ("left", "right"))
    if left is None or right is None:
        return None, "axis_points_missing"
    if (len(left) != 2 or len(right) != 2
            or not all(_number(value) for point in (left, right) for value in point)):
        return None, "axis_points_invalid"
    dx, dy = right[0] - left[0], right[1] - left[1]
    if math.hypot(dx, dy) < MIN_AXIS_SPAN[joint]:
        return None, "axis_projection_too_short"
    return (math.degrees(math.atan2(dy, dx)) + 90) % 180 - 90, None


def _axis_angle(sample: Any, joint: str) -> float | None:
    return _axis_observation(sample, joint)[0]


def _metric_values(joint: str, values: list[float], speeds: list[float]) -> dict[str, float | None]:
    key = f"{joint}_line_change_deg" if joint != "separation" else "shoulder_hip_separation_change_deg"
    result = {key: round(max(values) - min(values), 1) if values else None}
    if joint == "separation":
        result["shoulder_hip_separation_max_deg"] = round(max(abs((value + 90) % 180 - 90) for value in values), 1) if values else None
    else:
        result[f"peak_{joint}_angular_speed_deg_s"] = round(max(speeds), 1) if speeds else None
    return result


def _filtered_segment(indexes: list[int], angles: list[float | None], times: list[float | None]) -> tuple[list[float], list[float]]:
    unwrapped = [angles[indexes[0]]]
    for previous, current in zip(indexes, indexes[1:]):
        unwrapped.append(unwrapped[-1] + _axis_delta(angles[previous], angles[current]))
    filtered = [statistics.median(unwrapped[i - 1:i + 2]) if 0 < i < len(unwrapped) - 1 else value
                for i, value in enumerate(unwrapped)]
    speeds = [(filtered[i + 1] - filtered[i - 1]) * 1000 / (times[indexes[i + 1]] - times[indexes[i - 1]])
              for i in range(1, len(indexes) - 1)]
    return filtered, speeds


def _metric_evidence(metrics: Mapping[str, float | None], detail: dict[str, Any]) -> dict[str, Any]:
    return {key: {**detail, "value": value, "unit": "deg/s" if key.endswith("deg_s") else "deg",
                  "source_version": ANALYSIS_VERSION, "view_semantics": "image_plane_proxy",
                  "is_3d_rotation": False} for key, value in metrics.items()}


def _availability_reasons(indexes: list[int], span: float, sample_coverage: float,
                          time_coverage: float, scope: str) -> list[str]:
    reasons = []
    if len(indexes) < MIN_SAMPLES:
        reasons.append("insufficient_continuous_samples")
    if span < MIN_DURATION_MS:
        reasons.append("continuous_window_too_short")
    if min(sample_coverage, time_coverage) < MIN_COVERAGE:
        reasons.append(f"{scope}_coverage_insufficient")
    return reasons


def _axis_delta(before: float, after: float) -> float:
    # Left/right endpoint reversal leaves an undirected line unchanged.
    return (after - before + 90) % 180 - 90


def _segments(values: list[float | None], times: list[float | None], breaks: list[bool]) -> list[list[int]]:
    result: list[list[int]] = []
    current: list[int] = []
    for index, value in enumerate(values):
        valid = value is not None and times[index] is not None
        continuous = not breaks[index]
        if valid and current and continuous:
            previous = current[-1]
            delta = abs(_axis_delta(values[previous], value))
            dt = times[index] - times[previous]
            # This is a conservative ambiguity gate for projected pose jumps,
            # not a validated bound on anatomical rotation speed.
            continuous = delta <= 45 and delta * 1000 / dt <= 720
        if not valid or not continuous:
            if current:
                result.append(current)
            current = []
        if valid:
            current.append(index)
    if current:
        result.append(current)
    return result


def _phase_measurements(phases: list[Mapping[str, Any]], samples: list[Any], times: list[float | None],
                        angles: dict[str, list[float | None]], summaries: dict[str, list[Any]]) -> list[dict[str, Any]]:
    """Intersect observed axis segments with observed motion-phase bounds.

    The denominator is the supplied phase duration, including clipped input.
    Splits from the original sequence are retained, so neither cropping nor a
    fresh estimate of the sample interval can reconnect a missing interval.
    """
    result = []
    for phase in phases:
        if not isinstance(phase, Mapping) or phase.get("phase") not in {"preparation", "acceleration", "follow_through"}:
            continue
        start, end = phase.get("start_ms"), phase.get("end_ms")
        observed = phase.get("status") == "measured" and _number(start) and _number(end) and 0 <= start < end
        total_samples = sum(time is not None and start <= time <= end for time in times) if observed else 0
        metrics, evidence = {}, {}
        for joint in AXIS_FIELDS:
            windows = []
            if observed:
                for source_indexes, _, _, _ in summaries[joint]:
                    indexes = [index for index in source_indexes if start <= times[index] <= end]
                    if indexes:
                        values, speeds = _filtered_segment(indexes, angles[joint], times)
                        windows.append((indexes, values, [abs(speed) for speed in speeds], times[indexes[-1]] - times[indexes[0]]))
            dominant = max(windows, key=lambda item: (item[3], len(item[0])), default=([], [], [], 0))
            indexes, values, speeds, span = dominant
            sample_coverage = len(indexes) / total_samples if total_samples else 0
            time_coverage = span / (end - start) if observed else 0
            reasons = _availability_reasons(indexes, span, sample_coverage, time_coverage, "phase") if observed else ["phase_not_observed"]
            axis_metrics = _metric_values(joint, values if not reasons else [], speeds if not reasons else [])
            detail = {
                "status": "measured" if not reasons else "unavailable", "scope": "motion_phase", "axis": joint,
                "reason_codes": reasons, "reason_zh": "阶段内连续二维观测满足门槛。" if not reasons else " ".join(_REASONS[reason] for reason in reasons),
                "start_ms": int(times[indexes[0]]) if not reasons else None,
                "end_ms": int(times[indexes[-1]]) if not reasons else None,
                "continuous_samples": len(indexes), "total_samples": total_samples,
                "coverage_fraction": round(sample_coverage, 3), "time_coverage_fraction": round(time_coverage, 3),
                "phase": phase["phase"], "anchor_is_contact": False,
            }
            metrics.update(axis_metrics)
            evidence.update(_metric_evidence(axis_metrics, detail))
        count = sum(value is not None for value in metrics.values())
        result.append({
            "phase": phase["phase"], "label_zh": phase.get("label_zh") if isinstance(phase.get("label_zh"), str) else phase["phase"],
            "start_ms": int(start) if observed else None, "end_ms": int(end) if observed else None,
            "status": "measured_2d" if count == len(metrics) else "partial" if count else "unavailable",
            "boundary_semantics": "estimated_motion_phase", "anchor_is_contact": False,
            "metrics": metrics, "metric_evidence": evidence, "source_version": ANALYSIS_VERSION,
        })
    return result


def analyze_rotation(samples: list[Any], *, phases: list[Mapping[str, Any]] | None = None) -> dict[str, Any]:
    """Measure each axis only within its dominant continuous observed window.

    A summary requires >=7 observations, >=200 ms, and >=80% of both samples
    and episode time in one continuous window. Thus even a short *interior*
    missing interval cannot be silently stitched into a rotation measurement.
    Series retain actual timestamps and segment ids for replay inspection.
    Independent local windows retain the same minimum samples and duration,
    but describe only their own continuous observed interval. They never
    replace the episode summary or imply a completed rotation or a grade.
    """
    times = [float(sample.time) if _number(sample.time) else None for sample in samples]
    deltas = [b - a for a, b in zip(times, times[1:]) if a is not None and b is not None and b > a]
    nominal_step = statistics.median(deltas) if deltas else None
    gap_limit = min(MAX_GAP_MS, nominal_step * 1.8) if nominal_step else MAX_GAP_MS
    frame_steps = [b.frame_index - a.frame_index for a, b in zip(samples, samples[1:])
                   if _number(a.frame_index) and _number(b.frame_index) and b.frame_index > a.frame_index]
    frame_step = statistics.median(frame_steps) if frame_steps else None
    breaks = [True] * len(samples)
    for index in range(1, len(samples)):
        before, after = samples[index - 1], samples[index]
        a, b = times[index - 1:index + 1]
        valid_geometry = (all(_number(value) for value in (*before.center, *after.center, before.scale, after.scale))
                          and before.scale > 0 and after.scale > 0)
        frame_gap = (frame_step is not None and _number(before.frame_index) and _number(after.frame_index)
                     and not 0 < after.frame_index - before.frame_index <= frame_step * 1.8)
        breaks[index] = (a is None or b is None or not 0 < b - a <= gap_limit or frame_gap
                         or before.track != after.track or before.selection_epoch != after.selection_epoch
                         or getattr(before, "camera_reference_epoch", None) != getattr(after, "camera_reference_epoch", None)
                         or getattr(before, "normalization_basis", None) != getattr(after, "normalization_basis", None)
                         or not valid_geometry or not 0.65 < after.scale / before.scale < 1.55
                         or math.dist(before.center, after.center) / after.scale >= 0.8)

    observations = {joint: [_axis_observation(sample, joint) for sample in samples] for joint in ("shoulder", "hip")}
    angles = {joint: [value for value, _ in observations[joint]] for joint in ("shoulder", "hip")}
    angles["separation"] = [_axis_delta(hip, shoulder) if shoulder is not None and hip is not None else None
                            for shoulder, hip in zip(angles["shoulder"], angles["hip"])]
    segments = {joint: _segments(angles[joint], times, breaks) for joint in AXIS_FIELDS}
    # Separation needs continuous evidence for *both* constituent axes. Equal
    # shoulder and hip jumps must not cancel into a falsely stable separation.
    paired_breaks = breaks[:]
    for joint in ("shoulder", "hip"):
        for segment in segments[joint]:
            paired_breaks[segment[0]] = True
    segments["separation"] = _segments(angles["separation"], times, paired_breaks)

    series = [{"timestamp_ms": int(time) if time is not None else None,
               "frame_index": sample.frame_index if _number(sample.frame_index) else None,
               "person_track_id": sample.track, "selection_epoch": sample.selection_epoch,
               "shoulder_line_angle_deg": None, "hip_line_angle_deg": None,
               "shoulder_hip_separation_deg": None,
               "shoulder_angular_velocity_deg_s": None, "hip_angular_velocity_deg_s": None,
               "separation_angular_velocity_deg_s": None,
               "shoulder_segment_id": None, "hip_segment_id": None, "separation_segment_id": None}
              for sample, time in zip(samples, times)]
    angle_fields = {"shoulder": "shoulder_line_angle_deg", "hip": "hip_line_angle_deg",
                    "separation": "shoulder_hip_separation_deg"}
    metrics: dict[str, float | None] = {}
    evidence = {}
    local_windows = []
    summaries_by_axis = {}
    duration = max(times) - min(times) if times and all(time is not None for time in times) else 0
    for joint in AXIS_FIELDS:
        summaries = []
        for segment_id, indexes in enumerate(segments[joint]):
            filtered, signed_speeds = _filtered_segment(indexes, angles[joint], times)
            velocities = [abs(speed) for speed in signed_speeds]
            for i, index in enumerate(indexes):
                row = series[index]
                row[angle_fields[joint]] = round((filtered[i] + 90) % 180 - 90, 3)
                row[f"{joint}_segment_id"] = segment_id
                # Only central differences with two observed neighbours are
                # exported; edge speeds remain null rather than guessed.
                if 0 < i < len(indexes) - 1:
                    speed = signed_speeds[i - 1]
                    row[f"{joint}_angular_velocity_deg_s"] = round(speed, 3)
            span = times[indexes[-1]] - times[indexes[0]]
            summaries.append((indexes, filtered, velocities, span))
            if len(indexes) >= MIN_SAMPLES and span >= MIN_DURATION_MS:
                window_metrics = _metric_values(joint, filtered, velocities)
                window_detail = {
                    "status": "measured", "scope": "continuous_local_window", "axis": joint,
                    "reason_codes": [], "reason_zh": "该连续窗口满足观测门槛；仅描述本窗口的二维变化。",
                    "start_ms": int(times[indexes[0]]), "end_ms": int(times[indexes[-1]]),
                    "continuous_samples": len(indexes), "coverage_fraction": 1.0, "time_coverage_fraction": 1.0,
                    "episode_sample_fraction": round(len(indexes) / len(samples), 3),
                    "episode_time_fraction": round(span / duration, 3) if duration > 0 else None,
                    "person_track_id": samples[indexes[0]].track,
                    "selection_epoch": samples[indexes[0]].selection_epoch,
                }
                local_windows.append({
                    "window_id": f"{joint}-{segment_id}", **window_detail,
                    "metrics": window_metrics, "metric_evidence": _metric_evidence(window_metrics, window_detail),
                })
        summaries_by_axis[joint] = summaries
        dominant = max(summaries, key=lambda item: (item[3], len(item[0])), default=None)
        indexes, values, speeds, span = dominant if dominant else ([], [], [], 0)
        sample_coverage = len(indexes) / len(samples) if samples else 0
        time_coverage = span / duration if duration > 0 else 0
        reason_codes = _availability_reasons(indexes, span, sample_coverage, time_coverage, "episode")
        axis_names = ("shoulder", "hip") if joint == "separation" else (joint,)
        missing_reasons = Counter(reason for axis in axis_names for _, reason in observations[axis] if reason)
        available = not reason_codes
        reason = ("连续二维轴观测满足覆盖门槛，可结合时间窗回放核验。" if available else
                  " ".join(_REASONS[code] for code in sorted(missing_reasons) + reason_codes))
        detail = {"status": "measured" if available else "unavailable", "reason_zh": reason,
                  "scope": "episode_summary", "axis": joint,
                  "reason_codes": reason_codes + sorted(missing_reasons) if not available else [],
                  "observation_rejections": dict(missing_reasons),
                  "review_hint_zh": "按时间窗复核肩线、髋线投影；调整机位使所需两侧关节持续可见。" if not available else None,
                  "valid_samples": sum(value is not None for value in angles[joint]),
                  "total_samples": len(samples), "continuous_samples": len(indexes),
                  "coverage_fraction": round(sample_coverage, 3),
                  "time_coverage_fraction": round(time_coverage, 3), "segment_count": len(summaries),
                  "start_ms": int(times[indexes[0]]) if available else None,
                  "end_ms": int(times[indexes[-1]]) if available else None}
        values_for_axis = _metric_values(joint, values if available else [], speeds if available else [])
        metrics.update(values_for_axis)
        evidence.update(_metric_evidence(values_for_axis, detail))
    phase_measurements = _phase_measurements(phases or [], samples, times, angles, summaries_by_axis)
    measured = sum(value is not None for value in metrics.values())
    return {
        "schema_version": "1.1.0", "analysis_version": ANALYSIS_VERSION, "method": "undirected_image_plane_body_axes",
        "status": "measured_2d" if measured == len(metrics) else "partial" if measured else "unavailable",
        "coordinate_system": ("background_stabilized_image_x_right_y_down"
                              if any(getattr(sample, "camera_compensated", False) for sample in samples)
                              else "image_x_right_y_down"), "angle_unit": "deg", "angular_velocity_unit": "deg/s",
        "is_3d_rotation": False, "is_formal_coach_score": False, "score": None,
        "score_status": "calibration_required" if measured else "insufficient_evidence",
        "metrics": metrics, "metric_evidence": evidence, "series": series,
        "local_windows": local_windows, "phase_measurements": phase_measurements,
        "local_measurement_status": "measured_2d" if local_windows else "unavailable",
        "quality": {"sample_count": len(samples), "temporal_break_count": sum(breaks[1:]),
                    "camera_compensated_samples": sum(getattr(sample, "camera_compensated", False) for sample in samples),
                    "max_observation_gap_ms": round(max(deltas), 3) if deltas else None,
                    "nominal_interval_ms": round(nominal_step, 3) if nominal_step else None,
                    "minimum_continuous_coverage": MIN_COVERAGE, "minimum_samples": MIN_SAMPLES,
                    "minimum_duration_ms": MIN_DURATION_MS, "maximum_gap_ms": MAX_GAP_MS,
                    "minimum_axis_span_torso": MIN_AXIS_SPAN.copy(), "keypoint_confidence_min": 0.35},
        "limitations_zh": LIMITATIONS[:],
    }
