from __future__ import annotations

import math
from typing import Any, Callable

import numpy as np

from rallymate_features.coordinates import (
    body_center,
    body_scale,
    hip_center,
    point_series,
    shoulder_center,
)
from rallymate_features.geometry import (
    angle_three_points_deg,
    orientation_deg,
    unwrap_axis_angle_deg,
    wrap_axis_angle_deg,
)
from rallymate_features.kinematics import irregular_derivative, scalar_acceleration, speed
from rallymate_features.fs01_fs02_features import (
    FS01_FS02_EVENT_FEATURE_NAMES,
    FS01_FS02_FEATURE_DEFINITIONS,
    FS01_FS02_FEATURE_VERSION,
    clear_fs01_fs02_feature_cache,
    summarize_fs01_fs02_event_feature,
)
from rallymate_features.fs09_features import (
    FS09_EVENT_FEATURE_NAMES,
    FS09_FEATURE_VERSION,
    FS09_SERIES_FEATURE_NAMES,
    clear_fs09_feature_cache,
    raw_fs09_series,
    summarize_fs09_event_feature,
)
from rallymate_features.schemas import EventInterval, FeatureResult, PoseSequence
from rallymate_features.smoothing import smooth_series


FEATURE_LIBRARY_VERSION = "rallymate-features-v0.1.0"
MIN_VALID_FRACTION = 0.50
SCORING_FEATURE_NAMES = (
    "hip_center_y_body",
    "body_center_speed_body_s",
    "left_knee_flexion_deg",
    "right_knee_flexion_deg",
    "torso_lean_deg",
    "stance_width_body",
    "hip_center_relative_to_ankle_support",
    "body_center_deceleration_body_s2",
    "stability_duration_ms",
    "shoulder_hip_angular_velocity",
)
_SCALE_NORMALIZED_FEATURES = {
    "hip_center_y_body",
    "body_center_speed_body_s",
    "stance_width_body",
    "body_center_deceleration_body_s2",
    "hip_center_velocity_x_body_s",
    "hip_center_velocity_y_body_s",
    "hip_center_speed_body_s",
    "hip_center_relative_to_ankle_midpoint_x_body",
    "hip_center_relative_to_ankle_midpoint_y_body",
    "hip_center_speed_trend_body_s2",
    "hip_center_deceleration_body_s2",
    "left_ankle_speed_body_s",
    "right_ankle_speed_body_s",
}
_SERIES_CACHE: dict[int, tuple[PoseSequence, dict[str, tuple[np.ndarray, np.ndarray]]]] = {}
_BODY_SCALE_CACHE: dict[int, tuple[PoseSequence, np.ndarray]] = {}


FEATURE_DEFINITIONS = {
    "hip_center_y_body": {
        "version": "1.0.0",
        "unit": "body",
        "aggregation": "median",
        "required_joints": ["left_hip", "right_hip", "left_ankle", "right_ankle"],
        "definition": "vertical distance from ankle-support midpoint to hip center divided by event median body scale",
    },
    "body_center_speed_body_s": {
        "version": "1.0.0",
        "unit": "body/s",
        "aggregation": "peak",
        "required_joints": ["left_shoulder", "right_shoulder", "left_hip", "right_hip"],
        "definition": "image-plane body-center speed divided by event median body scale using timestamp_ms",
    },
    "left_knee_flexion_deg": {
        "version": "1.0.0",
        "unit": "deg",
        "aggregation": "peak",
        "required_joints": ["left_hip", "left_knee", "left_ankle"],
        "definition": "180 degrees minus internal hip-knee-ankle angle",
    },
    "right_knee_flexion_deg": {
        "version": "1.0.0",
        "unit": "deg",
        "aggregation": "peak",
        "required_joints": ["right_hip", "right_knee", "right_ankle"],
        "definition": "180 degrees minus internal hip-knee-ankle angle",
    },
    "torso_lean_deg": {
        "version": "1.0.0",
        "unit": "deg",
        "aggregation": "peak_absolute_signed",
        "required_joints": ["left_shoulder", "right_shoulder", "left_hip", "right_hip"],
        "definition": "signed torso angle from image vertical; image-plane feature only",
    },
    "stance_width_body": {
        "version": "1.0.0",
        "unit": "body",
        "aggregation": "median",
        "required_joints": ["left_ankle", "right_ankle"],
        "definition": "ankle distance divided by event median body scale",
    },
    "hip_center_relative_to_ankle_support": {
        "version": "1.0.0",
        "unit": "ratio",
        "aggregation": "peak_deviation_from_mid_support",
        "required_joints": ["left_hip", "right_hip", "left_ankle", "right_ankle"],
        "definition": "projection of hip center on left-to-right ankle axis; 0 left ankle, 1 right ankle",
    },
    "body_center_deceleration_body_s2": {
        "version": "1.0.0",
        "unit": "body/s2",
        "aggregation": "peak_positive",
        "required_joints": ["left_shoulder", "right_shoulder", "left_hip", "right_hip"],
        "definition": "negative derivative of smoothed normalized body-center speed using timestamp_ms",
    },
    "stability_duration_ms": {
        "version": "0.2.0-provisional-envelope-evidence",
        "unit": "ms",
        "aggregation": "longest_contiguous_stable_run",
        "required_joints": ["left_shoulder", "right_shoulder", "left_hip", "right_hip"],
        "definition": "longest run below event-adaptive speed and shoulder-hip angular-velocity envelopes",
    },
    "shoulder_hip_angular_velocity": {
        "version": "1.0.0",
        "unit": "deg/s",
        "aggregation": "peak_absolute_signed",
        "required_joints": ["left_shoulder", "right_shoulder", "left_hip", "right_hip"],
        "definition": "timestamp derivative of unwrapped shoulder-axis minus hip-axis angle",
    },
    "left_ankle_shank_foot_angle_deg": {
        "version": "0.2.0-fine-foot-diagnostic",
        "unit": "deg",
        "aggregation": "median",
        "required_joints": ["left_knee", "left_ankle", "left_big_toe", "left_small_toe"],
        "definition": "2D angle knee-ankle-forefoot-center from a registered fine-foot topology; diagnostic, not a calibrated dorsiflexion grade",
    },
    "right_ankle_shank_foot_angle_deg": {
        "version": "0.2.0-fine-foot-diagnostic",
        "unit": "deg",
        "aggregation": "median",
        "required_joints": ["right_knee", "right_ankle", "right_big_toe", "right_small_toe"],
        "definition": "2D angle knee-ankle-forefoot-center from a registered fine-foot topology; diagnostic, not a calibrated dorsiflexion grade",
    },
}

