"""Serve-specific engineering rules in an already validated body coordinate frame.

The caller establishes source identity, continuous time and a serve phase before
calling this module. Raised arms alone never classify a serve. These rules do
not infer ball release, racket contact, landing contact or physical force.
"""
from __future__ import annotations

import math

import numpy as np


SERVE_METRICS = {
    "movement": (("knee_load", "发球屈膝蓄势", "deg", "以屈膝蓄势衔接向上伸展"),
                 ("leg_extension_continuity", "下肢伸展连贯", "ratio", "临近向上挥拍时同一腿连贯伸展，不追求锁直膝盖")),
    "preparation": (("serve_trophy", "持拍臂蓄势", "deg", "持拍侧上臂舒展，避免夹在躯干旁"),
                    ("serve_arm_coordination", "双臂蓄势配合", "fraction", "非持拍臂上伸与持拍臂蓄势相互配合")),
    "coordination": (("serve_extension_sequence", "上伸衔接", "phase_fraction", "下肢伸展与手臂向上挥出连续衔接"),
                     ("serve_extension", "下肢伸展", "deg", "从屈膝姿态连贯伸展，不只用手臂挥动")),
    "striking": (("serve_overhead_position", "向上挥拍位置", "torso", "手臂向身体上方舒展挥出"),
                 ("serve_arm_extension", "向上伸展空间", "arm_length", "保留舒展空间，不强行锁直肘部")),
    "follow_through": (("follow_path", "发球随挥延展", "ratio", "向上挥出后继续完成自然随挥"),
                       ("serve_recovery_control", "发球后再准备", "ratio", "随挥后连贯恢复可启动准备"),
                       ("follow_deceleration", "随挥速度衔接", "ratio", "主挥拍之后自然减速，减少短时反复加速，不要求停住球拍")),
}
SERVE_DRILLS = {
    "movement": ("连贯完成屈膝蓄势", "沿用自己的发球站姿，以屈膝蓄势衔接向上伸展。",
                 "做 2 组每组 8 次半速发球空挥：站稳、屈膝蓄势、向上伸展，再恢复准备。",
                 "同机位录 8 次发球，对比屈膝蓄势是否连续衔接向上伸展。"),
    "preparation": ("建立双臂蓄势配合", "让持拍侧上臂自然展开，非持拍臂的上伸与蓄势配合。",
                    "做 2 组每组 8 次慢速发球准备：双臂进入蓄势姿态后连续向上挥出，不追求额外抬高。",
                    "同机位录 8 次发球，对比持拍臂蓄势与双臂配合。"),
    "coordination": ("把下肢与上伸连起来", "从屈膝准备连续伸展，再让持拍臂向上挥出，减少中途脱节。",
                     "做 2 组每组 8 次半速空挥：屈膝、连贯上伸、随挥，先保持节奏再逐渐提速。",
                     "同机位录 8 次发球，对比上伸衔接和下肢伸展。"),
    "striking": ("向上舒展挥拍", "让持拍手向身体上方伸展，保留手臂空间，不强行锁直肘部。",
                 "做 2 组每组 8 次半速发球空挥：向上挥出后顺势随挥，保持身体平衡。",
                 "同机位录 8 次发球，对比向上挥拍位置和伸展空间。"),
    "follow_through": ("发球后重新准备", "完成向上挥出后自然随挥，再恢复能够启动的准备姿态。",
                       "做 2 组每组 8 次：发球空挥、完成随挥、小步调整、重新准备。",
                       "同机位录连续发球与恢复过程，对比随挥延展和发球后再准备。"),
}
SERVE_RUBRIC = {
    "version": "tennis-serve-profile-v1.2.0",
    "threshold_origin": "engineering_initial_values_not_document_numeric_grades",
    "classification_required": "source_bound_serve_phase_not_arm_height",
    "ball_toss_quality_assumed": False,
    "ball_contact_assumed": False,
    "ground_contact_assumed": False,
    "stance_width_graded": False,
    "body_up_projection_used_as_balance": False,
    "minimum_metric_coverage": .7,
    "parameters": {
        "knee_load": {"curve": "time_mean_saturating_ramp", "knots": [0, 25],
                      "measurement": "best_per_leg_time_mean_over_complete_preparation", "deeper_flexion_penalized": False},
        "leg_extension_continuity": {"curve": "shared_net_same_leg_extension_divided_by_total_angle_variation", "weight": "equal_with_other_observed_movement_metrics"},
        "follow_deceleration": {"curve": "shared_net_wrist_speed_reduction_divided_by_total_speed_variation", "weight": "equal_with_other_observed_follow_through_metrics"},
        "serve_trophy": {"curve": "trapezoid", "knots": [35, 65, 115, 155], "unit": "deg_upper_arm_to_torso_up"},
        "serve_arm_coordination": {"curve": "mean_simultaneous_arm_geometry", "support_arm_up_angle_good_max_deg": 45,
                                   "support_arm_up_angle_bad_deg": 95, "support_arm_height_saturation_torso": .35},
        "serve_extension_sequence": {"curve": "downward_ramp_late_or_reversed_sequence", "knots": [0, .5],
                                     "simultaneous_tolerance": "one_median_frame_interval", "long_gap_phase_fraction": .5},
        "serve_extension": {"curve": "saturating_ramp", "knots": [3, 20], "unit": "deg_same_leg_flexion_reduction"},
        "serve_overhead_position": {"curve": "saturating_ramp", "knots": [.1, .55], "unit": "torso_wrist_above_shoulder"},
        "serve_arm_extension": {"curve": "trapezoid", "knots": [.45, .85, 1, 1.05]},
        "serve_recovery_control": {"curve": "equal_mean_observed_motion_continuity_and_late_knee_readiness",
                                   "continuity": "caller_validated_directed_relative_rotation_excess_variation",
                                   "knee_measurement": "max_per_leg_p75_over_late_follow_through",
                                   "knee_knots": [0, 10], "deeper_flexion_penalized": False},
    },
    "internal_sources": ["VIS-SV:P0030", "VIS-SV:P0039", "VIS-SV:P0048", "VIS-SV:P0057"],
}


