"""Observed motion process in body-relative image coordinates.

These measurements describe a continuous wrist trajectory and projected axis
speed timing. They cannot recover force, a three-dimensional kinetic chain,
the intended shot direction, or a technical grade.
"""
from __future__ import annotations

from collections.abc import Mapping
import math
import statistics
from typing import Any

from .indicator_observations import analyze_indicator_observations
from .rotation_analysis import analyze_rotation
from .temporal_recognition import samples_are_continuous
from .pose_observations import measurement_provenance
from .preparation_translation import analyze_preparation_translation


VERSION = "process-motion-v1.1.0"
PHASES = ("preparation", "acceleration", "follow_through")
MIN_SAMPLES = 5
MIN_DURATION_MS = 120
MIN_PATH_TORSO = 0.03
LIMITATIONS = [
    "方向是手腕相对身体在画面内的投影；画面左右随镜像改变，不能据此判断向球场前方或击球目标的方向是否正确。",
    "肩、髋和手腕的峰值先后仅表示二维运动时序，不等于真实受力顺序、骨盆领先或动力链质量。",
    "阶段由同一条手腕运动曲线推断，速度趋势用于描述过程，不是独立验证的动作优劣或正式技术分。",
    "缺帧、遮挡、主体或相机参考变化不补全；缺测不等于动作错误，静止或多个相近峰值不强行判定先后。",
]


def _number(value: Any) -> bool:
    if not isinstance(value, (int, float)) or isinstance(value, bool):
        return False
    try:
        return math.isfinite(value)
    except OverflowError:
        return False


def _point(sample: Any, name: str) -> tuple[float, float] | None:
    point = sample.points.get(name)
    confidence = getattr(sample, "point_confidences", {}).get(name)
    if (not isinstance(point, (tuple, list)) or len(point) != 2
            or not all(_number(value) for value in point)
            or confidence is not None and (not _number(confidence) or not 0.35 <= confidence <= 1)):
        return None
    return float(point[0]), float(point[1])


def _continuity_reasons(samples: list[Any], side: str, *, require_wrist: bool = True) -> list[str]:
    if require_wrist and side not in {"left", "right"}:
        return ["invalid_racket_side"]
    if len(samples) < MIN_SAMPLES:
        return ["insufficient_continuous_samples"]
    if any(not _number(sample.time) or sample.time < 0 or not _number(sample.frame_index) or sample.frame_index < 0
           or not _number(sample.scale) or sample.scale <= 0
           or not isinstance(sample.center, (tuple, list)) or len(sample.center) != 2
           or not all(_number(value) for value in sample.center) for sample in samples):
        return ["invalid_sample_geometry_or_time"]
    if any(getattr(sample, "timestamp_source", None) == "fps_fallback" for sample in samples):
        return ["source_timestamps_unavailable"]
    deltas = [b.time - a.time for a, b in zip(samples, samples[1:])]
    frames = [b.frame_index - a.frame_index for a, b in zip(samples, samples[1:])]
    if any(delta <= 0 for delta in deltas) or any(delta <= 0 for delta in frames):
        return ["nonmonotonic_observations"]
    time_limit = min(160, statistics.median(deltas) * 1.8)
    frame_limit = statistics.median(frames) * 1.8
    if any(not samples_are_continuous(a, b, max_gap_ms=time_limit)
           or b.frame_index - a.frame_index > frame_limit
           or getattr(a, "camera_compensated", False) != getattr(b, "camera_compensated", False)
           for a, b in zip(samples, samples[1:])):
        return ["observation_or_reference_discontinuity"]
    if require_wrist:
        points = [_point(sample, f"{side}_wrist") for sample in samples]
        if any(point is None for point in points):
            return ["wrist_missing_or_low_confidence"]
        if any(math.dist(a, b) > 1.5 for a, b in zip(points, points[1:])):
            return ["wrist_projection_jump"]
    if samples[-1].time - samples[0].time < MIN_DURATION_MS:
        return ["continuous_window_too_short"]
    return []