FEATURE_DEFINITIONS.update(
    {
        "hip_center_velocity_x_body_s": {
            "version": FS09_FEATURE_VERSION,
            "unit": "body/s",
            "aggregation": "peak_absolute_signed",
            "required_joints": ["left_hip", "right_hip"],
            "definition": "image-plane hip-center proxy horizontal velocity; positive is image-right; timestamp_ms derivative",
        },
        "hip_center_velocity_y_body_s": {
            "version": FS09_FEATURE_VERSION,
            "unit": "body/s",
            "aggregation": "peak_absolute_signed",
            "required_joints": ["left_hip", "right_hip"],
            "definition": "image-plane hip-center proxy vertical velocity; positive is image-down; timestamp_ms derivative",
        },
        "hip_center_speed_body_s": {
            "version": FS09_FEATURE_VERSION,
            "unit": "body/s",
            "aggregation": "peak",
            "required_joints": ["left_hip", "right_hip"],
            "definition": "image-plane hip-center proxy speed using timestamp_ms and event body scale",
        },
        "hip_center_motion_direction_deg": {
            "version": FS09_FEATURE_VERSION,
            "unit": "deg",
            "aggregation": "circular_mean",
            "required_joints": ["left_hip", "right_hip"],
            "definition": "hip-center proxy motion direction; 0 image-right and 90 image-up",
        },
        "hip_center_relative_to_ankle_midpoint_x_body": {
            "version": FS09_FEATURE_VERSION,
            "unit": "body",
            "aggregation": "peak_absolute_signed",
            "required_joints": ["left_hip", "right_hip", "left_ankle", "right_ankle"],
            "definition": "horizontal hip-center proxy offset from ankle midpoint divided by event body scale; positive image-right",
        },
        "hip_center_relative_to_ankle_midpoint_y_body": {
            "version": FS09_FEATURE_VERSION,
            "unit": "body",
            "aggregation": "peak_absolute_signed",
            "required_joints": ["left_hip", "right_hip", "left_ankle", "right_ankle"],
            "definition": "vertical hip-center proxy offset from ankle midpoint divided by event body scale; positive image-down",
        },
        "hip_center_speed_trend_body_s2": {
            "version": FS09_FEATURE_VERSION,
            "unit": "body/s2",
            "aggregation": "peak_absolute_signed",
            "required_joints": ["left_hip", "right_hip"],
            "definition": "signed timestamp_ms derivative of hip-center proxy speed; negative means slowing",
        },
        "hip_center_deceleration_body_s2": {
            "version": FS09_FEATURE_VERSION,
            "unit": "body/s2",
            "aggregation": "peak_positive",
            "required_joints": ["left_hip", "right_hip"],
            "definition": "negative timestamp_ms derivative of hip-center proxy speed; positive means slowing",
        },
        "left_ankle_speed_body_s": {
            "version": FS09_FEATURE_VERSION,
            "unit": "body/s",
            "aggregation": "peak",
            "required_joints": ["left_ankle"],
            "definition": "left ankle image-plane speed using timestamp_ms; not foot-ground contact",
        },
        "right_ankle_speed_body_s": {
            "version": FS09_FEATURE_VERSION,
            "unit": "body/s",
            "aggregation": "peak",
            "required_joints": ["right_ankle"],
            "definition": "right ankle image-plane speed using timestamp_ms; not foot-ground contact",
        },
        "left_knee_flexion_velocity_deg_s": {
            "version": FS09_FEATURE_VERSION,
            "unit": "deg/s",
            "aggregation": "peak_absolute_signed",
            "required_joints": ["left_hip", "left_knee", "left_ankle"],
            "definition": "timestamp_ms derivative of left 2D knee-flexion proxy; positive means increasing flexion",
        },
        "right_knee_flexion_velocity_deg_s": {
            "version": FS09_FEATURE_VERSION,
            "unit": "deg/s",
            "aggregation": "peak_absolute_signed",
            "required_joints": ["right_hip", "right_knee", "right_ankle"],
            "definition": "timestamp_ms derivative of right 2D knee-flexion proxy; positive means increasing flexion",
        },
        "hip_center_speed_drop_body_s": {
            "version": FS09_FEATURE_VERSION,
            "unit": "body/s",
            "aggregation": "event_early_median_minus_late_median",
            "required_joints": ["left_hip", "right_hip"],
            "definition": "hip-center proxy speed early-event median minus late-event median; positive means slowing",
        },
        "left_ankle_speed_drop_body_s": {
            "version": FS09_FEATURE_VERSION,
            "unit": "body/s",
            "aggregation": "event_early_median_minus_late_median",
            "required_joints": ["left_ankle"],
            "definition": "left ankle speed early-event median minus late-event median; kinematic proxy, not landing",
        },
        "right_ankle_speed_drop_body_s": {
            "version": FS09_FEATURE_VERSION,
            "unit": "body/s",
            "aggregation": "event_early_median_minus_late_median",
            "required_joints": ["right_ankle"],
            "definition": "right ankle speed early-event median minus late-event median; kinematic proxy, not landing",
        },
        "left_knee_flexion_change_deg": {
            "version": FS09_FEATURE_VERSION,
            "unit": "deg",
            "aggregation": "event_late_median_minus_early_median",
            "required_joints": ["left_hip", "left_knee", "left_ankle"],
            "definition": "left 2D knee-flexion late-event median minus early-event median",
        },
        "right_knee_flexion_change_deg": {
            "version": FS09_FEATURE_VERSION,
            "unit": "deg",
            "aggregation": "event_late_median_minus_early_median",
            "required_joints": ["right_hip", "right_knee", "right_ankle"],
            "definition": "right 2D knee-flexion late-event median minus early-event median",
        },
        "braking_side_code": {
            "version": FS09_FEATURE_VERSION,
            "unit": "code",
            "aggregation": "positive_net_drop_then_positive_local_slowdown",
            "required_joints": ["left_ankle", "right_ankle"],
            "definition": "kinematic diagnostic: -1 left, 0 indeterminate/bilateral, 1 right; positive net speed drop is primary and strongest positive local slowdown is the no-net-drop fallback; not measured landing, contact, or force",
        },
        "braking_ankle_speed_drop_body_s": {
            "version": FS09_FEATURE_VERSION,
            "unit": "body/s",
            "aggregation": "selected_ankle_early_median_minus_late_median",
            "required_joints": ["left_ankle", "right_ankle"],
            "definition": "event-edge speed drop for the ankle selected by braking_side_code; may be zero or negative when a local slowdown exists without positive net braking; kinematic proxy only",
        },
        "braking_ankle_slowdown_to_hip_deceleration_ms": {
            "version": FS09_FEATURE_VERSION,
            "unit": "ms",
            "aggregation": "peak_time_delta",
            "required_joints": ["left_hip", "right_hip", "left_ankle", "right_ankle"],
            "definition": "hip proxy peak-deceleration time minus selected ankle peak-slowdown time using timestamp_ms",
        },
        "hip_deceleration_to_double_support_proxy_ms": {
            "version": FS09_FEATURE_VERSION,
            "unit": "ms",
            "aggregation": "phase_proxy_start_time_delta",
            "required_joints": ["left_hip", "right_hip", "left_ankle", "right_ankle"],
            "definition": "simultaneous low-ankle-motion proxy start minus hip peak-deceleration time; not contact timing",
        },
        "hip_height_delta_body": {
            "version": FS09_FEATURE_VERSION,
            "unit": "body",
            "aggregation": "event_late_median_minus_early_median",
            "required_joints": ["left_hip", "right_hip", "left_ankle", "right_ankle"],
            "definition": "late minus early hip height above ankle midpoint; negative means visual lowering",
        },
        "torso_lean_variability_deg": {
            "version": FS09_FEATURE_VERSION,
            "unit": "deg",
            "aggregation": "event_population_standard_deviation",
            "required_joints": ["left_shoulder", "right_shoulder", "left_hip", "right_hip"],
            "definition": "population standard deviation of signed image-plane torso lean within the event",
        },
        "shoulder_hip_angular_velocity_change_deg_s": {
            "version": FS09_FEATURE_VERSION,
            "unit": "deg/s",
            "aggregation": "event_late_absolute_median_minus_early_absolute_median",
            "required_joints": ["left_shoulder", "right_shoulder", "left_hip", "right_hip"],
            "definition": "late minus early magnitude of shoulder-hip separation angular velocity; negative means slowing",
        },
        "double_support_proxy_duration_ms": {
            "version": FS09_FEATURE_VERSION,
            "unit": "ms",
            "aggregation": "longest_simultaneous_event_low_ankle_motion_run",
            "required_joints": ["left_ankle", "right_ankle"],
            "definition": "longest simultaneous low-ankle-motion run under event-adaptive signal envelopes; not ground contact or support force",
        },
    }
)