def _number(value):
    return type(value) in (int, float) and math.isfinite(value)


def _point(row, name):
    value = row.get("local", {}).get(name)
    if value is None:
        return None
    point = np.asarray(value, dtype=float)
    return point if point.shape == (3,) and np.isfinite(point).all() else None


def _ramp(value, low, high):
    return float(np.clip((value - low) / (high - low), 0., 1.)) * 100


def _band(value, a, b, c, d):
    return min(_ramp(value, a, b), 100 - _ramp(value, c, d))


def _scalar(values, coverage=.7):
    values = np.asarray([float(v) if _number(v) else np.nan for v in values])
    return values if len(values) and np.isfinite(values).mean() >= coverage else None


def _time_mean(values, times):
    values, times = np.asarray(values, dtype=float), np.asarray(times, dtype=float)
    if len(values) < 2 or len(values) != len(times):
        return None
    dt = np.diff(times)
    valid = np.isfinite(values[:-1]) & np.isfinite(values[1:])
    if not np.isfinite(times).all() or not (dt > 0).all() or dt[valid].sum() < .7 * dt.sum():
        return None
    return float(np.sum(dt[valid] * (values[:-1][valid] + values[1:][valid]) / 2) / dt[valid].sum())


def _up_angle(vector):
    length = float(np.linalg.norm(vector))
    return math.degrees(math.acos(float(np.clip(vector[1] / length, -1, 1)))) if length > 1e-8 else None


def _leg_release(rows):
    """One actual leg must extend; alternating visible legs cannot make a delta."""
    choices = []
    for side in ("left", "right"):
        values = _scalar([row.get(f"{side}_knee_flexion") for row in rows])
        if values is None:
            continue
        largest, previous_max = 0., None
        for value in values:
            if not np.isfinite(value):
                previous_max = None
                continue
            previous_max = value if previous_max is None else max(previous_max, value)
            largest = max(largest, previous_max - value)
        choices.append((largest, values))
    return max(choices, key=lambda item: item[0]) if choices else (None, None)


def _leg_load(rows):
    """Use one sufficiently observed leg, without mixing visibility changes."""
    loads = []
    for side in ("left", "right"):
        values = _scalar([row.get(f"{side}_knee_flexion") for row in rows])
        if values is not None:
            loads.append(float(np.nanquantile(values, .75)))
    return max(loads) if loads else None


