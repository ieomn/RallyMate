from __future__ import annotations

from dataclasses import dataclass, field
import math
from typing import Any

import numpy as np

from rallymate_features.coordinates import (
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
from rallymate_features.kinematics import irregular_derivative, speed
from rallymate_features.schemas import PoseSequence
from rallymate_features.smoothing import smooth_series


FS09_FEATURE_VERSION = "fs09-pose-proxies-v0.2.0"

# Several event summary features reuse the same full-video kinematic series.
# Cache them by sequence identity and body scale so an event loop does not
# recompute and smooth nine arrays for every FS09 indicator instance.
_BASE_EVENT_SERIES_CACHE: dict[
    int,
    tuple[
        PoseSequence,
        dict[str, np.ndarray],
        dict[str, np.ndarray],
    ],
] = {}
_BODY_NORMALIZED_EVENT_SERIES = frozenset(
    {
        "hip_speed",
        "hip_deceleration",
        "left_ankle_speed",
        "right_ankle_speed",
        "left_ankle_slowdown",
        "right_ankle_slowdown",
        "hip_slowdown",
        "hip_height",
    }
)

FS09_SERIES_FEATURE_NAMES = frozenset(
    {
        "hip_center_velocity_x_body_s",
        "hip_center_velocity_y_body_s",
        "hip_center_speed_body_s",
        "hip_center_motion_direction_deg",
        "hip_center_relative_to_ankle_midpoint_x_body",
        "hip_center_relative_to_ankle_midpoint_y_body",
        "hip_center_speed_trend_body_s2",
        "hip_center_deceleration_body_s2",
        "left_ankle_speed_body_s",
        "right_ankle_speed_body_s",
        "left_knee_flexion_velocity_deg_s",
        "right_knee_flexion_velocity_deg_s",
    }
)

FS09_EVENT_FEATURE_NAMES = frozenset(
    {
        "hip_center_speed_drop_body_s",
        "left_ankle_speed_drop_body_s",
        "right_ankle_speed_drop_body_s",
        "left_knee_flexion_change_deg",
        "right_knee_flexion_change_deg",
        "braking_side_code",
        "braking_ankle_speed_drop_body_s",
        "braking_ankle_slowdown_to_hip_deceleration_ms",
        "hip_deceleration_to_double_support_proxy_ms",
        "hip_height_delta_body",
        "torso_lean_variability_deg",
        "shoulder_hip_angular_velocity_change_deg_s",
        "double_support_proxy_duration_ms",
    }
)


@dataclass(frozen=True)
class FS09EventSummary:
    """Pure event summary returned before conversion to the public FeatureResult.

    ``evidence_indexes`` are indexes into the source PoseSequence.  Arrays are
    intentionally retained so the caller can serialize the complete evidence
    series without this module depending on a report format.
    """

    value: float | int | None
    valid_mask: np.ndarray
    evidence_indexes: tuple[int, ...] = ()
    raw_series: dict[str, np.ndarray] = field(default_factory=dict)
    smoothed_series: dict[str, np.ndarray] = field(default_factory=dict)
    diagnostics: dict[str, Any] = field(default_factory=dict)
    reason: str = "valid"


def _scale_value(body_scale: float) -> float:
    value = float(body_scale)
    if not math.isfinite(value) or value <= 1e-12:
        raise ValueError("body_scale must be finite and positive")
    return value


def _mask_rows(values: np.ndarray, valid: np.ndarray) -> np.ndarray:
    result = np.asarray(values, dtype=np.float64).copy()
    result[~np.asarray(valid, dtype=bool)] = np.nan
    return result


def hip_center_velocity_body_s(
    sequence: PoseSequence,
    body_scale: float,
) -> np.ndarray:
    """Image-plane hip-center velocity in body scales/second.

    Positive x is image-right and positive y is image-down.  This is a visual
    body-center proxy, not a biomechanical centre-of-mass estimate.
    """

    scale_value = _scale_value(body_scale)
    hips, valid = hip_center(sequence)
    smoothed = smooth_series(sequence.timestamp_ms, hips)
    velocity = irregular_derivative(sequence.timestamp_ms, smoothed) / scale_value
    return _mask_rows(velocity, valid)


def hip_center_speed_body_s(
    sequence: PoseSequence,
    body_scale: float,
) -> np.ndarray:
    velocity = hip_center_velocity_body_s(sequence, body_scale)
    values = np.linalg.norm(velocity, axis=1)
    values[~np.isfinite(velocity).all(axis=1)] = np.nan
    return values


def hip_center_motion_direction_deg(
    sequence: PoseSequence,
    body_scale: float = 1.0,
) -> np.ndarray:
    """Hip proxy motion direction: 0° image-right, 90° image-up."""

    velocity = hip_center_velocity_body_s(sequence, body_scale)
    magnitude = np.linalg.norm(velocity, axis=1)
    values = np.degrees(np.arctan2(-velocity[:, 1], velocity[:, 0]))
    values[~np.isfinite(velocity).all(axis=1) | (magnitude <= 1e-12)] = np.nan
    return values


def hip_center_relative_to_ankle_midpoint_body(
    sequence: PoseSequence,
    body_scale: float,
) -> np.ndarray:
    """Hip proxy minus ankle midpoint in body scales (x right, y down)."""

    scale_value = _scale_value(body_scale)
    hips, hip_valid = hip_center(sequence)
    left, left_valid = point_series(sequence, "left_ankle")
    right, right_valid = point_series(sequence, "right_ankle")
    midpoint = (left + right) / 2.0
    valid = hip_valid & left_valid & right_valid
    return _mask_rows((hips - midpoint) / scale_value, valid)


def hip_center_speed_trend_body_s2(
    sequence: PoseSequence,
    body_scale: float,
) -> np.ndarray:
    values = hip_center_speed_body_s(sequence, body_scale)
    smoothed = smooth_series(sequence.timestamp_ms, values)
    return irregular_derivative(sequence.timestamp_ms, smoothed[:, None])[:, 0]


def ankle_speed_body_s(
    sequence: PoseSequence,
    side: str,
    body_scale: float,
) -> np.ndarray:
    if side not in {"left", "right"}:
        raise ValueError("side must be 'left' or 'right'")
    scale_value = _scale_value(body_scale)
    ankle, valid = point_series(sequence, f"{side}_ankle")
    smoothed = smooth_series(sequence.timestamp_ms, ankle)
    values = speed(sequence.timestamp_ms, smoothed) / scale_value
    values[~valid] = np.nan
    return values


def knee_flexion_deg(sequence: PoseSequence, side: str) -> np.ndarray:
    if side not in {"left", "right"}:
        raise ValueError("side must be 'left' or 'right'")
    hip, hip_valid = point_series(sequence, f"{side}_hip")
    knee, knee_valid = point_series(sequence, f"{side}_knee")
    ankle, ankle_valid = point_series(sequence, f"{side}_ankle")
    values = 180.0 - angle_three_points_deg(hip, knee, ankle)
    values[~(hip_valid & knee_valid & ankle_valid)] = np.nan
    return values


def knee_flexion_velocity_deg_s(sequence: PoseSequence, side: str) -> np.ndarray:
    flexion = knee_flexion_deg(sequence, side)
    smoothed = smooth_series(sequence.timestamp_ms, flexion)
    return irregular_derivative(sequence.timestamp_ms, smoothed[:, None])[:, 0]


def hip_height_above_ankle_midpoint_body(
    sequence: PoseSequence,
    body_scale: float,
) -> np.ndarray:
    relative = hip_center_relative_to_ankle_midpoint_body(sequence, body_scale)
    # Image y points downward, hence support_y - hip_y is positive height.
    return -relative[:, 1]


def torso_lean_deg(sequence: PoseSequence) -> np.ndarray:
    shoulders, shoulder_valid = shoulder_center(sequence)
    hips, hip_valid = hip_center(sequence)
    vector = shoulders - hips
    values = np.degrees(np.arctan2(vector[:, 0], -vector[:, 1]))
    values[~(shoulder_valid & hip_valid)] = np.nan
    return values


def shoulder_hip_angular_velocity_deg_s(sequence: PoseSequence) -> np.ndarray:
    left_shoulder, ls_valid = point_series(sequence, "left_shoulder")
    right_shoulder, rs_valid = point_series(sequence, "right_shoulder")
    left_hip, lh_valid = point_series(sequence, "left_hip")
    right_hip, rh_valid = point_series(sequence, "right_hip")
    shoulder_angle = orientation_deg(right_shoulder - left_shoulder)
    hip_angle = orientation_deg(right_hip - left_hip)
    separation = wrap_axis_angle_deg(shoulder_angle - hip_angle)
    separation[~(ls_valid & rs_valid & lh_valid & rh_valid)] = np.nan
    unwrapped = unwrap_axis_angle_deg(separation)
    smoothed = smooth_series(sequence.timestamp_ms, unwrapped)
    return irregular_derivative(sequence.timestamp_ms, smoothed[:, None])[:, 0]


def raw_fs09_series(
    feature_name: str,
    sequence: PoseSequence,
    body_scale: float = 1.0,
) -> np.ndarray:
    """Compute a versioned FS09 frame series without event aggregation."""

    if feature_name == "hip_center_velocity_x_body_s":
        return hip_center_velocity_body_s(sequence, body_scale)[:, 0]
    if feature_name == "hip_center_velocity_y_body_s":
        return hip_center_velocity_body_s(sequence, body_scale)[:, 1]
    if feature_name == "hip_center_speed_body_s":
        return hip_center_speed_body_s(sequence, body_scale)
    if feature_name == "hip_center_motion_direction_deg":
        return hip_center_motion_direction_deg(sequence, body_scale)
    if feature_name == "hip_center_relative_to_ankle_midpoint_x_body":
        return hip_center_relative_to_ankle_midpoint_body(sequence, body_scale)[:, 0]
    if feature_name == "hip_center_relative_to_ankle_midpoint_y_body":
        return hip_center_relative_to_ankle_midpoint_body(sequence, body_scale)[:, 1]
    if feature_name == "hip_center_speed_trend_body_s2":
        return hip_center_speed_trend_body_s2(sequence, body_scale)
    if feature_name == "hip_center_deceleration_body_s2":
        return -hip_center_speed_trend_body_s2(sequence, body_scale)
    if feature_name == "left_ankle_speed_body_s":
        return ankle_speed_body_s(sequence, "left", body_scale)
    if feature_name == "right_ankle_speed_body_s":
        return ankle_speed_body_s(sequence, "right", body_scale)
    if feature_name == "left_knee_flexion_velocity_deg_s":
        return knee_flexion_velocity_deg_s(sequence, "left")
    if feature_name == "right_knee_flexion_velocity_deg_s":
        return knee_flexion_velocity_deg_s(sequence, "right")
    raise ValueError(f"unknown FS09 series feature: {feature_name}")


def _finite_event_indexes(values: np.ndarray, indexes: np.ndarray) -> np.ndarray:
    indexes = np.asarray(indexes, dtype=np.int64)
    return indexes[np.isfinite(np.asarray(values, dtype=np.float64)[indexes])]


def event_edge_change(
    values: np.ndarray,
    indexes: np.ndarray,
    *,
    early_minus_late: bool = False,
    edge_fraction: float = 0.25,
) -> tuple[float | None, tuple[int, ...], dict[str, float | int]]:
    """Compare robust medians at the event edges without a scoring threshold."""

    finite = _finite_event_indexes(values, indexes)
    if finite.size < 4:
        return None, (), {"valid_samples": int(finite.size)}
    count = max(2, int(math.ceil(finite.size * edge_fraction)))
    count = min(count, finite.size // 2)
    early_indexes = finite[:count]
    late_indexes = finite[-count:]
    early = float(np.median(values[early_indexes]))
    late = float(np.median(values[late_indexes]))
    value = early - late if early_minus_late else late - early
    evidence = (
        int(early_indexes[np.argmin(np.abs(values[early_indexes] - early))]),
        int(late_indexes[np.argmin(np.abs(values[late_indexes] - late))]),
    )
    return value, evidence, {
        "early_median": early,
        "late_median": late,
        "edge_fraction": float(edge_fraction),
        "edge_sample_count": int(count),
        "valid_samples": int(finite.size),
    }


def event_variability(
    values: np.ndarray,
    indexes: np.ndarray,
) -> tuple[float | None, tuple[int, ...], dict[str, float | int]]:
    finite = _finite_event_indexes(values, indexes)
    if finite.size < 2:
        return None, (), {"valid_samples": int(finite.size)}
    eligible = values[finite]
    value = float(np.std(eligible, ddof=0))
    median = float(np.median(eligible))
    evidence = (int(finite[np.argmax(np.abs(eligible - median))]),)
    return value, evidence, {
        "median": median,
        "valid_samples": int(finite.size),
        "variability_statistic": "population_standard_deviation",
    }


def braking_side_from_speed_drops(
    left_drop: float | None,
    right_drop: float | None,
    *,
    left_peak_slowdown: float | None = None,
    right_peak_slowdown: float | None = None,
) -> tuple[int | None, str]:
    """Encode the strongest observable ankle-slowdown proxy.

    Codes are -1=left, 0=indeterminate/bilateral, +1=right.  The result is a
    kinematic diagnostic only and must not be described as measured contact or
    load-bearing side.  Positive event-edge speed drop remains the primary
    signal.  When neither ankle has a positive net drop, a positive local
    slowdown peak may still identify the candidate side.  This preserves a
    measured negative/zero net response as evidence of poor or absent braking
    instead of misclassifying it as missing data.
    """

    if left_drop is None or right_drop is None:
        return None, "required_ankle_speed_drop_unavailable"
    if left_drop <= 0.0 and right_drop <= 0.0:
        left_peak = (
            float(left_peak_slowdown)
            if left_peak_slowdown is not None and left_peak_slowdown > 0.0
            else None
        )
        right_peak = (
            float(right_peak_slowdown)
            if right_peak_slowdown is not None and right_peak_slowdown > 0.0
            else None
        )
        if left_peak is None and right_peak is None:
            return 0, "no_positive_net_drop_or_local_slowdown"
        if right_peak is None or (
            left_peak is not None and left_peak > right_peak
        ):
            return -1, "left_peak_slowdown_fallback_without_positive_net_drop"
        if left_peak is None or right_peak > left_peak:
            return 1, "right_peak_slowdown_fallback_without_positive_net_drop"
        # Equal local peaks are resolved by the less-negative net response so
        # the downstream numeric vector remains deterministic.  A complete tie
        # stays explicitly bilateral/indeterminate.
        if left_drop > right_drop:
            return -1, "left_less_negative_net_drop_breaks_equal_peak_tie"
        if right_drop > left_drop:
            return 1, "right_less_negative_net_drop_breaks_equal_peak_tie"
        return 0, "equal_local_slowdown_and_net_drop"
    if left_drop > right_drop:
        return -1, "left_ankle_has_larger_speed_drop"
    if right_drop > left_drop:
        return 1, "right_ankle_has_larger_speed_drop"
    return 0, "equal_positive_ankle_speed_drop"


def peak_slowdown_index(
    timestamp_ms: np.ndarray,
    speed_values: np.ndarray,
    indexes: np.ndarray,
) -> tuple[int | None, float | None]:
    smoothed = smooth_series(timestamp_ms, speed_values)
    slowdown = -irregular_derivative(timestamp_ms, smoothed[:, None])[:, 0]
    return _positive_peak_index(slowdown, indexes)


def _positive_peak_index(
    values: np.ndarray,
    indexes: np.ndarray,
) -> tuple[int | None, float | None]:
    """Return a strictly positive event-local peak from a precomputed series."""

    finite = _finite_event_indexes(values, indexes)
    if finite.size == 0:
        return None, None
    selected = int(finite[np.argmax(values[finite])])
    peak = float(values[selected])
    if peak <= 0.0:
        return None, peak
    return selected, peak


def _longest_true_run(
    timestamp_ms: np.ndarray,
    indexes: np.ndarray,
    mask: np.ndarray,
) -> tuple[int, tuple[int, ...]]:
    if indexes.size == 0:
        return 0, ()
    local = np.asarray(mask, dtype=bool)[indexes]
    longest: tuple[int, int] | None = None
    start: int | None = None
    for position, active in enumerate(np.append(local, False)):
        if active and start is None:
            start = position
        elif not active and start is not None:
            end = position - 1
            if longest is None or end - start > longest[1] - longest[0]:
                longest = (start, end)
            start = None
    if longest is None:
        return 0, ()
    first = int(indexes[longest[0]])
    last = int(indexes[longest[1]])
    local_timestamps = np.asarray(timestamp_ms, dtype=np.int64)[indexes]
    median_dt = int(np.median(np.diff(local_timestamps))) if indexes.size > 1 else 0
    duration = int(timestamp_ms[last] - timestamp_ms[first] + median_dt)
    return max(duration, 0), (first, last)


def double_support_low_motion_proxy(
    timestamp_ms: np.ndarray,
    left_ankle_speed: np.ndarray,
    right_ankle_speed: np.ndarray,
    indexes: np.ndarray,
) -> tuple[int | None, tuple[int, ...], np.ndarray, dict[str, Any]]:
    """Longest simultaneous event-low ankle-motion run.

    The event medians are signal envelopes, not A-E thresholds.  This proxy
    cannot establish actual foot-ground contact, support force, or weight load.
    """

    indexes = np.asarray(indexes, dtype=np.int64)
    joint_valid = (
        np.isfinite(left_ankle_speed[indexes])
        & np.isfinite(right_ankle_speed[indexes])
    )
    valid_indexes = indexes[joint_valid]
    mask = np.zeros(np.asarray(timestamp_ms).shape, dtype=bool)
    if valid_indexes.size < 3:
        return None, (), mask, {"valid_samples": int(valid_indexes.size)}
    left_envelope = float(np.median(left_ankle_speed[valid_indexes]))
    right_envelope = float(np.median(right_ankle_speed[valid_indexes]))
    mask[valid_indexes] = (
        (left_ankle_speed[valid_indexes] <= left_envelope)
        & (right_ankle_speed[valid_indexes] <= right_envelope)
    )
    duration, evidence = _longest_true_run(timestamp_ms, indexes, mask)
    return duration, evidence, mask, {
        "valid_samples": int(valid_indexes.size),
        "left_event_median_envelope_body_s": left_envelope,
        "right_event_median_envelope_body_s": right_envelope,
        "envelope_status": "event_adaptive_signal_proxy_not_scoring_threshold",
        "contact_status": "not_observed_from_pose",
    }


def _validated_phase_timing_series(
    timestamp_ms: np.ndarray,
    indexes: np.ndarray,
    **series: np.ndarray,
) -> tuple[np.ndarray, np.ndarray, dict[str, np.ndarray], dict[str, Any]] | None:
    """Validate aligned timestamp-based inputs for FS09 phase timing proxies.

    This is an input-contract check, not a motion or scoring threshold.  Timing
    functions fail closed when the serialized evidence cannot identify one
    strictly ordered sample grid and one event-local index set.
    """

    timestamps = np.asarray(timestamp_ms)
    if timestamps.ndim != 1 or timestamps.size < 2:
        return None
    try:
        timestamps_float = timestamps.astype(np.float64)
    except (TypeError, ValueError):
        return None
    if (
        not np.isfinite(timestamps_float).all()
        or np.any(np.diff(timestamps_float) <= 0.0)
    ):
        return None

    raw_indexes = np.asarray(indexes)
    if (
        raw_indexes.ndim != 1
        or raw_indexes.size == 0
        or not np.issubdtype(raw_indexes.dtype, np.integer)
    ):
        return None
    event_indexes = raw_indexes.astype(np.int64, copy=False)
    if (
        np.any(event_indexes < 0)
        or np.any(event_indexes >= timestamps.size)
        or (event_indexes.size > 1 and np.any(np.diff(event_indexes) <= 0))
    ):
        return None

    aligned: dict[str, np.ndarray] = {}
    for name, values in series.items():
        try:
            array = np.asarray(values, dtype=np.float64)
        except (TypeError, ValueError):
            return None
        if array.ndim != 1 or array.shape != timestamps.shape:
            return None
        aligned[name] = array
    return timestamps_float, event_indexes, aligned, {
        "input_status": "valid_aligned_timestamp_series",
        "timestamp_source": "timestamp_ms",
        "event_sample_count": int(event_indexes.size),
    }


def braking_ankle_slowdown_to_hip_deceleration_from_series(
    timestamp_ms: np.ndarray,
    left_ankle_speed: np.ndarray,
    right_ankle_speed: np.ndarray,
    left_ankle_slowdown: np.ndarray,
    right_ankle_slowdown: np.ndarray,
    hip_slowdown: np.ndarray,
    indexes: np.ndarray,
) -> tuple[int | None, tuple[int, ...], dict[str, Any]]:
    """Timestamp delta between selected ankle and hip slowdown peaks.

    The caller supplies the exact speed and slowdown series to aggregate.  The
    production path passes its smoothed/cached series; error analysis passes
    unsmoothed speed plus derivatives computed from the serialized raw series.
    No fixed FPS, ground contact, force, or A--E threshold is inferred here.
    """

    validated = _validated_phase_timing_series(
        timestamp_ms,
        indexes,
        left_ankle_speed=left_ankle_speed,
        right_ankle_speed=right_ankle_speed,
        left_ankle_slowdown=left_ankle_slowdown,
        right_ankle_slowdown=right_ankle_slowdown,
        hip_slowdown=hip_slowdown,
    )
    if validated is None:
        return None, (), {"input_status": "invalid_or_misaligned_timing_series"}
    timestamps, event_indexes, values, input_diagnostics = validated
    left_drop, _, left_diag = event_edge_change(
        values["left_ankle_speed"], event_indexes, early_minus_late=True
    )
    right_drop, _, right_diag = event_edge_change(
        values["right_ankle_speed"], event_indexes, early_minus_late=True
    )
    left_index, left_peak = _positive_peak_index(
        values["left_ankle_slowdown"], event_indexes
    )
    right_index, right_peak = _positive_peak_index(
        values["right_ankle_slowdown"], event_indexes
    )
    side_code, side_reason = braking_side_from_speed_drops(
        left_drop,
        right_drop,
        left_peak_slowdown=left_peak,
        right_peak_slowdown=right_peak,
    )
    if side_code == -1:
        selected_side, ankle_index, ankle_peak = "left", left_index, left_peak
    elif side_code == 1:
        selected_side, ankle_index, ankle_peak = "right", right_index, right_peak
    else:
        selected_side = None
        ankle_index = ankle_peak = None
    hip_index, hip_peak = _positive_peak_index(
        values["hip_slowdown"], event_indexes
    )
    value = (
        int(timestamps[hip_index] - timestamps[ankle_index])
        if ankle_index is not None and hip_index is not None
        else None
    )
    evidence = tuple(
        int(index) for index in (ankle_index, hip_index) if index is not None
    )
    return value, evidence, {
        **input_diagnostics,
        "code_map": {"-1": "left", "0": "indeterminate_or_bilateral", "1": "right"},
        "selected_side": selected_side,
        "selection_reason": side_reason,
        "left_speed_drop_body_s": left_drop,
        "right_speed_drop_body_s": right_drop,
        "left_edge_summary": left_diag,
        "right_edge_summary": right_diag,
        "left_peak_slowdown_body_s2": left_peak,
        "right_peak_slowdown_body_s2": right_peak,
        "left_peak_slowdown_timestamp_ms": (
            int(timestamps[left_index]) if left_index is not None else None
        ),
        "right_peak_slowdown_timestamp_ms": (
            int(timestamps[right_index]) if right_index is not None else None
        ),
        "ankle_peak_slowdown_body_s2": ankle_peak,
        "hip_peak_deceleration_body_s2": hip_peak,
        "hip_peak_deceleration_timestamp_ms": (
            int(timestamps[hip_index]) if hip_index is not None else None
        ),
        "proxy_status": "ankle_kinematics_only_not_ground_contact_or_force",
        "sign_convention": "hip_deceleration_time_minus_braking_ankle_slowdown_time",
    }


def hip_deceleration_to_double_support_proxy_from_series(
    timestamp_ms: np.ndarray,
    left_ankle_speed: np.ndarray,
    right_ankle_speed: np.ndarray,
    hip_slowdown: np.ndarray,
    indexes: np.ndarray,
) -> tuple[int | None, tuple[int, ...], np.ndarray, dict[str, Any]]:
    """Timestamp delta from hip slowdown peak to low-ankle-motion start.

    ``double_support`` remains a simultaneous low-motion Pose proxy.  This
    function does not observe ground contact or load transfer.
    """

    validated = _validated_phase_timing_series(
        timestamp_ms,
        indexes,
        left_ankle_speed=left_ankle_speed,
        right_ankle_speed=right_ankle_speed,
        hip_slowdown=hip_slowdown,
    )
    mask = np.zeros(np.asarray(timestamp_ms).shape, dtype=bool)
    if validated is None:
        return (
            None,
            (),
            mask,
            {"input_status": "invalid_or_misaligned_timing_series"},
        )
    timestamps, event_indexes, values, input_diagnostics = validated
    _, support_evidence, mask, support_diagnostics = double_support_low_motion_proxy(
        timestamps,
        values["left_ankle_speed"],
        values["right_ankle_speed"],
        event_indexes,
    )
    support_start = support_evidence[0] if support_evidence else None
    hip_index, hip_peak = _positive_peak_index(
        values["hip_slowdown"], event_indexes
    )
    value = (
        int(timestamps[support_start] - timestamps[hip_index])
        if support_start is not None and hip_index is not None
        else None
    )
    evidence = tuple(
        int(index) for index in (hip_index, support_start) if index is not None
    )
    return value, evidence, mask, {
        **input_diagnostics,
        **support_diagnostics,
        "support_proxy_start_timestamp_ms": (
            int(timestamps[support_start]) if support_start is not None else None
        ),
        "hip_peak_deceleration_body_s2": hip_peak,
        "hip_peak_deceleration_timestamp_ms": (
            int(timestamps[hip_index]) if hip_index is not None else None
        ),
        "sign_convention": "low_ankle_motion_proxy_start_minus_hip_deceleration_time",
    }


def _base_event_series(
    sequence: PoseSequence,
    body_scale: float,
) -> tuple[dict[str, np.ndarray], dict[str, np.ndarray]]:
    scale = _scale_value(body_scale)
    cache_key = id(sequence)
    cached = _BASE_EVENT_SERIES_CACHE.get(cache_key)
    if cached is None or cached[0] is not sequence:
        raw_base = {
        "hip_speed": hip_center_speed_body_s(sequence, 1.0),
        "hip_deceleration": -hip_center_speed_trend_body_s2(sequence, 1.0),
        "left_ankle_speed": ankle_speed_body_s(sequence, "left", 1.0),
        "right_ankle_speed": ankle_speed_body_s(sequence, "right", 1.0),
        "left_knee_flexion": knee_flexion_deg(sequence, "left"),
        "right_knee_flexion": knee_flexion_deg(sequence, "right"),
        "hip_height": hip_height_above_ankle_midpoint_body(sequence, 1.0),
        "torso_lean": torso_lean_deg(sequence),
        "shoulder_hip_angular_velocity": shoulder_hip_angular_velocity_deg_s(sequence),
        }
        smoothed_base = {
            name: smooth_series(sequence.timestamp_ms, values)
            for name, values in raw_base.items()
        }
        # These derivatives are full-video cached signals.  Recomputing them
        # for every FS09 event/feature is mathematically redundant and made a
        # long clip scale with event count instead of frame count.
        for source_name, slowdown_name in (
            ("hip_speed", "hip_slowdown"),
            ("left_ankle_speed", "left_ankle_slowdown"),
            ("right_ankle_speed", "right_ankle_slowdown"),
        ):
            twice_smoothed = smooth_series(
                sequence.timestamp_ms,
                smoothed_base[source_name],
            )
            smoothed_base[slowdown_name] = -irregular_derivative(
                sequence.timestamp_ms,
                twice_smoothed[:, None],
            )[:, 0]
        cached = (sequence, raw_base, smoothed_base)
        _BASE_EVENT_SERIES_CACHE[cache_key] = cached
    raw = {
        name: values / scale if name in _BODY_NORMALIZED_EVENT_SERIES else values
        for name, values in cached[1].items()
    }
    smoothed = {
        name: values / scale if name in _BODY_NORMALIZED_EVENT_SERIES else values
        for name, values in cached[2].items()
    }
    return raw, smoothed


def clear_fs09_feature_cache(sequence: PoseSequence | None = None) -> None:
    """Release cached full-video FS09 series after a scoring run."""

    if sequence is None:
        _BASE_EVENT_SERIES_CACHE.clear()
        return
    for key, cached in list(_BASE_EVENT_SERIES_CACHE.items()):
        if cached[0] is sequence:
            _BASE_EVENT_SERIES_CACHE.pop(key, None)


def summarize_fs09_event_feature(
    feature_name: str,
    sequence: PoseSequence,
    indexes: np.ndarray,
    body_scale: float,
) -> FS09EventSummary:
    """Compute an event-level FS09 quantity using only PoseSequence evidence."""

    if feature_name not in FS09_EVENT_FEATURE_NAMES:
        raise ValueError(f"unknown FS09 event feature: {feature_name}")
    indexes = np.asarray(indexes, dtype=np.int64)
    raw, smoothed = _base_event_series(sequence, body_scale)

    edge_specs = {
        "hip_center_speed_drop_body_s": ("hip_speed", True),
        "left_ankle_speed_drop_body_s": ("left_ankle_speed", True),
        "right_ankle_speed_drop_body_s": ("right_ankle_speed", True),
        "left_knee_flexion_change_deg": ("left_knee_flexion", False),
        "right_knee_flexion_change_deg": ("right_knee_flexion", False),
        "hip_height_delta_body": ("hip_height", False),
    }
    if feature_name in edge_specs:
        series_name, early_minus_late = edge_specs[feature_name]
        value, evidence, diagnostics = event_edge_change(
            smoothed[series_name],
            indexes,
            early_minus_late=early_minus_late,
        )
        valid_mask = np.isfinite(smoothed[series_name])
        return FS09EventSummary(
            value=value,
            valid_mask=valid_mask,
            evidence_indexes=evidence,
            raw_series={series_name: raw[series_name]},
            smoothed_series={series_name: smoothed[series_name]},
            diagnostics=diagnostics,
            reason="valid" if value is not None else "insufficient_event_edge_samples",
        )

    if feature_name == "torso_lean_variability_deg":
        value, evidence, diagnostics = event_variability(
            smoothed["torso_lean"], indexes
        )
        return FS09EventSummary(
            value=value,
            valid_mask=np.isfinite(smoothed["torso_lean"]),
            evidence_indexes=evidence,
            raw_series={"torso_lean": raw["torso_lean"]},
            smoothed_series={"torso_lean": smoothed["torso_lean"]},
            diagnostics=diagnostics,
            reason="valid" if value is not None else "insufficient_variability_samples",
        )

    if feature_name == "shoulder_hip_angular_velocity_change_deg_s":
        absolute = np.abs(smoothed["shoulder_hip_angular_velocity"])
        value, evidence, diagnostics = event_edge_change(absolute, indexes)
        diagnostics["sign_convention"] = (
            "late_absolute_angular_velocity_minus_early_absolute_angular_velocity"
        )
        return FS09EventSummary(
            value=value,
            valid_mask=np.isfinite(absolute),
            evidence_indexes=evidence,
            raw_series={
                "shoulder_hip_angular_velocity": raw[
                    "shoulder_hip_angular_velocity"
                ]
            },
            smoothed_series={"absolute_angular_velocity": absolute},
            diagnostics=diagnostics,
            reason="valid" if value is not None else "insufficient_event_edge_samples",
        )

    left_drop, left_evidence, left_diag = event_edge_change(
        smoothed["left_ankle_speed"], indexes, early_minus_late=True
    )
    right_drop, right_evidence, right_diag = event_edge_change(
        smoothed["right_ankle_speed"], indexes, early_minus_late=True
    )
    left_slowdown_index, left_slowdown_peak = _positive_peak_index(
        smoothed["left_ankle_slowdown"], indexes
    )
    right_slowdown_index, right_slowdown_peak = _positive_peak_index(
        smoothed["right_ankle_slowdown"], indexes
    )
    side_code, side_reason = braking_side_from_speed_drops(
        left_drop,
        right_drop,
        left_peak_slowdown=left_slowdown_peak,
        right_peak_slowdown=right_slowdown_peak,
    )
    selected_side = "left" if side_code == -1 else "right" if side_code == 1 else None
    both_ankles_valid = (
        np.isfinite(smoothed["left_ankle_speed"])
        & np.isfinite(smoothed["right_ankle_speed"])
    )
    side_diagnostics: dict[str, Any] = {
        "code_map": {"-1": "left", "0": "indeterminate_or_bilateral", "1": "right"},
        "selected_side": selected_side,
        "selection_reason": side_reason,
        "left_speed_drop_body_s": left_drop,
        "right_speed_drop_body_s": right_drop,
        "left_edge_summary": left_diag,
        "right_edge_summary": right_diag,
        "left_peak_slowdown_body_s2": left_slowdown_peak,
        "right_peak_slowdown_body_s2": right_slowdown_peak,
        "left_peak_slowdown_timestamp_ms": (
            int(sequence.timestamp_ms[left_slowdown_index])
            if left_slowdown_index is not None
            else None
        ),
        "right_peak_slowdown_timestamp_ms": (
            int(sequence.timestamp_ms[right_slowdown_index])
            if right_slowdown_index is not None
            else None
        ),
        "proxy_status": "ankle_kinematics_only_not_ground_contact_or_force",
    }
    if feature_name == "braking_side_code":
        return FS09EventSummary(
            value=side_code,
            valid_mask=both_ankles_valid,
            evidence_indexes=tuple(sorted(set(left_evidence + right_evidence))),
            raw_series={
                "left_ankle_speed": raw["left_ankle_speed"],
                "right_ankle_speed": raw["right_ankle_speed"],
            },
            smoothed_series={
                "left_ankle_speed": smoothed["left_ankle_speed"],
                "right_ankle_speed": smoothed["right_ankle_speed"],
            },
            diagnostics=side_diagnostics,
            reason=(
                "valid_kinematic_proxy_not_ground_contact"
                if side_code is not None
                else side_reason
            ),
        )

    if feature_name == "braking_ankle_speed_drop_body_s":
        selected_drop = left_drop if selected_side == "left" else right_drop if selected_side == "right" else None
        selected_evidence = left_evidence if selected_side == "left" else right_evidence if selected_side == "right" else ()
        return FS09EventSummary(
            value=selected_drop,
            valid_mask=both_ankles_valid,
            evidence_indexes=selected_evidence,
            raw_series={
                "left_ankle_speed": raw["left_ankle_speed"],
                "right_ankle_speed": raw["right_ankle_speed"],
            },
            smoothed_series={
                "left_ankle_speed": smoothed["left_ankle_speed"],
                "right_ankle_speed": smoothed["right_ankle_speed"],
            },
            diagnostics=side_diagnostics,
            reason=(
                "valid_kinematic_proxy_not_ground_contact"
                if selected_drop is not None
                else "braking_side_indeterminate"
            ),
        )

    duration, support_evidence, support_mask, support_diag = (
        double_support_low_motion_proxy(
            sequence.timestamp_ms,
            smoothed["left_ankle_speed"],
            smoothed["right_ankle_speed"],
            indexes,
        )
    )
    if feature_name == "double_support_proxy_duration_ms":
        support_payload = support_mask.astype(np.float64)
        support_payload[~both_ankles_valid] = np.nan
        return FS09EventSummary(
            value=duration,
            valid_mask=both_ankles_valid,
            evidence_indexes=support_evidence,
            raw_series={
                "left_ankle_speed": raw["left_ankle_speed"],
                "right_ankle_speed": raw["right_ankle_speed"],
            },
            smoothed_series={
                "left_ankle_speed": smoothed["left_ankle_speed"],
                "right_ankle_speed": smoothed["right_ankle_speed"],
                "simultaneous_low_motion_mask": support_payload,
            },
            diagnostics=support_diag,
            reason=(
                "valid_low_ankle_motion_proxy_not_ground_contact"
                if duration is not None
                else "insufficient_joint_ankle_speed_samples"
            ),
        )

    if feature_name == "braking_ankle_slowdown_to_hip_deceleration_ms":
        value, evidence, diagnostics = (
            braking_ankle_slowdown_to_hip_deceleration_from_series(
                sequence.timestamp_ms,
                smoothed["left_ankle_speed"],
                smoothed["right_ankle_speed"],
                smoothed["left_ankle_slowdown"],
                smoothed["right_ankle_slowdown"],
                smoothed["hip_slowdown"],
                indexes,
            )
        )
        return FS09EventSummary(
            value=value,
            valid_mask=(
                both_ankles_valid
                & np.isfinite(smoothed["left_ankle_slowdown"])
                & np.isfinite(smoothed["right_ankle_slowdown"])
                & np.isfinite(smoothed["hip_slowdown"])
            ),
            evidence_indexes=evidence,
            raw_series={
                "left_ankle_speed": raw["left_ankle_speed"],
                "right_ankle_speed": raw["right_ankle_speed"],
                "hip_speed": raw["hip_speed"],
            },
            smoothed_series={
                "left_ankle_speed": smoothed["left_ankle_speed"],
                "right_ankle_speed": smoothed["right_ankle_speed"],
                "left_ankle_slowdown": smoothed["left_ankle_slowdown"],
                "right_ankle_slowdown": smoothed["right_ankle_slowdown"],
                "hip_slowdown": smoothed["hip_slowdown"],
            },
            diagnostics=diagnostics,
            reason=(
                "valid_pose_phase_timing_proxy"
                if value is not None
                else "required_slowdown_peak_unavailable"
            ),
        )

    if feature_name == "hip_deceleration_to_double_support_proxy_ms":
        value, evidence, support_mask, diagnostics = (
            hip_deceleration_to_double_support_proxy_from_series(
                sequence.timestamp_ms,
                smoothed["left_ankle_speed"],
                smoothed["right_ankle_speed"],
                smoothed["hip_slowdown"],
                indexes,
            )
        )
        support_payload = support_mask.astype(np.float64)
        support_payload[~both_ankles_valid] = np.nan
        return FS09EventSummary(
            value=value,
            valid_mask=(
                both_ankles_valid & np.isfinite(smoothed["hip_slowdown"])
            ),
            evidence_indexes=evidence,
            raw_series={
                "hip_speed": raw["hip_speed"],
                "left_ankle_speed": raw["left_ankle_speed"],
                "right_ankle_speed": raw["right_ankle_speed"],
            },
            smoothed_series={
                "hip_slowdown": smoothed["hip_slowdown"],
                "left_ankle_speed": smoothed["left_ankle_speed"],
                "right_ankle_speed": smoothed["right_ankle_speed"],
                "simultaneous_low_motion_mask": support_payload,
            },
            diagnostics=diagnostics,
            reason=(
                "valid_pose_phase_timing_proxy"
                if value is not None
                else "required_phase_proxy_unavailable"
            ),
        )

    raise ValueError(f"unhandled FS09 event feature: {feature_name}")