FEATURE_DEFINITIONS.update(FS01_FS02_FEATURE_DEFINITIONS)


def _event_indexes(sequence: PoseSequence, event: EventInterval) -> np.ndarray:
    return np.flatnonzero(
        (sequence.timestamp_ms >= event.start_ms)
        & (sequence.timestamp_ms <= event.end_ms)
    )


def _event_scale(sequence: PoseSequence, indexes: np.ndarray) -> float | None:
    cache_key = id(sequence)
    cached = _BODY_SCALE_CACHE.get(cache_key)
    if cached is None or cached[0] is not sequence:
        scale, _ = body_scale(sequence)
        _BODY_SCALE_CACHE[cache_key] = (sequence, scale)
    else:
        scale = cached[1]
    values = scale[indexes]
    values = values[np.isfinite(values) & (values > 1e-6)]
    return float(np.median(values)) if values.size else None


def _series_payload(
    sequence: PoseSequence,
    indexes: np.ndarray,
    values: np.ndarray,
) -> list[dict[str, Any]]:
    payload = []
    for index, value in zip(indexes, values[indexes]):
        array_value = np.asarray(value)
        if array_value.ndim == 0:
            serialized_value: float | list[float | None] | None = (
                round(float(array_value), 8)
                if np.isfinite(array_value)
                else None
            )
        else:
            serialized_value = [
                round(float(component), 8) if np.isfinite(component) else None
                for component in array_value.reshape(-1)
            ]
        payload.append(
            {
                "timestamp_ms": int(sequence.timestamp_ms[index]),
                "source_frame": int(sequence.source_frames[index]),
                "value": serialized_value,
            }
        )
    return payload


