"""Joint-specific measurements for the FS/GS source cards.

Every value is an image-plane observation. Three estimated motion phases are
retained as such: they do not certify the ten GS stages, contact, gaze or force.
Missing joints split only the measurements that depend on those joints.
"""
from __future__ import annotations

import math
import statistics
from collections.abc import Mapping
from typing import Any

from .temporal_recognition import samples_are_continuous
from .pose_observations import measurement_provenance, sample_source_reference

VERSION = "indicator-observations-v1.0.0"
PHASES = ("preparation", "acceleration", "follow_through")
CONFIDENCE_MIN = 0.35
MIN_WINDOW_SAMPLES = 3
MIN_WINDOW_DURATION_MS = 80


def _num(value: Any) -> bool:
    try:
        return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)
    except OverflowError:
        return False


def _point(sample: Any, name: str):
    point = sample.points.get(name)
    confidence = getattr(sample, "point_confidences", {}).get(name)
    if (not isinstance(point, (list, tuple)) or len(point) != 2
            or not all(_num(v) for v in point)
            or confidence is not None and (not _num(confidence) or not CONFIDENCE_MIN <= confidence <= 1)):
        return None
    return tuple(float(v) for v in point)


def _middle(a, b):
    return ((a[0] + b[0]) / 2, (a[1] + b[1]) / 2)


def _angle(a, b, c, *, minimum):
    u, v = (a[0] - b[0], a[1] - b[1]), (c[0] - b[0], c[1] - b[1])
    lengths = math.hypot(*u), math.hypot(*v)
    if not all(math.isfinite(value) for value in lengths) or min(lengths) < minimum or max(lengths) / min(lengths) > 4:
        return None
    cosine = sum((x / lengths[0]) * (y / lengths[1]) for x, y in zip(u, v))
    return math.degrees(math.acos(max(-1, min(1, cosine))))


def _specs():
    specs = {}
    for side in ("left", "right"):
        specs[f"{side}_knee_flexion_deg"] = (
            tuple(f"{side}_{joint}" for joint in ("hip", "knee", "ankle")), "deg", "knee",
            "180°减去髋—膝—踝的二维夹角；正变化表示画面中屈膝增加。")
        specs[f"{side}_elbow_angle_deg"] = (
            tuple(f"{side}_{joint}" for joint in ("shoulder", "elbow", "wrist")), "deg", "elbow",
            "肩—肘—腕的二维夹角；正变化表示画面中肘部伸展增加。")
    specs.update({
        "trunk_tilt_deg": (("left_shoulder", "right_shoulder", "left_hip", "right_hip"), "deg", "trunk",
                           "肩中心至髋中心连线相对画面竖直的绝对倾角，不是相对球场竖直的真实倾角。"),
        "ankle_distance_over_hip_width": (("left_ankle", "right_ankle", "left_hip", "right_hip"), "ratio", "stance",
                                         "双踝横向距离÷双髋二维距离；髋宽投影过小时拒绝计算。"),
        "hip_inside_ankle_support_fraction": (("left_ankle", "right_ankle", "left_hip", "right_hip"), "fraction", "support",
                                              "每帧髋中心横坐标在双踝横坐标闭区间内记1，否则记0；均值为投影支撑区间内比例，不是真实承重。"),
        "hip_to_ankle_midpoint_torso": (("left_ankle", "right_ankle", "left_hip", "right_hip"), "torso", "hip_offset",
                                       "髋中心减去双踝中点的画面横向位置；按当前躯干长度归一化。"),
        "wrist_separation_torso": (("left_wrist", "right_wrist"), "torso", "distance",
                                   "双腕二维距离，按躯干长度归一化；距离变小不能单独确认双手持拍。"),
        "head_to_shoulders_distance_torso": (("nose", "left_shoulder", "right_shoulder"), "torso", "head",
                                             "鼻部至双肩中点的二维距离；只表示头部相对位置，不能确认真实注视方向。"),
        "shoulder_axis_deg": (("left_shoulder", "right_shoulder"), "deg", "axis_shoulder",
                               "无方向肩线在画面中的角度，连续段按180°周期展开；不是三维转肩角。"),
        "hip_axis_deg": (("left_hip", "right_hip"), "deg", "axis_hip",
                          "无方向髋线在画面中的角度，连续段按180°周期展开；不是三维转髋角。"),
        "shoulder_hip_separation_deg": (("left_shoulder", "right_shoulder", "left_hip", "right_hip"), "deg", "separation",
                                       "肩线和髋线在画面中的最小夹角，范围0～90°；不是三维肩髋分离。"),
    })
    return specs


MEASUREMENTS = _specs()