def _trajectory(samples: list[Any], side: str) -> tuple[list[tuple[float, float]], list[float]]:
    points = [_point(sample, f"{side}_wrist") for sample in samples]
    # Preserve observed endpoints. In particular, never manufacture zero speed
    # at the final frame, which would fabricate a perfect follow-through stop.
    filtered = [tuple(statistics.median(p[axis] for p in points[i - 1:i + 2]) for axis in (0, 1))
                if 0 < i < len(points) - 1 else point for i, point in enumerate(points)]
    speeds = [math.dist(a, b) * 1000 / (after.time - before.time)
              for a, b, before, after in zip(filtered, filtered[1:], samples, samples[1:])]
    return filtered, speeds


def _wrist_measurements(samples: list[Any], side: str) -> dict[str, Any]:
    points, speeds = _trajectory(samples, side)
    dx, dy = points[-1][0] - points[0][0], points[-1][1] - points[0][1]
    displacement = math.hypot(dx, dy)
    path = sum(math.dist(a, b) for a, b in zip(points, points[1:]))
    edge = min(2, len(speeds))
    start, end = statistics.fmean(speeds[:edge]), statistics.fmean(speeds[-edge:])
    changes = [b - a for a, b in zip(speeds, speeds[1:])]
    positive = sum(max(0, change) for change in changes)
    negative = sum(max(0, -change) for change in changes)
    variation = positive + negative
    tolerance = max(0.05, max(start, end) * 0.1)
    return {
        "dx_torso": round(dx, 4), "dy_torso": round(dy, 4),
        "direction_angle_deg": round(math.degrees(math.atan2(dy, dx)), 1) if displacement >= MIN_PATH_TORSO else None,
        "net_displacement_torso": round(displacement, 4), "path_length_torso": round(path, 4),
        "mean_speed_torso_per_s": round(path * 1000 / (samples[-1].time - samples[0].time), 4),
        "start_speed_torso_per_s": round(start, 4), "end_speed_torso_per_s": round(end, 4),
        "speed_trend": "increasing" if end - start > tolerance else "decreasing" if start - end > tolerance else "steady",
        "speed_increase_fraction": round(positive / variation, 4) if variation > 0.01 else None,
        "speed_decrease_fraction": round(negative / variation, 4) if variation > 0.01 else None,
        "speed_sample_count": len(speeds),
    }


def _empty_wrist() -> dict[str, Any]:
    return {key: None for key in (
        "dx_torso", "dy_torso", "direction_angle_deg", "net_displacement_torso", "path_length_torso",
        "mean_speed_torso_per_s", "start_speed_torso_per_s", "end_speed_torso_per_s", "speed_trend",
        "speed_increase_fraction", "speed_decrease_fraction", "speed_sample_count")}


def _phase_rows(samples: list[Any], phases: list[Mapping[str, Any]], side: str,
                global_reasons: list[str]) -> list[dict[str, Any]]:
    result = []
    by_name = {phase.get("phase"): phase for phase in phases if isinstance(phase, Mapping)}
    ordered = [phase for phase in phases if isinstance(phase, Mapping) and phase.get("status") == "measured"]
    valid_order = (len(by_name) == len(phases) and all(
        _number(phase.get("start_ms")) and _number(phase.get("end_ms"))
        and phase["start_ms"] < phase["end_ms"] for phase in ordered))
    if valid_order:
        valid_order = all(a["end_ms"] <= b["start_ms"] for a, b in zip(ordered, ordered[1:]))
    times = {sample.time for sample in samples if _number(sample.time)}
    for name in PHASES:
        phase = by_name.get(name, {})
        start, end = phase.get("start_ms"), phase.get("end_ms")
        reasons = global_reasons[:]
        if not valid_order:
            reasons.append("invalid_phase_order")
        if phase.get("status") != "measured" or not _number(start) or not _number(end) or not start < end:
            reasons.append("phase_not_observed")
        elif start not in times or end not in times:
            reasons.append("phase_bounds_not_observed")
        selected = [sample for sample in samples if start <= sample.time <= end] if not reasons else []
        if not reasons:
            reasons.extend(_continuity_reasons(selected, side))
        result.append({
            "phase": name, "status": "unavailable" if reasons else "measured",
            "start_ms": start if not reasons else None, "end_ms": end if not reasons else None,
            "duration_ms": end - start if not reasons else None,
            "sample_count": len(selected), "boundary_semantics": "estimated_motion_phase", "anchor_is_contact": False,
            "wrist": _wrist_measurements(selected, side) if not reasons else _empty_wrist(),
            "reason_codes": list(dict.fromkeys(reasons)),
        })
    return result