def _feature_confidence(
    sequence: PoseSequence,
    indexes: np.ndarray,
    required_joints: list[str],
    valid: np.ndarray,
) -> float:
    scores = []
    for joint in required_joints:
        if joint not in sequence.confidence:
            continue
        values = sequence.confidence[joint][indexes]
        values = values[np.isfinite(values)]
        if values.size:
            scores.append(float(np.mean(np.clip(values, 0.0, 1.0))))
    keypoint_confidence = statistics_mean(scores) if scores else 0.0
    valid_fraction = float(np.mean(valid[indexes])) if indexes.size else 0.0
    return max(0.0, min(1.0, keypoint_confidence * valid_fraction))


def statistics_mean(values: list[float]) -> float:
    return sum(values) / len(values) if values else 0.0


def _representative_frames(
    sequence: PoseSequence,
    indexes: np.ndarray,
    values: np.ndarray,
    aggregation: str,
) -> list[int]:
    eligible = indexes[np.isfinite(values[indexes])]
    if eligible.size == 0:
        return []
    selected: list[int] = []
    eligible_values = values[eligible]
    if aggregation == "median":
        median = float(np.median(eligible_values))
        selected.append(int(eligible[np.argmin(np.abs(eligible_values - median))]))
    elif aggregation in {"peak", "peak_positive"}:
        selected.append(int(eligible[np.argmax(eligible_values)]))
    elif aggregation == "peak_deviation_from_mid_support":
        selected.append(int(eligible[np.argmax(np.abs(eligible_values - 0.5))]))
    elif aggregation == "circular_mean":
        radians = np.radians(eligible_values)
        mean = math.degrees(
            math.atan2(float(np.mean(np.sin(radians))), float(np.mean(np.cos(radians))))
        )
        distance = np.abs(((eligible_values - mean + 180.0) % 360.0) - 180.0)
        selected.append(int(eligible[np.argmin(distance)]))
    else:
        selected.append(int(eligible[np.argmax(np.abs(eligible_values))]))
    selected.extend([int(eligible[0]), int(eligible[-1])])
    return sorted({int(sequence.source_frames[index]) for index in selected})


def _aggregate(values: np.ndarray, indexes: np.ndarray, aggregation: str) -> float | None:
    eligible = values[indexes]
    eligible = eligible[np.isfinite(eligible)]
    if eligible.size == 0:
        return None
    if aggregation == "median":
        return float(np.median(eligible))
    if aggregation in {"peak", "peak_positive"}:
        return float(np.max(eligible))
    if aggregation == "peak_deviation_from_mid_support":
        value = eligible[np.argmax(np.abs(eligible - 0.5))]
        return float(value)
    if aggregation == "peak_absolute_signed":
        return float(eligible[np.argmax(np.abs(eligible))])
    if aggregation == "circular_mean":
        radians = np.radians(eligible)
        sine = float(np.mean(np.sin(radians)))
        cosine = float(np.mean(np.cos(radians)))
        if abs(sine) <= 1e-12 and abs(cosine) <= 1e-12:
            return None
        return float(math.degrees(math.atan2(sine, cosine)))
    raise ValueError(f"unsupported aggregation: {aggregation}")


def _hip_center_y(sequence: PoseSequence, scale: float) -> np.ndarray:
    hips, hip_valid = hip_center(sequence)
    left_ankle, left_valid = point_series(sequence, "left_ankle")
    right_ankle, right_valid = point_series(sequence, "right_ankle")
    support = (left_ankle + right_ankle) / 2.0
    valid = hip_valid & left_valid & right_valid
    values = (support[:, 1] - hips[:, 1]) / scale
    values[~valid] = np.nan
    return values


def _body_speed(sequence: PoseSequence, scale: float) -> np.ndarray:
    center, valid = body_center(sequence)
    smoothed = smooth_series(sequence.timestamp_ms, center)
    values = speed(sequence.timestamp_ms, smoothed) / scale
    values[~valid] = np.nan
    return values


def _knee_flexion(sequence: PoseSequence, side: str) -> np.ndarray:
    hip, _ = point_series(sequence, f"{side}_hip")
    knee, _ = point_series(sequence, f"{side}_knee")
    ankle, _ = point_series(sequence, f"{side}_ankle")
    return 180.0 - angle_three_points_deg(hip, knee, ankle)


def _torso_lean(sequence: PoseSequence) -> np.ndarray:
    shoulders, shoulder_valid = shoulder_center(sequence)
    hips, hip_valid = hip_center(sequence)
    vector = shoulders - hips
    values = np.degrees(np.arctan2(vector[:, 0], -vector[:, 1]))
    values[~(shoulder_valid & hip_valid)] = np.nan
    return values


def _stance_width(sequence: PoseSequence, scale: float) -> np.ndarray:
    left, left_valid = point_series(sequence, "left_ankle")
    right, right_valid = point_series(sequence, "right_ankle")
    values = np.linalg.norm(right - left, axis=1) / scale
    values[~(left_valid & right_valid)] = np.nan
    return values