def _axis(a, b, minimum):
    if math.dist(a, b) < minimum:
        return None
    return (math.degrees(math.atan2(b[1] - a[1], b[0] - a[0])) + 90) % 180 - 90


def _value(sample, joints, kind):
    points = [_point(sample, name) for name in joints]
    if any(p is None for p in points):
        return None
    if kind in {"knee", "elbow"}:
        angle = _angle(*points, minimum=.15 if kind == "elbow" else .1)
        return 180 - angle if angle is not None and kind == "knee" else angle
    if kind == "distance":
        return math.dist(*points)
    if kind == "head":
        return math.dist(points[0], _middle(*points[1:]))
    if kind == "trunk":
        shoulders, hip = _middle(*points[:2]), _middle(*points[2:])
        if math.dist(shoulders, hip) < .1:
            return None
        return math.degrees(math.atan2(abs(hip[0] - shoulders[0]), abs(hip[1] - shoulders[1])))
    if kind in {"stance", "support", "hip_offset"}:
        a, b, left, right = points
        if kind == "stance":
            width = math.dist(left, right)
            return abs(b[0] - a[0]) / width if width >= .18 else None
        hip_x = (left[0] + right[0]) / 2
        return float(min(a[0], b[0]) <= hip_x <= max(a[0], b[0])) if kind == "support" else hip_x - (a[0] + b[0]) / 2
    if kind.startswith("axis_"):
        return _axis(*points, .3 if kind == "axis_shoulder" else .18)
    shoulder, hip = _axis(*points[:2], .3), _axis(*points[2:], .18)
    return abs((shoulder - hip + 90) % 180 - 90) if shoulder is not None and hip is not None else None


def _summary(values, kind):
    if kind.startswith("axis_"):
        unwrapped = [values[0]]
        for a, b in zip(values, values[1:]):
            unwrapped.append(unwrapped[-1] + (b - a + 90) % 180 - 90)
        values = unwrapped
    return {key: round(value, 5) for key, value in {
        "start": values[0], "end": values[-1], "delta": values[-1] - values[0],
        "min": min(values), "max": max(values), "median": statistics.median(values),
        "mean": statistics.fmean(values), "range": max(values) - min(values),
    }.items()}


def _valid_sample(sample):
    return (_num(sample.time) and sample.time >= 0 and _num(sample.frame_index) and sample.frame_index >= 0
            and _num(sample.scale) and sample.scale > 0
            and isinstance(sample.center, (list, tuple)) and len(sample.center) == 2 and all(_num(v) for v in sample.center)
            and getattr(sample, "timestamp_source", None) != "fps_fallback")


def _reference(sample):
    return {"person_track_id": sample.track, "selection_epoch": sample.selection_epoch,
            "camera_reference_epoch": getattr(sample, "camera_reference_epoch", None),
            "camera_compensated": getattr(sample, "camera_compensated", False),
            "normalization_basis": getattr(sample, "normalization_basis", "full_torso"), **sample_source_reference(sample)}