def apply_serve_profile(rows, *, prep_end, peak, side, wrist, values, recovery_continuity=None):
    """Return measured serve subcriteria without changing arguments.

    ``rows.local`` is the caller's hip-centred, torso-scaled body basis. Unknown
    joint samples stay missing. This helper neither classifies nor fabricates a
    serve, a ball event, phase boundaries, or a missing performance score.
    ``recovery_continuity`` is an optional caller-measured (ratio, score) pair;
    no torso-up projection is interpreted as gravity or balance.
    """
    shared = {"follow_path", "leg_extension_continuity", "follow_deceleration"}
    out = {key: tuple(value) for key, value in values.items() if key in shared}
    if side not in {"left", "right"} or not 0 < prep_end <= peak < len(rows) or len(rows) < 6:
        return {}
    other = "left" if side == "right" else "right"
    times = np.asarray([row.get("time", np.nan) for row in rows], dtype=float)
    wrist = np.asarray(wrist, dtype=float)
    if wrist.shape != (len(rows), 3) or not np.isfinite(times).all() or np.any(np.diff(times) <= 0):
        return {}
    prep = rows[:prep_end]
    core = rows[max(0, peak - 2):min(len(rows), peak + 3)]
    post = rows[peak:]

    def put(key, value, score):
        if _number(float(value)) and _number(float(score)):
            out[key] = (float(value), float(np.clip(score, 0, 100)))

    knees = []
    for leg_side in ("left", "right"):
        observed = _scalar([row.get(f"{leg_side}_knee_flexion") for row in prep])
        if observed is not None:
            value = _time_mean(observed, times[:prep_end])
            score = _time_mean([_ramp(x, 0, 25) for x in observed], times[:prep_end])
            if value is not None and score is not None:
                knees.append((value, score))
    if knees:
        knee, score = max(knees, key=lambda item: item[1])
        put("knee_load", knee, score)
    trophy, cooperation = [], []
    for row in prep:
        shoulder, elbow = _point(row, f"{side}_shoulder"), _point(row, f"{side}_elbow")
        angle = _up_angle(elbow - shoulder) if shoulder is not None and elbow is not None else None
        trophy.append(angle)
        support_shoulder, support_wrist = _point(row, f"{other}_shoulder"), _point(row, f"{other}_wrist")
        if angle is None or support_shoulder is None or support_wrist is None:
            cooperation.append(None)
            continue
        vector = support_wrist - support_shoulder
        arm_angle = _up_angle(vector)
        if arm_angle is None:
            cooperation.append(None)
            continue
        up_score = min(100 - _ramp(arm_angle, 45, 95), _ramp(float(vector[1]), 0, .35))
        cooperation.append(min(_band(angle, 35, 65, 115, 155), up_score))
    trophy = _scalar(trophy)
    if trophy is not None:
        value = float(np.nanmedian(trophy[-max(3, len(trophy) // 2):]))
        put("serve_trophy", value, _band(value, 35, 65, 115, 155))
    cooperation = _scalar(cooperation)
    if cooperation is not None:
        score = float(np.nanmean(cooperation[-max(3, len(cooperation) // 2):]))
        put("serve_arm_coordination", score / 100, score)

    release, leg = _leg_release(rows[:peak + 1])
    if release is not None:
        put("serve_extension", release, _ramp(release, 3, 20))
        # Compare real adjacent derivatives, never bridge a missing observation.
        dt = np.diff(times[:peak + 1])
        knee_rate = -np.diff(leg) / dt
        arm_rate = np.diff(wrist[:peak + 1, 1]) / dt
        valid = np.isfinite(knee_rate) & np.isfinite(arm_rate)
        if valid.any():
            valid_positions = np.flatnonzero(valid)
            lower = int(valid_positions[np.argmax(np.round(knee_rate[valid], 10))]) + 1
            upper = int(valid_positions[np.argmax(np.round(arm_rate[valid], 10))]) + 1
            duration = times[peak] - times[0]
            tolerance = float(np.median(dt))
            disorder = max(0., times[lower] - times[upper] - tolerance)
            gap = max(0., times[upper] - times[lower] - .5 * duration)
            value = (disorder + gap) / duration
            observed_up = float(np.nanmax(wrist[:peak + 1, 1]) - np.nanmin(wrist[:peak + 1, 1]))
            score = 100 - _ramp(value, 0, .5) if release >= 3 and observed_up >= .2 and arm_rate[upper - 1] > 0 else 0.
            put("serve_extension_sequence", value, score)

    heights = []
    for row in core:
        shoulder, hand = _point(row, f"{side}_shoulder"), _point(row, f"{side}_wrist")
        heights.append(float(hand[1] - shoulder[1]) if hand is not None and shoulder is not None else None)
    heights = _scalar(heights)
    if heights is not None:
        value = float(np.nanquantile(heights, .75))
        put("serve_overhead_position", value, _ramp(value, .1, .55))
    reaches = _scalar([row.get(f"{side}_reach") for row in core])
    if reaches is not None:
        value = float(np.nanmedian(reaches))
        put("serve_arm_extension", value, _band(value, .45, .85, 1, 1.05))

    continuity_valid = (isinstance(recovery_continuity, (tuple, list)) and len(recovery_continuity) == 2
                        and all(_number(value) for value in recovery_continuity)
                        and recovery_continuity[0] >= 0 and 0 <= recovery_continuity[1] <= 100)
    if len(post) >= 5 and continuity_valid:
        count = max(3, len(post) // 2)
        late_knee = _leg_load(post[-count:])
        if late_knee is not None:
            ratio, continuity_score = recovery_continuity
            score = .5 * continuity_score + .5 * _ramp(late_knee, 0, 10)
            put("serve_recovery_control", ratio, score)
    return out