def _hip_support(sequence: PoseSequence) -> np.ndarray:
    hips, hip_valid = hip_center(sequence)
    left, left_valid = point_series(sequence, "left_ankle")
    right, right_valid = point_series(sequence, "right_ankle")
    axis = right - left
    denominator = np.sum(axis * axis, axis=1)
    values = np.full(denominator.shape, np.nan, dtype=np.float64)
    np.divide(
        np.sum((hips - left) * axis, axis=1),
        denominator,
        out=values,
        where=denominator > 1e-12,
    )
    values[~(hip_valid & left_valid & right_valid & (denominator > 1e-12))] = np.nan
    return values


def _angular_velocity(sequence: PoseSequence) -> np.ndarray:
    left_shoulder, ls_valid = point_series(sequence, "left_shoulder")
    right_shoulder, rs_valid = point_series(sequence, "right_shoulder")
    left_hip, lh_valid = point_series(sequence, "left_hip")
    right_hip, rh_valid = point_series(sequence, "right_hip")
    shoulder_angle = orientation_deg(right_shoulder - left_shoulder)
    hip_angle = orientation_deg(right_hip - left_hip)
    separation = wrap_axis_angle_deg(shoulder_angle - hip_angle)
    separation[~(ls_valid & rs_valid & lh_valid & rh_valid)] = np.nan
    smoothed = smooth_series(sequence.timestamp_ms, unwrap_axis_angle_deg(separation))
    return irregular_derivative(sequence.timestamp_ms, smoothed[:, None])[:, 0]


def _ankle_shank_foot_angle(sequence: PoseSequence, side: str) -> np.ndarray:
    knee, knee_valid = point_series(sequence, f"{side}_knee")
    ankle, ankle_valid = point_series(sequence, f"{side}_ankle")
    big_toe, big_valid = point_series(sequence, f"{side}_big_toe")
    small_toe, small_valid = point_series(sequence, f"{side}_small_toe")
    forefoot = (big_toe + small_toe) / 2.0
    values = angle_three_points_deg(knee, ankle, forefoot)
    values[~(knee_valid & ankle_valid & big_valid & small_valid)] = np.nan
    return values


def _raw_series(feature_name: str, sequence: PoseSequence, scale: float) -> np.ndarray:
    if feature_name in FS09_SERIES_FEATURE_NAMES:
        return raw_fs09_series(feature_name, sequence, scale)
    if feature_name == "hip_center_y_body":
        return _hip_center_y(sequence, scale)
    if feature_name == "body_center_speed_body_s":
        return _body_speed(sequence, scale)
    if feature_name == "left_knee_flexion_deg":
        return _knee_flexion(sequence, "left")
    if feature_name == "right_knee_flexion_deg":
        return _knee_flexion(sequence, "right")
    if feature_name == "torso_lean_deg":
        return _torso_lean(sequence)
    if feature_name == "stance_width_body":
        return _stance_width(sequence, scale)
    if feature_name == "hip_center_relative_to_ankle_support":
        return _hip_support(sequence)
    if feature_name == "body_center_deceleration_body_s2":
        body_speed = _body_speed(sequence, scale)
        smoothed_speed = smooth_series(sequence.timestamp_ms, body_speed)
        return -scalar_acceleration(sequence.timestamp_ms, smoothed_speed)
    if feature_name == "shoulder_hip_angular_velocity":
        return _angular_velocity(sequence)
    if feature_name == "left_ankle_shank_foot_angle_deg":
        return _ankle_shank_foot_angle(sequence, "left")
    if feature_name == "right_ankle_shank_foot_angle_deg":
        return _ankle_shank_foot_angle(sequence, "right")
    raise ValueError(f"unknown feature: {feature_name}")


def _cached_series(
    sequence: PoseSequence, feature_name: str
) -> tuple[np.ndarray, np.ndarray]:
    cache_key = id(sequence)
    cached = _SERIES_CACHE.get(cache_key)
    if cached is None or cached[0] is not sequence:
        cached = (sequence, {})
        _SERIES_CACHE[cache_key] = cached
    series_cache = cached[1]
    if feature_name not in series_cache:
        raw = _raw_series(feature_name, sequence, 1.0)
        smoothed = smooth_series(sequence.timestamp_ms, raw)
        series_cache[feature_name] = (raw, smoothed)
    return series_cache[feature_name]


def clear_feature_cache(sequence: PoseSequence | None = None) -> None:
    if sequence is None:
        _SERIES_CACHE.clear()
        _BODY_SCALE_CACHE.clear()
    else:
        _SERIES_CACHE.pop(id(sequence), None)
        _BODY_SCALE_CACHE.pop(id(sequence), None)
    clear_fs01_fs02_feature_cache(sequence)
    clear_fs09_feature_cache(sequence)