def _measure(samples, start, end, spec, phase_reason):
    joints, unit, kind, definition = spec
    values = [_value(sample, joints, kind) if _valid_sample(sample) else None for sample in samples]
    count, total = sum(v is not None for v in values), len(values)
    coverage = count / total if total else 0
    row = {"status": "unavailable", "unit": unit, "joint_names": list(joints), "definition_zh": definition,
           "coverage": round(coverage, 6), "valid_frame_count": count, "total_frame_count": total,
           "coverage_scope": "all_recorded_samples_in_estimated_motion_phase",
           "source_minimum_coverage": .7, "point_confidence_min": CONFIDENCE_MIN,
           "windows": [], "reason_codes": [],
           **{key: None for key in ("start", "end", "delta", "min", "max", "median", "mean", "range")}}
    provenance = measurement_provenance(samples)
    if provenance:
        row.update(measurement_provenance=provenance, point_confidence_min=None,
                   coverage_scope="in_frame_model_estimates_in_recorded_samples_not_visibility")
    if phase_reason:
        row["reason_codes"] = [phase_reason]
        return row
    reference_changed = any(_reference(sample) != _reference(samples[0]) for sample in samples[1:])
    if reference_changed:
        row["reason_codes"].append("subject_or_coordinate_reference_changed_no_phase_aggregate")
    diffs = [b.time - a.time for a, b in zip(samples, samples[1:]) if b.time > a.time]
    frame_diffs = [b.frame_index - a.frame_index for a, b in zip(samples, samples[1:]) if b.frame_index > a.frame_index]
    limit = min(160, statistics.median(diffs) * 1.8) if diffs else 160
    frame_limit = statistics.median(frame_diffs) * 1.8 if frame_diffs else 1
    segments, current = [], []
    for i, value in enumerate(values):
        continuous = (not current or (
            samples_are_continuous(samples[i - 1], samples[i], max_gap_ms=limit)
            and 0 < samples[i].frame_index - samples[i - 1].frame_index <= frame_limit
            and getattr(samples[i], "camera_compensated", False) == getattr(samples[i - 1], "camera_compensated", False)))
        if value is None or not continuous:
            if current:
                segments.append(current)
            current = []
        if value is not None:
            current.append(i)
    if current:
        segments.append(current)
    for segment in segments:
        if len(segment) < MIN_WINDOW_SAMPLES or samples[segment[-1]].time - samples[segment[0]].time < MIN_WINDOW_DURATION_MS:
            continue
        window = {"start_ms": samples[segment[0]].time, "end_ms": samples[segment[-1]].time,
                               "sample_count": len(segment), "source_frames": [samples[i].frame_index for i in segment],
                               "reference": _reference(samples[segment[0]]),
                               **_summary([values[i] for i in segment], kind)}
        if provenance:
            window["source_binding_start"] = {**samples[segment[0]].source_binding, "video_timestamp_ms": samples[segment[0]].time}
            window["source_binding_end"] = {**samples[segment[-1]].source_binding, "video_timestamp_ms": samples[segment[-1]].time}
            window["source_time_origin_ms"] = samples[segment[0]].source_binding["timestamp_ms"] - samples[segment[0]].time
        row["windows"].append(window)
    if not total or count * 10 < total * 7:
        row["reason_codes"].append("required_joint_coverage_below_70_percent")
    if not row["windows"]:
        row["reason_codes"].append("insufficient_continuous_joint_observations")
        return row
    complete = (len(row["windows"]) == 1 and count == total and samples[0].time == start
                and samples[-1].time == end and row["windows"][0]["sample_count"] == total)
    if complete:
        row.update({key: row["windows"][0][key] for key in ("start", "end", "delta", "min", "max", "median", "mean", "range")})
        row["status"] = "measured"
    else:
        row["status"] = "partial"
        row["reason_codes"].append("partial_windows_not_full_stage_change")
        if count * 10 >= total * 7 and not kind.startswith("axis_") and not reference_changed:
            observed = [v for v in values if v is not None]
            row.update({key: value for key, value in _summary(observed, kind).items() if key not in {"start", "end", "delta"}})
    return row


def analyze_indicator_observations(samples: list[Any], phases: list[Mapping[str, Any]], side: str) -> dict:
    """Return thirteen joint measurements per genuinely observed motion phase."""
    rows = []
    phase_map = {row.get("phase"): row for row in phases if isinstance(row, Mapping)}
    invalid_timeline = any(not _valid_sample(s) for s in samples) or any(
        b.time <= a.time or b.frame_index <= a.frame_index for a, b in zip(samples, samples[1:]))
    measured_phases = [phase_map[p] for p in PHASES if phase_map.get(p, {}).get("status") == "measured"]
    invalid_order = len(phase_map) != len(phases) or any(
        not _num(p.get("start_ms")) or not _num(p.get("end_ms")) or p["start_ms"] >= p["end_ms"] for p in measured_phases)
    if not invalid_order:
        invalid_order = any(a["end_ms"] > b["start_ms"] for a, b in zip(measured_phases, measured_phases[1:]))
    for name in PHASES:
        phase = phase_map.get(name, {})
        start, end = phase.get("start_ms"), phase.get("end_ms")
        valid_phase = _num(start) and _num(end) and start < end and phase.get("status") == "measured"
        reason = ("invalid_source_timeline" if invalid_timeline else "invalid_phase_order" if invalid_order
                  else "phase_not_observed" if not valid_phase else None)
        selected = [s for s in samples if start <= s.time <= end] if valid_phase and not invalid_timeline else []
        measurements = {key: _measure(selected, start, end, spec, reason) for key, spec in MEASUREMENTS.items()}
        rows.append({"phase": name, "start_ms": start if valid_phase else None, "end_ms": end if valid_phase else None,
                     "boundary_semantics": "estimated_motion_phase_not_formal_GS_stage", "measurements": measurements})
    return {"version": VERSION, "semantics": "joint_specific_2d_measurements_not_technical_grade",
            "measurement_provenance": measurement_provenance(samples),
            "racket_side": side if side in {"left", "right"} else "unknown", "phases": rows,
            "is_formal_score": False,
            "limitations_zh": ["逐关节70%有效帧是观测门槛，不是70分或技术合格阈值。",
                               "完整阶段变化不跨遮挡、身份或相机参照切换；局部数值保留真实起止时间。",
                               "肩髋角、头部位置及踝支撑仅为二维代理，不能替代三维转体、视线、承重、真实触球。"]}