def _direction(samples: list[Any], phase: Mapping[str, Any], side: str) -> dict[str, Any]:
    result = {"status": "unavailable", "phase": "acceleration", "semantics": "projected_path_consistency_not_direction_correctness",
              "dx_torso": None, "dy_torso": None, "direction_angle_deg": None,
              "net_displacement_torso": None, "path_length_torso": None,
              "direction_consistency_ratio": None, "reversal_count": None,
              "reversal_definition": "adjacent_observed_steps_turn_more_than_120_degrees",
              "reason_codes": phase["reason_codes"][:]}
    if phase["status"] != "measured":
        return result
    wrist = phase["wrist"]
    result.update({key: wrist[key] for key in ("dx_torso", "dy_torso", "direction_angle_deg", "net_displacement_torso", "path_length_torso")})
    selected = [sample for sample in samples if phase["start_ms"] <= sample.time <= phase["end_ms"]]
    points, _ = _trajectory(selected, side)
    steps = [(b[0] - a[0], b[1] - a[1]) for a, b in zip(points, points[1:])]
    steps = [step for step in steps if math.hypot(*step) >= 0.005]
    path = sum(math.dist(a, b) for a, b in zip(points, points[1:]))
    if path < MIN_PATH_TORSO or len(steps) < 2:
        result["reason_codes"] = ["insufficient_projected_motion"]
        return result
    displacement = math.dist(points[0], points[-1])
    result.update(status="measured", direction_consistency_ratio=round(min(1, displacement / path), 4),
                  reversal_count=sum((a[0] * b[0] + a[1] * b[1]) / math.hypot(*a) / math.hypot(*b) < -0.5
                                     for a, b in zip(steps, steps[1:])))
    return result


def _peak(rows: list[tuple[float, float]], *, minimum_speed: float) -> tuple[int | None, list[str]]:
    if len(rows) < 5:
        return None, ["insufficient_peak_observations"]
    maximum = max(speed for _, speed in rows)
    if maximum < minimum_speed:
        return None, ["insufficient_peak_motion"]
    minimum = min(speed for _, speed in rows)
    if maximum - minimum <= max(minimum_speed, maximum * 0.15):
        return None, ["ambiguous_flat_peak"]
    indexes = [i for i, (_, speed) in enumerate(rows) if speed >= maximum * 0.95]
    step = statistics.median(b[0] - a[0] for a, b in zip(rows, rows[1:]))
    if (indexes != list(range(indexes[0], indexes[-1] + 1))
            or rows[indexes[-1]][0] - rows[indexes[0]][0] > max(100, step * 2)):
        return None, ["ambiguous_multiple_or_broad_peaks"]
    if indexes[0] == 0 or indexes[-1] == len(rows) - 1:
        return None, ["peak_at_observation_boundary"]
    return round(statistics.fmean(rows[index][0] for index in indexes)), []