def stability_duration_from_series(
    timestamp_ms: np.ndarray,
    speed_body_s: np.ndarray,
    absolute_angular_velocity_deg_s: np.ndarray,
) -> tuple[int | None, np.ndarray, dict[str, Any]]:
    """Apply the provisional event-adaptive stability envelope to two series.

    This pure function contains the production aggregation used by
    ``stability_duration_ms``.  Callers may provide the production-smoothed
    inputs or the serialized pre-extra-smoothing inputs for an exact
    counterfactual; thresholds are recomputed from the supplied series.
    """

    timestamps = np.asarray(timestamp_ms, dtype=np.int64)
    speed_values = np.asarray(speed_body_s, dtype=np.float64)
    angular_values = np.asarray(
        absolute_angular_velocity_deg_s, dtype=np.float64
    )
    if (
        timestamps.ndim != 1
        or speed_values.ndim != 1
        or angular_values.ndim != 1
        or timestamps.size != speed_values.size
        or timestamps.size != angular_values.size
    ):
        raise ValueError("stability series must be aligned one-dimensional arrays")
    if timestamps.size > 1 and np.any(np.diff(timestamps) <= 0):
        raise ValueError("stability timestamps must be strictly increasing")

    joint_valid = np.isfinite(speed_values) & np.isfinite(angular_values)
    valid_count = int(joint_valid.sum())
    valid_fraction = (
        float(joint_valid.mean()) if joint_valid.size else 0.0
    )
    if valid_count < 3:
        return None, np.zeros(timestamps.shape, dtype=bool), {
            "reason": "insufficient_valid_speed_and_angular_velocity_samples",
            "valid_sample_count": valid_count,
            "sample_count": int(timestamps.size),
            "valid_fraction": valid_fraction,
            "speed_limit_body_s": None,
            "angular_velocity_limit_deg_s": None,
            "median_dt_ms": None,
            "longest_run_local_indexes": None,
        }

    valid_speed = speed_values[joint_valid]
    valid_angular = angular_values[joint_valid]
    speed_limit = float(np.percentile(valid_speed, 35))
    angular_limit = float(np.percentile(valid_angular, 50))
    stable = joint_valid & (speed_values <= speed_limit) & (
        angular_values <= angular_limit
    )
    median_dt = int(np.median(np.diff(timestamps))) if timestamps.size > 1 else 0
    longest_start = longest_end = None
    start = None
    for position, is_stable in enumerate(np.append(stable, False)):
        if is_stable and start is None:
            start = position
        if not is_stable and start is not None:
            end = position - 1
            if longest_start is None or end - start > longest_end - longest_start:
                longest_start, longest_end = start, end
            start = None
    if longest_start is None:
        duration = 0
        longest = None
    else:
        duration = int(
            timestamps[longest_end] - timestamps[longest_start] + median_dt
        )
        longest = [int(longest_start), int(longest_end)]
    return duration, stable, {
        "reason": "computed_provisional_event_adaptive_envelope",
        "valid_sample_count": valid_count,
        "sample_count": int(timestamps.size),
        "valid_fraction": valid_fraction,
        "speed_limit_body_s": speed_limit,
        "angular_velocity_limit_deg_s": angular_limit,
        "median_dt_ms": median_dt,
        "longest_run_local_indexes": longest,
    }


def _stability_feature(
    sequence: PoseSequence,
    event: EventInterval,
    indexes: np.ndarray,
    scale: float,
) -> FeatureResult:
    definition = FEATURE_DEFINITIONS["stability_duration_ms"]
    raw_speed, smoothed_speed = _cached_series(
        sequence, "body_center_speed_body_s"
    )
    raw_body_speed = raw_speed / scale
    body_speed = smoothed_speed / scale
    raw_angular, cached_angular = _cached_series(
        sequence, "shoulder_hip_angular_velocity"
    )
    raw_absolute_angular_velocity = np.abs(raw_angular)
    angular_velocity = np.abs(cached_angular)
    speed_values = body_speed[indexes]
    angular_values = angular_velocity[indexes]
    duration, stable, envelope = stability_duration_from_series(
        sequence.timestamp_ms[indexes],
        speed_values,
        angular_values,
    )
    if duration is None:
        return FeatureResult(
            feature_name="stability_duration_ms",
            feature_version=definition["version"],
            value=None,
            unit=definition["unit"],
            confidence=0.0,
            valid=False,
            reason="insufficient_valid_speed_and_angular_velocity_samples",
            source_frames=[],
            raw_value=None,
            smoothed_value=None,
            provenance={
                "library_version": FEATURE_LIBRARY_VERSION,
                "event_id": event.event_id,
                "envelope_status": "provisional_event_adaptive_not_scoring_threshold",
                "envelope_diagnostics": envelope,
            },
        )
    longest = envelope["longest_run_local_indexes"]
    if longest is None:
        frames = []
    else:
        first_index = indexes[int(longest[0])]
        last_index = indexes[int(longest[1])]
        frames = [
            int(sequence.source_frames[first_index]),
            int(sequence.source_frames[last_index]),
        ]
    valid_fraction = float(envelope["valid_fraction"])
    return FeatureResult(
        feature_name="stability_duration_ms",
        feature_version=definition["version"],
        value=duration,
        unit=definition["unit"],
        confidence=valid_fraction,
        valid=valid_fraction >= MIN_VALID_FRACTION,
        reason=(
            "valid_provisional_event_adaptive_envelope"
            if valid_fraction >= MIN_VALID_FRACTION
            else "valid_fraction_below_quality_gate"
        ),
        source_frames=frames,
        raw_value={
            "speed": _series_payload(sequence, indexes, raw_body_speed),
            "absolute_angular_velocity": _series_payload(
                sequence, indexes, raw_absolute_angular_velocity
            ),
        },
        smoothed_value={
            "series": {
                "speed": _series_payload(sequence, indexes, body_speed),
                "absolute_angular_velocity": _series_payload(
                    sequence, indexes, angular_velocity
                ),
            },
            "stable_mask": [bool(value) for value in stable],
            "speed_limit_body_s": round(
                float(envelope["speed_limit_body_s"]), 8
            ),
            "angular_velocity_limit_deg_s": round(
                float(envelope["angular_velocity_limit_deg_s"]), 8
            ),
        },
        provenance={
            "library_version": FEATURE_LIBRARY_VERSION,
            "event_id": event.event_id,
            "aggregation": definition["aggregation"],
            "envelope_status": "provisional_event_adaptive_not_scoring_threshold",
            "timestamp_source": "timestamp_ms",
            "evidence_contract": "raw_pre_extra_smoothing_and_smoothed_series_v1",
            "envelope_diagnostics": envelope,
        },
    )


