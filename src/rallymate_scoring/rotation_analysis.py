"""Replayable image-plane body-axis measurements, never a technique score.

Inputs are the confidence-filtered, torso-normalized samples from
``stroke_candidates._sample``. No 3D rotation, depth, contact or kinetic-chain
order can be recovered from these angles. Missing/foreshortened axes and
temporal discontinuities split measurements; they are never interpolated.
"""
from __future__ import annotations

import math
import statistics
from typing import Any


MIN_COVERAGE = 0.8
MIN_SAMPLES = 7
MIN_DURATION_MS = 200
MAX_GAP_MS = 160
MIN_AXIS_SPAN = {"shoulder": 0.3, "hip": 0.18}
AXIS_FIELDS = ("shoulder", "hip", "separation")
LIMITATIONS = [
    "肩线、髋线和肩髋分离均为画面内无方向轴的二维代理，不是三维转体角。",
    "投影、相机运动和遮挡会影响测量；不能据此判定骨盆领先、动力链质量或转体是否合格。",
    "低置信点已在姿态样本入口剔除；缺测、主体变化、时间缺口和异常跳变不跨段计算。",
    "尚无教练真值标定，转体技术分保持为空；可用数值描述运动幅度和速度，不代表技术优劣。",
]


def _number(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


def _axis_angle(sample: Any, joint: str) -> float | None:
    left, right = (sample.points.get(f"{side}_{joint}") for side in ("left", "right"))
    if (left is None or right is None or len(left) != 2 or len(right) != 2
            or not all(_number(value) for point in (left, right) for value in point)):
        return None
    dx, dy = right[0] - left[0], right[1] - left[1]
    if math.hypot(dx, dy) < MIN_AXIS_SPAN[joint]:
        return None
    return (math.degrees(math.atan2(dy, dx)) + 90) % 180 - 90


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


def analyze_rotation(samples: list[Any]) -> dict[str, Any]:
    """Measure each axis only within its dominant continuous observed window.

    A summary requires >=7 observations, >=200 ms, and >=80% of both samples
    and episode time in one continuous window. Thus even a short *interior*
    missing interval cannot be silently stitched into a rotation measurement.
    Series retain actual timestamps and segment ids for replay inspection.
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
                         or not valid_geometry or not 0.65 < after.scale / before.scale < 1.55
                         or math.dist(before.center, after.center) / after.scale >= 0.8)

    angles = {joint: [_axis_angle(sample, joint) for sample in samples] for joint in ("shoulder", "hip")}
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
    duration = max(times) - min(times) if times and all(time is not None for time in times) else 0
    for joint in AXIS_FIELDS:
        summaries = []
        for segment_id, indexes in enumerate(segments[joint]):
            unwrapped = [angles[joint][indexes[0]]]
            for previous, current in zip(indexes, indexes[1:]):
                unwrapped.append(unwrapped[-1] + _axis_delta(angles[joint][previous], angles[joint][current]))
            filtered = [statistics.median(unwrapped[i - 1:i + 2]) if 0 < i < len(unwrapped) - 1 else value
                        for i, value in enumerate(unwrapped)]
            velocities = []
            for i, index in enumerate(indexes):
                row = series[index]
                row[angle_fields[joint]] = round((filtered[i] + 90) % 180 - 90, 3)
                row[f"{joint}_segment_id"] = segment_id
                # Only central differences with two observed neighbours are
                # exported; edge speeds remain null rather than guessed.
                if 0 < i < len(indexes) - 1:
                    speed = (filtered[i + 1] - filtered[i - 1]) * 1000 / (times[indexes[i + 1]] - times[indexes[i - 1]])
                    row[f"{joint}_angular_velocity_deg_s"] = round(speed, 3)
                    velocities.append(abs(speed))
            span = times[indexes[-1]] - times[indexes[0]]
            summaries.append((indexes, filtered, velocities, span))
        dominant = max(summaries, key=lambda item: (item[3], len(item[0])), default=None)
        indexes, values, speeds, span = dominant if dominant else ([], [], [], 0)
        sample_coverage = len(indexes) / len(samples) if samples else 0
        time_coverage = span / duration if duration > 0 else 0
        available = (len(indexes) >= MIN_SAMPLES and span >= MIN_DURATION_MS
                     and min(sample_coverage, time_coverage) >= MIN_COVERAGE)
        reason = ("连续二维轴观测满足覆盖门槛，可结合时间窗回放核验。" if available else
                  "连续有效观测不足；可能存在投影缩短、低置信或缺点、时间缺口、身份切换或轴跳变。")
        detail = {"status": "measured" if available else "unavailable", "reason_zh": reason,
                  "valid_samples": sum(value is not None for value in angles[joint]),
                  "total_samples": len(samples), "continuous_samples": len(indexes),
                  "coverage_fraction": round(sample_coverage, 3),
                  "time_coverage_fraction": round(time_coverage, 3), "segment_count": len(summaries),
                  "start_ms": int(times[indexes[0]]) if available else None,
                  "end_ms": int(times[indexes[-1]]) if available else None}
        range_key = f"{joint}_line_change_deg" if joint != "separation" else "shoulder_hip_separation_change_deg"
        metrics[range_key] = round(max(values) - min(values), 1) if available else None
        evidence[range_key] = {**detail, "unit": "deg"}
        if joint == "separation":
            key = "shoulder_hip_separation_max_deg"
            metrics[key] = round(max(abs((value + 90) % 180 - 90) for value in values), 1) if available else None
            evidence[key] = {**detail, "unit": "deg"}
        else:
            key = f"peak_{joint}_angular_speed_deg_s"
            metrics[key] = round(max(speeds), 1) if available and speeds else None
            evidence[key] = {**detail, "unit": "deg/s"}
    measured = sum(value is not None for value in metrics.values())
    return {
        "schema_version": "1.0.0", "method": "undirected_image_plane_body_axes",
        "status": "measured_2d" if measured == len(metrics) else "partial" if measured else "unavailable",
        "coordinate_system": ("background_stabilized_image_x_right_y_down"
                              if any(getattr(sample, "camera_compensated", False) for sample in samples)
                              else "image_x_right_y_down"), "angle_unit": "deg", "angular_velocity_unit": "deg/s",
        "is_3d_rotation": False, "is_formal_coach_score": False, "score": None,
        "score_status": "calibration_required" if measured else "insufficient_evidence",
        "metrics": metrics, "metric_evidence": evidence, "series": series,
        "quality": {"sample_count": len(samples), "temporal_break_count": sum(breaks[1:]),
                    "camera_compensated_samples": sum(getattr(sample, "camera_compensated", False) for sample in samples),
                    "max_observation_gap_ms": round(max(deltas), 3) if deltas else None,
                    "nominal_interval_ms": round(nominal_step, 3) if nominal_step else None,
                    "minimum_continuous_coverage": MIN_COVERAGE, "minimum_samples": MIN_SAMPLES,
                    "minimum_duration_ms": MIN_DURATION_MS, "maximum_gap_ms": MAX_GAP_MS,
                    "minimum_axis_span_torso": MIN_AXIS_SPAN.copy(), "keypoint_confidence_min": 0.35},
        "limitations_zh": LIMITATIONS[:],
    }