def _sequence(samples: list[Any], side: str, reasons: list[str]) -> dict[str, Any]:
    result = {"status": "unavailable", "semantics": "image_plane_speed_peak_order", "scope": "whole_observed_episode",
              "hip_peak_ms": None, "shoulder_peak_ms": None, "wrist_peak_ms": None,
              "hip_to_shoulder_ms": None, "shoulder_to_wrist_ms": None, "order": [],
              "timing_resolution_ms": None, "reason_codes": reasons[:], "peak_reason_codes": {}}
    if reasons or side not in {"left", "right"}:
        if not reasons:
            result["reason_codes"] = ["invalid_racket_side"]
        return result
    rotation = analyze_rotation(samples)
    series = rotation["series"]
    for joint in ("hip", "shoulder"):
        valid_points = all(_point(sample, f"{part}_{joint}") is not None for sample in samples for part in ("left", "right"))
        rows = [(row["timestamp_ms"], abs(row[f"{joint}_angular_velocity_deg_s"])) for row in series
                if row[f"{joint}_angular_velocity_deg_s"] is not None]
        segments = {row[f"{joint}_segment_id"] for row in series}
        if not valid_points or None in segments or len(segments) != 1 or len(rows) != len(samples) - 2:
            result["peak_reason_codes"][joint] = ["axis_not_continuously_observed"]
            continue
        peak, peak_reasons = _peak(rows, minimum_speed=2.0)
        result[f"{joint}_peak_ms"] = peak
        result["peak_reason_codes"][joint] = peak_reasons
    wrist_reasons = _continuity_reasons(samples, side)
    wrist_peak = None
    if not wrist_reasons:
        points, _ = _trajectory(samples, side)
        wrist_rows = [(samples[i].time, math.dist(points[i - 1], points[i + 1]) * 1000 / (samples[i + 1].time - samples[i - 1].time))
                      for i in range(1, len(samples) - 1)]
        wrist_peak, wrist_reasons = _peak(wrist_rows, minimum_speed=0.05)
    result["wrist_peak_ms"], result["peak_reason_codes"]["wrist"] = wrist_peak, wrist_reasons
    result["reason_codes"] = list(dict.fromkeys(code for codes in result["peak_reason_codes"].values() for code in codes))
    result["timing_resolution_ms"] = round(max(b.time - a.time for a, b in zip(samples, samples[1:])), 3)
    # A missing wrist cannot invalidate an independently observed hip/shoulder
    # interval. The complete three-joint ordering still requires all peaks.
    hip, shoulder, wrist = (result[f"{joint}_peak_ms"] for joint in ("hip", "shoulder", "wrist"))
    if hip is not None and shoulder is not None:
        result["hip_to_shoulder_ms"] = shoulder - hip
    if shoulder is not None and wrist is not None:
        result["shoulder_to_wrist_ms"] = wrist - shoulder
    if not result["reason_codes"]:
        result.update(status="measured", hip_to_shoulder_ms=shoulder - hip, shoulder_to_wrist_ms=wrist - shoulder)
        result["order"] = sorted([{"joint": joint, "timestamp_ms": result[f"{joint}_peak_ms"]}
                                  for joint in ("hip", "shoulder", "wrist")], key=lambda item: item["timestamp_ms"])
        result["ordering_resolved"] = all(b["timestamp_ms"] - a["timestamp_ms"] > result["timing_resolution_ms"]
                                         for a, b in zip(result["order"], result["order"][1:]))
    else:
        if result["hip_to_shoulder_ms"] is not None or result["shoulder_to_wrist_ms"] is not None:
            result["status"] = "partial"
        result["ordering_resolved"] = False
    return result