def _fs09_event_summary_feature(
    sequence: PoseSequence,
    event: EventInterval,
    indexes: np.ndarray,
    scale: float,
    feature_name: str,
) -> FeatureResult:
    definition = FEATURE_DEFINITIONS[feature_name]
    summary = summarize_fs09_event_feature(
        feature_name,
        sequence,
        indexes,
        scale,
    )
    valid_mask = np.asarray(summary.valid_mask, dtype=bool)
    valid_fraction = float(valid_mask[indexes].mean()) if indexes.size else 0.0
    confidence = _feature_confidence(
        sequence,
        indexes,
        definition["required_joints"],
        valid_mask,
    )
    is_valid = summary.value is not None and valid_fraction >= MIN_VALID_FRACTION
    evidence_indexes = sorted(
        {
            int(index)
            for index in summary.evidence_indexes
            if 0 <= int(index) < sequence.source_frames.size
        }
    )
    return FeatureResult(
        feature_name=feature_name,
        feature_version=definition["version"],
        value=(
            round(float(summary.value), 8)
            if summary.value is not None and not isinstance(summary.value, int)
            else summary.value
        ),
        unit=definition["unit"],
        confidence=confidence,
        valid=is_valid,
        reason=(
            summary.reason
            if is_valid
            else "valid_fraction_below_quality_gate"
            if summary.value is not None
            else summary.reason
        ),
        source_frames=[
            int(sequence.source_frames[index]) for index in evidence_indexes
        ],
        raw_value={
            name: _series_payload(sequence, indexes, values)
            for name, values in summary.raw_series.items()
        },
        smoothed_value={
            "series": {
                name: _series_payload(sequence, indexes, values)
                for name, values in summary.smoothed_series.items()
            },
            "summary": summary.diagnostics,
        },
        provenance={
            "library_version": FEATURE_LIBRARY_VERSION,
            "fs09_feature_version": FS09_FEATURE_VERSION,
            "event_id": event.event_id,
            "event_code": event.event_code,
            "person_track_id": event.person_track_id,
            "aggregation": definition["aggregation"],
            "definition": definition["definition"],
            "timestamp_source": "timestamp_ms",
            "body_scale": scale,
            "valid_fraction": round(valid_fraction, 6),
            "measurement_status": "pose_proxy_not_calibrated_for_grade",
            "score_threshold_version": None,
            "smoothing": {"max_gap_ms": 160, "radius_ms": 100},
            **(
                {
                    "evidence_contract": (
                        "fs09_phase_timing_raw_speed_and_prepared_series_v1"
                    )
                }
                if feature_name
                in {
                    "braking_ankle_slowdown_to_hip_deceleration_ms",
                    "hip_deceleration_to_double_support_proxy_ms",
                }
                else {}
            ),
        },
    )


def _fs01_fs02_event_summary_feature(
    sequence: PoseSequence,
    event: EventInterval,
    indexes: np.ndarray,
    scale: float,
    feature_name: str,
) -> FeatureResult:
    definition = FEATURE_DEFINITIONS[feature_name]
    summary = summarize_fs01_fs02_event_feature(
        feature_name,
        sequence,
        indexes,
        scale,
        event.key_phases,
    )
    valid_mask = np.asarray(summary.valid_mask, dtype=bool)
    valid_fraction = float(valid_mask[indexes].mean()) if indexes.size else 0.0
    required_joints = list(summary.required_joints) or definition["required_joints"]
    confidence = _feature_confidence(
        sequence,
        indexes,
        required_joints,
        valid_mask,
    ) * float(summary.confidence_multiplier)
    confidence = max(0.0, min(1.0, confidence))
    is_valid = summary.value is not None and valid_fraction >= MIN_VALID_FRACTION
    evidence_indexes = sorted(
        {
            int(index)
            for index in summary.evidence_indexes
            if 0 <= int(index) < sequence.source_frames.size
        }
    )
    value = summary.value
    if value is not None and not isinstance(value, int):
        value = round(float(value), 8)
    return FeatureResult(
        feature_name=feature_name,
        feature_version=definition["version"],
        value=value,
        unit=definition["unit"],
        confidence=confidence,
        valid=is_valid,
        reason=(
            summary.reason
            if is_valid
            else "valid_fraction_below_quality_gate"
            if summary.value is not None
            else summary.reason
        ),
        source_frames=[
            int(sequence.source_frames[index]) for index in evidence_indexes
        ],
        raw_value={
            name: _series_payload(sequence, indexes, values)
            for name, values in summary.raw_series.items()
        },
        smoothed_value={
            "series": {
                name: _series_payload(sequence, indexes, values)
                for name, values in summary.smoothed_series.items()
            },
            "summary": summary.diagnostics,
        },
        provenance={
            "library_version": FEATURE_LIBRARY_VERSION,
            "fs01_fs02_feature_version": FS01_FS02_FEATURE_VERSION,
            "event_id": event.event_id,
            "event_code": event.event_code,
            "person_track_id": event.person_track_id,
            "aggregation": definition["aggregation"],
            "definition": definition["definition"],
            "timestamp_source": "timestamp_ms",
            "body_scale": scale,
            "valid_fraction": round(valid_fraction, 6),
            "required_joints_used": required_joints,
            "topology_confidence_multiplier": float(
                summary.confidence_multiplier
            ),
            "foot_reference_provenance": summary.diagnostics.get(
                "foot_reference_provenance"
            ),
            "measurement_status": (
                "pose_kinematic_proxy_not_contact_force_or_calibrated_grade"
            ),
            "score_threshold_version": None,
            "smoothing": {"max_gap_ms": 160, "radius_ms": 100},
            **(
                {
                    "evidence_contract": (
                        "fs01_m03_raw_and_prepared_kinematic_series_v1"
                    )
                }
                if feature_name
                in {
                    "bilateral_foot_rise_min_body",
                    "bilateral_foot_rise_synchrony_ms",
                    "bilateral_foot_rise_proxy_duration_ms",
                    "hip_center_vertical_velocity_body_s",
                }
                else {}
            ),
            **(
                {
                    "evidence_contract": (
                        "fs01_m04_raw_and_prepared_slowdown_series_v1"
                    )
                }
                if feature_name
                in {
                    "bilateral_foot_vertical_slowdown_time_offset_ms",
                    "post_slowdown_stance_width_body",
                    "hip_center_lateral_variability_body",
                }
                else {}
            ),
            **(
                {
                    "evidence_contract": (
                        "fs02_m04_raw_and_prepared_launch_kinematics_v1"
                    )
                }
                if feature_name
                in {
                    "launch_side_code",
                    "launch_foot_speed_peak_body_s",
                    "launch_foot_relative_displacement_body",
                    "launch_foot_motion_duration_ms",
                }
                else {}
            ),
            **(
                {
                    "evidence_contract": (
                        "fs02_m05_raw_and_prepared_first_step_phase_kinematics_v1"
                    )
                }
                if feature_name
                in {
                    "launch_foot_speed_drop_body_s",
                    "first_step_displacement_body",
                    "post_step_hip_direction_consistency",
                    "launch_foot_slowdown_to_post_hip_direction_ms",
                    "post_step_stance_width_body",
                }
                else {}
            ),
        },
    )