def analyze_process_motion(samples: list[Any], phases: list[Mapping[str, Any]], side: str) -> dict[str, Any]:
    """Measure observed phases; unavailable evidence retains null values."""
    reference_reasons = _continuity_reasons(samples, side, require_wrist=False)
    reasons = _continuity_reasons(samples, side)
    # Identity, time, scale and camera-reference failures invalidate the shared
    # coordinate system. Local wrist visibility is checked within each phase;
    # it must not erase complete measurements before or after an occlusion.
    phase_rows = _phase_rows(samples, phases, side, reference_reasons)
    sequence = _sequence(samples, side, reference_reasons)
    direction = _direction(samples, phase_rows[1], side)
    follow = phase_rows[2]
    follow_through = {"status": "unavailable", "end_to_peak_speed_ratio": None,
                      "peak_speed_torso_per_s": None, "end_speed_torso_per_s": None,
                      "reason_codes": list(dict.fromkeys(follow["reason_codes"] + reasons)),
                      "semantics": "observed_wrist_slowdown_not_finish_quality"}
    if not reasons and follow["status"] == "measured":
        _, speeds = _trajectory(samples, side)
        peak = max(speeds)
        if peak > 0.05:
            end = follow["wrist"]["end_speed_torso_per_s"]
            follow_through.update(status="measured", end_to_peak_speed_ratio=round(end / peak, 4),
                                  peak_speed_torso_per_s=round(peak, 4), end_speed_torso_per_s=end)
        else:
            follow_through["reason_codes"] = ["insufficient_projected_motion"]
    measured = sum(row["status"] == "measured" for row in phase_rows)
    components = [direction, sequence, follow_through]
    complete = measured == len(PHASES) and all(item["status"] == "measured" for item in components)
    return {
        "version": VERSION, "status": "measured_2d" if complete else "partial" if measured or any(item["status"] in {"measured", "partial"} for item in components) else "unavailable",
        "coordinate_system": "body_relative_image_x_right_y_down", "coordinate_unit": "torso_length", "score": None, "is_formal_score": False,
        "measurement_provenance": measurement_provenance(samples),
        "phases": phase_rows, "sequence": sequence, "direction": direction, "follow_through": follow_through,
        "source_observations": analyze_indicator_observations(samples, phases, side),
        "preparation_translation": analyze_preparation_translation(samples, phases),
        "reason_codes": reasons, "quality": {"minimum_samples": MIN_SAMPLES, "minimum_duration_ms": MIN_DURATION_MS,
            "maximum_observation_gap_ms": 160, "maximum_wrist_step_torso": 1.5, "source": "engineering_observability_gate"},
        "limitations_zh": LIMITATIONS[:],
    }


def analyze_motion_transition(previous: Mapping[str, Any], current: Mapping[str, Any],
                              samples: list[Any]) -> dict[str, Any]:
    """Report an interval only when its actual intermediate frames are continuous.

    Equal tracking identifiers alone cannot establish observation continuity.
    This interval is not recovery efficiency or readiness for the next ball.
    """
    result = {"status": "unavailable", "previous_episode_id": previous.get("episode_id"),
              "start_ms": None, "end_ms": None, "interval_ms": None, "reason_codes": [],
              "semantics": "observed_inter_motion_interval_not_readiness"}
    start, end = previous.get("end_ms"), current.get("start_ms")
    if previous.get("person_track_id") != current.get("person_track_id"):
        result["reason_codes"] = ["different_player"]
        return result
    if not _number(start) or not _number(end) or start >= end:
        result["reason_codes"] = ["overlapping_or_invalid_episode_bounds"]
        return result
    selected = [sample for sample in samples if _number(sample.time) and start <= sample.time <= end]
    if not selected or selected[0].time != start or selected[-1].time != end:
        result["reason_codes"] = ["transition_bounds_not_observed"]
        return result
    if any(sample.track != current.get("person_track_id") for sample in selected):
        result["reason_codes"] = ["different_player"]
        return result
    # This is elapsed observed time, not a wrist-motion metric. A hidden hand
    # between two episodes cannot by itself invalidate the time interval.
    reasons = _continuity_reasons(selected, current.get("racket_hand"), require_wrist=False)
    result["reason_codes"] = reasons
    if not reasons:
        result.update(status="measured", start_ms=start, end_ms=end, interval_ms=end - start)
    return result