def compute_feature(
    sequence: PoseSequence,
    event: EventInterval,
    feature_name: str,
) -> FeatureResult:
    if feature_name not in FEATURE_DEFINITIONS:
        raise ValueError(f"unknown feature: {feature_name}")
    definition = FEATURE_DEFINITIONS[feature_name]
    indexes = _event_indexes(sequence, event)
    scale = _event_scale(sequence, indexes) if indexes.size else None
    provenance = {
        "library_version": FEATURE_LIBRARY_VERSION,
        "event_id": event.event_id,
        "event_code": event.event_code,
        "person_track_id": event.person_track_id,
        "aggregation": definition["aggregation"],
        "definition": definition["definition"],
        "timestamp_source": "timestamp_ms",
        "body_scale": scale,
        "smoothing": {"max_gap_ms": 160, "radius_ms": 100},
    }
    if indexes.size < 2:
        return FeatureResult(
            feature_name=feature_name,
            feature_version=definition["version"],
            value=None,
            unit=definition["unit"],
            confidence=0.0,
            valid=False,
            reason="event_has_fewer_than_two_source_frames",
            source_frames=[],
            raw_value=None,
            smoothed_value=None,
            provenance=provenance,
        )
    if scale is None or scale <= 1e-6:
        return FeatureResult(
            feature_name=feature_name,
            feature_version=definition["version"],
            value=None,
            unit=definition["unit"],
            confidence=0.0,
            valid=False,
            reason="body_scale_unavailable",
            source_frames=[],
            raw_value=None,
            smoothed_value=None,
            provenance=provenance,
        )
    if feature_name == "stability_duration_ms":
        return _stability_feature(sequence, event, indexes, scale)
    if feature_name in FS01_FS02_EVENT_FEATURE_NAMES:
        return _fs01_fs02_event_summary_feature(
            sequence, event, indexes, scale, feature_name
        )
    if feature_name in FS09_EVENT_FEATURE_NAMES:
        return _fs09_event_summary_feature(
            sequence, event, indexes, scale, feature_name
        )
    raw_base, smoothed_base = _cached_series(sequence, feature_name)
    if feature_name in _SCALE_NORMALIZED_FEATURES:
        raw = raw_base / scale
        smoothed = smoothed_base / scale
    else:
        raw = raw_base
        smoothed = smoothed_base
    valid = np.isfinite(smoothed)
    valid_fraction = float(valid[indexes].mean())
    value = _aggregate(smoothed, indexes, definition["aggregation"])
    confidence = _feature_confidence(
        sequence,
        indexes,
        definition["required_joints"],
        valid,
    )
    is_valid = value is not None and valid_fraction >= MIN_VALID_FRACTION
    return FeatureResult(
        feature_name=feature_name,
        feature_version=definition["version"],
        value=round(value, 8) if value is not None else None,
        unit=definition["unit"],
        confidence=confidence,
        valid=is_valid,
        reason=(
            "valid"
            if is_valid
            else "valid_fraction_below_quality_gate"
            if value is not None
            else "required_keypoints_unavailable"
        ),
        source_frames=_representative_frames(
            sequence, indexes, smoothed, definition["aggregation"]
        ),
        raw_value=_series_payload(sequence, indexes, raw),
        smoothed_value=_series_payload(sequence, indexes, smoothed),
        provenance={**provenance, "valid_fraction": round(valid_fraction, 6)},
    )


def compute_event_features(
    sequence: PoseSequence,
    event: EventInterval,
    feature_names: list[str] | tuple[str, ...] | None = None,
) -> list[FeatureResult]:
    names = feature_names or SCORING_FEATURE_NAMES
    return [compute_feature(sequence, event, name) for name in names]
