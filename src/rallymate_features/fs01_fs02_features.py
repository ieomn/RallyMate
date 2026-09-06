from __future__ import annotations

from dataclasses import dataclass, field
import math
from typing import Any

import numpy as np

from rallymate_features.coordinates import hip_center, point_series
from rallymate_features.geometry import angle_three_points_deg
from rallymate_features.kinematics import irregular_derivative
from rallymate_features.schemas import PoseSequence
from rallymate_features.smoothing import smooth_series


FS01_FS02_FEATURE_VERSION = "fs01-fs02-pose-proxies-v0.5.0"
ANKLE_FALLBACK_CONFIDENCE_MULTIPLIER = 0.65
FS01_FS02_MAX_TEMPORAL_GAP_MS = 160
_NORMALIZED_COORDINATE_NUMERICAL_ZERO = 1e-12
_EVENT_SERIES_CACHE: dict[
    int,
    tuple[
        PoseSequence,
        dict[str, np.ndarray],
        dict[str, np.ndarray],
        dict[str, Any],
    ],
] = {}

FS01_FS02_EVENT_FEATURE_NAMES = frozenset(
    {
        "bilateral_foot_rise_min_body",
        "bilateral_foot_rise_synchrony_ms",
        "bilateral_foot_rise_proxy_duration_ms",
        "hip_center_vertical_velocity_body_s",
        "bilateral_foot_vertical_slowdown_time_offset_ms",
        "post_slowdown_stance_width_body",
        "hip_center_lateral_variability_body",
        "drive_side_code",
        "support_knee_extension_velocity_deg_s",
        "hip_acceleration_along_launch_direction_body_s2",
        "support_drive_to_moving_foot_rise_proxy_ms",
        "launch_direction_deg",
        "launch_side_code",
        "launch_foot_speed_peak_body_s",
        "launch_foot_relative_displacement_body",
        "launch_foot_motion_duration_ms",
        "launch_foot_speed_drop_body_s",
        "first_step_displacement_body",
        "post_step_hip_direction_consistency",
        "launch_foot_slowdown_to_post_hip_direction_ms",
        "post_step_stance_width_body",
    }
)


def _definition(
    unit: str,
    aggregation: str,
    required_joints: list[str],
    definition: str,
) -> dict[str, Any]:
    return {
        "version": FS01_FS02_FEATURE_VERSION,
        "unit": unit,
        "aggregation": aggregation,
        "required_joints": required_joints,
        "definition": definition,
        "measurement_status": (
            "pose_kinematic_proxy_not_contact_force_or_calibrated_grade"
        ),
    }


FS01_FS02_FEATURE_DEFINITIONS: dict[str, dict[str, Any]] = {
    "bilateral_foot_rise_min_body": _definition(
        "body",
        "minimum_of_left_and_right_event_local_upward_excursion",
        ["left_ankle", "right_ankle"],
        "minimum bilateral foot-reference upward excursion from an early-event local kinematic envelope, body-normalized; not measured ground clearance or contact",
    ),
    "bilateral_foot_rise_synchrony_ms": _definition(
        "ms",
        "absolute_peak_upward_velocity_time_difference",
        ["left_ankle", "right_ankle"],
        "absolute time difference between left and right foot-reference peak upward velocities using timestamp_ms; not take-off timing from ground contact",
    ),
    "bilateral_foot_rise_proxy_duration_ms": _definition(
        "ms",
        "longest_simultaneous_half_peak_upward_excursion_run",
        ["left_ankle", "right_ankle"],
        "longest simultaneous run above each foot's event-local half-peak upward-excursion envelope; not airborne duration or ground contact",
    ),
    "hip_center_vertical_velocity_body_s": _definition(
        "body/s",
        "peak_positive_image_up_velocity",
        ["left_hip", "right_hip"],
        "peak image-up hip-center proxy velocity, body-normalized and differentiated with timestamp_ms; not biomechanical centre-of-mass velocity",
    ),
    "bilateral_foot_vertical_slowdown_time_offset_ms": _definition(
        "ms",
        "absolute_event_local_vertical_slowdown_time_difference",
        ["left_ankle", "right_ankle"],
        "absolute time difference between bilateral foot-reference event-local vertical slowdown proxies after downward motion; not landing or contact timing",
    ),
    "post_slowdown_stance_width_body": _definition(
        "body",
        "post_bilateral_vertical_slowdown_median",
        ["left_ankle", "right_ankle"],
        "median foot-reference separation after both event-local vertical slowdown proxies, body-normalized; not support force or confirmed landing stance",
    ),
    "hip_center_lateral_variability_body": _definition(
        "body",
        "post_bilateral_vertical_slowdown_population_standard_deviation_with_boundary_censored_nearest_pre_sample",
        ["left_hip", "right_hip", "left_ankle", "right_ankle"],
        "image-horizontal hip-center proxy variability aligned to the later bilateral foot slowdown, body-normalized; when an event boundary leaves exactly one valid post-slowdown sample, the nearest preceding valid in-event sample may close a two-sample boundary-censored window only when its timestamp is within 160 ms; this is not a fully observed post-landing stability interval",
    ),
    "drive_side_code": _definition(
        "code",
        "larger_event_local_knee_extension_velocity",
        [
            "left_hip",
            "right_hip",
            "left_knee",
            "right_knee",
            "left_ankle",
            "right_ankle",
        ],
        "kinematic drive-side diagnostic: -1 left, 0 indeterminate, 1 right, selected from event-local 2D knee extension; not support force or load-bearing side",
    ),
    "support_knee_extension_velocity_deg_s": _definition(
        "deg/s",
        "selected_positive_drive_side_or_strongest_observed_knee_extension_velocity",
        [
            "left_hip",
            "right_hip",
            "left_knee",
            "right_knee",
            "left_ankle",
            "right_ankle",
        ],
        "peak 2D knee-extension velocity on the positive kinematic drive side, or the strongest observed bilateral value when neither side extends; preserves non-positive evidence using timestamp_ms and is not measured muscular force",
    ),
    "hip_acceleration_along_launch_direction_body_s2": _definition(
        "body/s2",
        "peak_positive_projection_on_event_launch_direction",
        ["left_hip", "right_hip"],
        "peak hip-center proxy acceleration projected onto the event launch direction composed from hip displacement and velocity; not force",
    ),
    "support_drive_to_moving_foot_rise_proxy_ms": _definition(
        "ms",
        "moving_foot_peak_up_velocity_time_minus_positive_drive_or_strongest_observed_knee_peak_time",
        [
            "left_hip",
            "right_hip",
            "left_knee",
            "right_knee",
            "left_ankle",
            "right_ankle",
        ],
        "time from the positive drive-knee peak, or strongest observed bilateral knee-extension candidate when neither side extends, to opposite foot-reference peak upward velocity using timestamp_ms; not force-to-take-off contact timing",
    ),
    "launch_direction_deg": _definition(
        "deg",
        "event_hip_displacement_velocity_composite_direction",
        ["left_hip", "right_hip"],
        "event launch direction composed from hip-center proxy displacement and velocity; 0 image-right and 90 image-up, fixed-camera 2D only",
    ),
    "launch_side_code": _definition(
        "code",
        "larger_positive_foot_displacement_along_launch_direction",
        ["left_hip", "right_hip", "left_ankle", "right_ankle"],
        "kinematic moving-foot diagnostic: -1 left, 0 indeterminate, 1 right, based on event displacement along launch direction; not take-off or contact side",
    ),
    "launch_foot_speed_peak_body_s": _definition(
        "body/s",
        "selected_launch_foot_peak_speed",
        ["left_hip", "right_hip", "left_ankle", "right_ankle"],
        "peak image-plane speed of the kinematically selected launch foot reference using timestamp_ms, body-normalized",
    ),
    "launch_foot_relative_displacement_body": _definition(
        "body",
        "launch_minus_other_foot_projected_event_displacement",
        ["left_hip", "right_hip", "left_ankle", "right_ankle"],
        "selected launch-foot displacement minus other-foot displacement projected along launch direction, body-normalized; not ground clearance",
    ),
    "launch_foot_motion_duration_ms": _definition(
        "ms",
        "longest_half_peak_positive_projected_velocity_run",
        ["left_hip", "right_hip", "left_ankle", "right_ankle"],
        "longest selected-foot run above its event-local half-peak positive velocity envelope along launch direction; not airborne duration",
    ),
    "launch_foot_speed_drop_body_s": _definition(
        "body/s",
        "peak_speed_minus_post_peak_late_median",
        ["left_hip", "right_hip", "left_ankle", "right_ankle"],
        "selected launch-foot peak speed minus post-peak late median speed, body-normalized; kinematic slowdown proxy, not landing",
    ),
    "first_step_displacement_body": _definition(
        "body",
        "selected_launch_foot_projected_event_displacement",
        ["left_hip", "right_hip", "left_ankle", "right_ankle"],
        "selected launch-foot event displacement projected along launch direction, body-normalized; not confirmed first-footfall distance",
    ),
    "post_step_hip_direction_consistency": _definition(
        "ratio",
        "post_event_first_step_slowdown_phase_mean_velocity_cosine",
        ["left_hip", "right_hip", "left_ankle", "right_ankle"],
        "mean cosine alignment of hip-center proxy velocity with launch direction after the event's versioned first-step slowdown phase; 1 aligned, -1 opposite",
    ),
    "launch_foot_slowdown_to_post_hip_direction_ms": _definition(
        "ms",
        "event_first_step_slowdown_phase_to_alignment_envelope_start_time_delta",
        ["left_hip", "right_hip", "left_ankle", "right_ankle"],
        "time from the event's versioned first-step slowdown phase to the first post-phase hip-direction alignment envelope sample; not landing-to-balance timing",
    ),
    "post_step_stance_width_body": _definition(
        "body",
        "post_event_first_step_slowdown_phase_median",
        ["left_hip", "right_hip", "left_ankle", "right_ankle"],
        "median foot-reference separation after the event's versioned first-step slowdown phase, body-normalized; not confirmed post-landing support width",
    ),
}


@dataclass(frozen=True)
class FootReference:
    values: np.ndarray
    valid: np.ndarray
    mode: str
    required_joints: tuple[str, ...]
    confidence_multiplier: float
    fine_mask: np.ndarray
    ankle_fallback_mask: np.ndarray
    unaligned_ankle_fallback_mask: np.ndarray
    missing_mask: np.ndarray
    fine_topology_available: bool
    alignment_method: str
    alignment_overlap_count: int
    fine_minus_ankle_offset_xy: tuple[float, float] | None


@dataclass(frozen=True)
class FS01FS02EventSummary:
    value: float | int | None
    valid_mask: np.ndarray
    evidence_indexes: tuple[int, ...] = ()
    raw_series: dict[str, np.ndarray] = field(default_factory=dict)
    smoothed_series: dict[str, np.ndarray] = field(default_factory=dict)
    diagnostics: dict[str, Any] = field(default_factory=dict)
    required_joints: tuple[str, ...] = ()
    confidence_multiplier: float = 1.0
    reason: str = "valid_pose_kinematic_proxy"


def _finite_positive_scale(body_scale: float) -> float:
    value = float(body_scale)
    if not math.isfinite(value) or value <= 1e-12:
        raise ValueError("body_scale must be finite and positive")
    return value


def _mask_rows(values: np.ndarray, valid: np.ndarray) -> np.ndarray:
    result = np.asarray(values, dtype=np.float64).copy()
    result[~np.asarray(valid, dtype=bool)] = np.nan
    return result


def foot_reference_series(sequence: PoseSequence, side: str) -> FootReference:
    """Resolve each frame to fine-foot evidence, then an explicit ankle fallback."""

    if side not in {"left", "right"}:
        raise ValueError("side must be 'left' or 'right'")
    fine_names = (
        f"{side}_heel",
        f"{side}_big_toe",
        f"{side}_small_toe",
    )
    ankle_name = f"{side}_ankle"
    ankle_values, ankle_valid = point_series(sequence, ankle_name)
    fine_topology_available = all(
        name in sequence.keypoints_xy for name in fine_names
    )
    frame_count = sequence.timestamp_ms.size
    fine_mask = np.zeros(frame_count, dtype=bool)
    fine_values = np.full((frame_count, 2), np.nan, dtype=np.float64)
    if fine_topology_available:
        points_and_masks = [point_series(sequence, name) for name in fine_names]
        fine_mask = np.logical_and.reduce([item[1] for item in points_and_masks])
        fine_values = np.mean(
            np.stack([item[0] for item in points_and_masks]), axis=0
        )
        fine_values[~fine_mask] = np.nan
    overlap_mask = fine_mask & ankle_valid
    overlap_count = int(overlap_mask.sum())
    if not fine_topology_available:
        alignment_method = "not_required_ankle_only_topology"
        offset: np.ndarray | None = np.zeros(2, dtype=np.float64)
    elif overlap_count:
        alignment_method = (
            "robust_full_sequence_median_fine_centroid_minus_ankle"
        )
        offset = np.median(
            fine_values[overlap_mask] - ankle_values[overlap_mask], axis=0
        )
    else:
        alignment_method = "unavailable_no_same_frame_fine_ankle_overlap"
        offset = None
    fallback_candidates = ~fine_mask & ankle_valid
    ankle_fallback_mask = (
        fallback_candidates.copy()
        if offset is not None
        else np.zeros(frame_count, dtype=bool)
    )
    unaligned_ankle_fallback_mask = fallback_candidates & ~ankle_fallback_mask
    valid = fine_mask | ankle_fallback_mask
    missing_mask = ~fine_mask & ~ankle_valid
    values = np.full((frame_count, 2), np.nan, dtype=np.float64)
    values[fine_mask] = fine_values[fine_mask]
    if offset is not None:
        values[ankle_fallback_mask] = (
            ankle_values[ankle_fallback_mask] + offset
        )
    fine_count = int(fine_mask.sum())
    fallback_count = int(ankle_fallback_mask.sum())
    unaligned_count = int(unaligned_ankle_fallback_mask.sum())
    reference_candidate_count = fine_count + fallback_count + unaligned_count
    confidence_multiplier = (
        (
            fine_count
            + ANKLE_FALLBACK_CONFIDENCE_MULTIPLIER * fallback_count
        )
        / reference_candidate_count
        if reference_candidate_count
        else 0.0
    )
    if fine_count and fallback_count:
        mode = "mixed_fine_foot_with_per_frame_ankle_fallback"
    elif fine_count and unaligned_count:
        mode = "fine_foot_with_unaligned_ankle_fallback_unusable"
    elif fine_count:
        mode = "fine_foot_centroid_heel_big_toe_small_toe"
    elif fine_topology_available and unaligned_count:
        mode = "fine_foot_ankle_fallback_unaligned_unusable"
    elif fine_topology_available:
        mode = "fine_foot_topology_no_valid_reference"
    else:
        mode = "coco17_ankle_fallback"
    required: list[str] = []
    if fine_count:
        required.extend(fine_names)
    if fallback_count or unaligned_count:
        required.append(ankle_name)
    return FootReference(
        values=values,
        valid=valid,
        mode=mode,
        required_joints=tuple(required),
        confidence_multiplier=float(confidence_multiplier),
        fine_mask=fine_mask,
        ankle_fallback_mask=ankle_fallback_mask,
        unaligned_ankle_fallback_mask=unaligned_ankle_fallback_mask,
        missing_mask=missing_mask,
        fine_topology_available=fine_topology_available,
        alignment_method=alignment_method,
        alignment_overlap_count=overlap_count,
        fine_minus_ankle_offset_xy=(
            (float(offset[0]), float(offset[1])) if offset is not None else None
        ),
    )


def _knee_flexion(sequence: PoseSequence, side: str) -> tuple[np.ndarray, np.ndarray]:
    hip, hip_valid = point_series(sequence, f"{side}_hip")
    knee, knee_valid = point_series(sequence, f"{side}_knee")
    ankle, ankle_valid = point_series(sequence, f"{side}_ankle")
    valid = hip_valid & knee_valid & ankle_valid
    values = 180.0 - angle_three_points_deg(hip, knee, ankle)
    values[~valid] = np.nan
    return values, valid


def _event_edge_displacement(
    positions: np.ndarray,
    indexes: np.ndarray,
) -> tuple[np.ndarray | None, tuple[int, ...], dict[str, Any]]:
    finite = indexes[np.isfinite(positions[indexes]).all(axis=1)]
    if finite.size < 4:
        return None, (), {"valid_samples": int(finite.size)}
    count = max(2, int(math.ceil(finite.size * 0.25)))
    count = min(count, finite.size // 2)
    early = np.median(positions[finite[:count]], axis=0)
    late = np.median(positions[finite[-count:]], axis=0)
    return late - early, (int(finite[0]), int(finite[-1])), {
        "valid_samples": int(finite.size),
        "edge_fraction": 0.25,
        "edge_sample_count": int(count),
        "early_position": early.tolist(),
        "late_position": late.tolist(),
    }


def launch_direction_from_hip_motion(
    timestamp_ms: np.ndarray,
    hip_positions: np.ndarray,
    indexes: np.ndarray,
) -> tuple[np.ndarray | None, float | None, tuple[int, ...], dict[str, Any]]:
    """Compose a 2D launch direction from event hip displacement and velocity."""

    indexes = np.asarray(indexes, dtype=np.int64)
    displacement, evidence, diagnostics = _event_edge_displacement(
        hip_positions, indexes
    )
    velocity = irregular_derivative(timestamp_ms, hip_positions)
    finite_velocity = indexes[np.isfinite(velocity[indexes]).all(axis=1)]
    if displacement is None or finite_velocity.size < 2:
        return None, None, evidence, {
            **diagnostics,
            "reason": "insufficient_hip_displacement_or_velocity_samples",
        }
    velocity_vector = np.median(velocity[finite_velocity], axis=0)
    duration_s = float(timestamp_ms[indexes[-1]] - timestamp_ms[indexes[0]]) / 1000.0
    composite = displacement + velocity_vector * max(duration_s, 0.0)
    magnitude = float(np.linalg.norm(composite))
    if not math.isfinite(magnitude) or magnitude <= 1e-12:
        return None, None, evidence, {
            **diagnostics,
            "median_velocity": velocity_vector.tolist(),
            "duration_s": duration_s,
            "reason": "hip_motion_direction_indeterminate",
        }
    unit = composite / magnitude
    direction_deg = float(np.degrees(np.arctan2(-unit[1], unit[0])))
    return unit, direction_deg, evidence, {
        **diagnostics,
        "median_velocity": velocity_vector.tolist(),
        "duration_s": duration_s,
        "composite_vector": composite.tolist(),
        "coordinate_convention": "0_deg_image_right_90_deg_image_up",
        "direction_source": "hip_displacement_plus_median_velocity_times_duration",
    }


def hip_acceleration_along_launch_direction_from_series(
    timestamp_ms: np.ndarray,
    hip_positions: np.ndarray,
    hip_acceleration_xy_body_s2: np.ndarray,
    indexes: np.ndarray,
) -> tuple[float | None, np.ndarray, tuple[int, ...], dict[str, Any]]:
    """Project 2D hip acceleration onto the event-local hip launch direction.

    The caller decides whether the inputs are raw or production-smoothed.  By
    keeping direction composition and projection in one pure function, feature
    production and no-extra-smoothing evaluation cannot silently drift.
    """

    timestamp_input = np.asarray(timestamp_ms)
    sample_count = int(timestamp_input.size) if timestamp_input.ndim == 1 else 0
    projection = np.full(sample_count, np.nan, dtype=np.float64)
    try:
        timestamps = np.asarray(timestamp_ms, dtype=np.float64)
        positions = np.asarray(hip_positions, dtype=np.float64)
        acceleration = np.asarray(
            hip_acceleration_xy_body_s2, dtype=np.float64
        )
        selected = np.asarray(indexes, dtype=np.int64)
    except (TypeError, ValueError, OverflowError):
        return None, projection, (), {
            "reason": "invalid_or_misaligned_hip_kinematic_series",
            "sample_count": sample_count,
        }
    if (
        timestamps.ndim != 1
        or positions.shape != (sample_count, 2)
        or acceleration.shape != (sample_count, 2)
        or selected.ndim != 1
        or selected.size == 0
        or np.any(selected < 0)
        or np.any(selected >= sample_count)
        or np.any(np.diff(selected) <= 0)
        or np.any(~np.isfinite(timestamps))
        or np.any(np.diff(timestamps) <= 0)
    ):
        return None, projection, (), {
            "reason": "invalid_or_misaligned_hip_kinematic_series",
            "sample_count": sample_count,
        }

    direction_unit, direction_deg, direction_evidence, direction_diag = (
        launch_direction_from_hip_motion(timestamps, positions, selected)
    )
    if direction_unit is not None:
        projection = acceleration @ direction_unit
    peak_index, value = _peak_index(projection, selected)
    evidence = tuple(
        sorted(
            set(
                direction_evidence
                + ((peak_index,) if peak_index is not None else ())
            )
        )
    )
    return value, projection, evidence, {
        **direction_diag,
        "launch_direction_deg": direction_deg,
        "peak_projection_index": peak_index,
        "proxy_status": "hip_center_kinematics_not_force",
    }


def launch_foot_event_kinematics_from_series(
    timestamp_ms: np.ndarray,
    hip_position: np.ndarray,
    left_foot_position: np.ndarray,
    right_foot_position: np.ndarray,
    left_foot_velocity: np.ndarray,
    right_foot_velocity: np.ndarray,
    indexes: np.ndarray,
    body_scale: float,
) -> dict[str, Any]:
    """Compute the shared FS02-M04 image-plane launch-foot proxies.

    Production and no-extra-smoothing evaluation both call this function.  It
    deliberately consumes position *and* velocity evidence on one timestamp
    grid so that launch-side selection, peak speed, projected displacement and
    the half-peak motion-duration envelope cannot drift independently.
    """

    timestamp_input = np.asarray(timestamp_ms)
    sample_count = int(timestamp_input.size) if timestamp_input.ndim == 1 else 0
    empty_vector = np.full((sample_count, 2), np.nan, dtype=np.float64)
    empty_scalar = np.full(sample_count, np.nan, dtype=np.float64)
    empty_mask = np.zeros(sample_count, dtype=bool)
    invalid = {
        "launch_code": None,
        "selected_side": None,
        "direction_unit": None,
        "direction_deg": None,
        "left_projected_displacement_body": None,
        "right_projected_displacement_body": None,
        "relative_displacement_body": None,
        "selected_displacement_body": None,
        "selected_speed_body_s": empty_scalar.copy(),
        "speed_peak_body_s": None,
        "speed_peak_index": None,
        "projected_velocity_body_s": empty_scalar.copy(),
        "projected_velocity_peak_body_s": None,
        "projected_velocity_peak_index": None,
        "motion_proxy_mask": empty_mask,
        "motion_duration_ms": None,
        "launch_side_evidence": (),
        "left_displacement_evidence": (),
        "right_displacement_evidence": (),
        "relative_displacement_evidence": (),
        "selected_displacement_evidence": (),
        "motion_duration_evidence": (),
        "diagnostics": {
            "reason": "invalid_or_misaligned_launch_foot_kinematic_series",
            "sample_count": sample_count,
        },
    }
    parsed = _validated_event_vector_series(
        timestamp_ms,
        indexes,
        {
            "hip_position": hip_position,
            "left_foot_position": left_foot_position,
            "right_foot_position": right_foot_position,
            "left_foot_velocity": left_foot_velocity,
            "right_foot_velocity": right_foot_velocity,
        },
    )
    try:
        scale = _finite_positive_scale(body_scale)
    except (TypeError, ValueError, OverflowError):
        return invalid
    if parsed is None:
        return invalid
    timestamps, selected, series, validation = parsed

    direction_unit, direction_deg, direction_evidence, direction_diag = (
        launch_direction_from_hip_motion(
            timestamps, series["hip_position"], selected
        )
    )
    left_displacement, left_evidence, left_diag = _event_edge_displacement(
        series["left_foot_position"], selected
    )
    right_displacement, right_evidence, right_diag = _event_edge_displacement(
        series["right_foot_position"], selected
    )
    left_projected = (
        float(left_displacement @ direction_unit) / scale
        if left_displacement is not None and direction_unit is not None
        else None
    )
    right_projected = (
        float(right_displacement @ direction_unit) / scale
        if right_displacement is not None and direction_unit is not None
        else None
    )
    launch_code, launch_side, launch_reason = _side_selection(
        left_projected, right_projected
    )
    relative_displacement = (
        left_projected - right_projected
        if launch_side == "left"
        and left_projected is not None
        and right_projected is not None
        else right_projected - left_projected
        if launch_side == "right"
        and left_projected is not None
        and right_projected is not None
        else None
    )
    selected_displacement = (
        left_projected
        if launch_side == "left"
        else right_projected
        if launch_side == "right"
        else None
    )
    selected_velocity = (
        series[f"{launch_side}_foot_velocity"]
        if launch_side is not None
        else empty_vector
    )
    selected_speed = np.linalg.norm(selected_velocity, axis=1) / scale
    selected_speed[~np.isfinite(selected_velocity).all(axis=1)] = np.nan
    speed_index, speed_peak = _peak_index(selected_speed, selected)

    projected_velocity = empty_scalar.copy()
    if launch_side is not None and direction_unit is not None:
        projected_velocity = selected_velocity @ direction_unit / scale
        projected_velocity[~np.isfinite(selected_velocity).all(axis=1)] = np.nan
    projected_index, projected_peak = _peak_index(projected_velocity, selected)
    motion_mask = empty_mask.copy()
    if projected_peak is not None and projected_peak > 0.0:
        motion_mask[selected] = (
            projected_velocity[selected] >= 0.5 * projected_peak
        )
    motion_duration, motion_evidence = _longest_true_run(
        timestamps, selected, motion_mask
    )
    launch_evidence = tuple(
        sorted(set(direction_evidence + left_evidence + right_evidence))
    )
    relative_evidence = tuple(sorted(set(left_evidence + right_evidence)))
    selected_evidence = (
        left_evidence
        if launch_side == "left"
        else right_evidence
        if launch_side == "right"
        else ()
    )
    return {
        "launch_code": launch_code,
        "selected_side": launch_side,
        "direction_unit": direction_unit,
        "direction_deg": direction_deg,
        "left_projected_displacement_body": left_projected,
        "right_projected_displacement_body": right_projected,
        "relative_displacement_body": relative_displacement,
        "selected_displacement_body": selected_displacement,
        "selected_speed_body_s": selected_speed,
        "speed_peak_body_s": speed_peak,
        "speed_peak_index": speed_index,
        "projected_velocity_body_s": projected_velocity,
        "projected_velocity_peak_body_s": projected_peak,
        "projected_velocity_peak_index": projected_index,
        "motion_proxy_mask": motion_mask,
        "motion_duration_ms": motion_duration,
        "launch_side_evidence": launch_evidence,
        "left_displacement_evidence": left_evidence,
        "right_displacement_evidence": right_evidence,
        "relative_displacement_evidence": relative_evidence,
        "selected_displacement_evidence": selected_evidence,
        "motion_duration_evidence": motion_evidence,
        "diagnostics": {
            **validation,
            "code_map": {"-1": "left", "0": "indeterminate", "1": "right"},
            "selected_side": launch_side,
            "selection_reason": launch_reason,
            "left_projected_displacement_body": left_projected,
            "right_projected_displacement_body": right_projected,
            "left_displacement_summary": left_diag,
            "right_displacement_summary": right_diag,
            "launch_direction_deg": direction_deg,
            "direction_source": direction_diag,
            "speed_peak_index": speed_index,
            "speed_peak_body_s": speed_peak,
            "projected_velocity_peak_index": projected_index,
            "projected_velocity_peak_body_s": projected_peak,
            "half_peak_projected_velocity_envelope_body_s": (
                projected_peak * 0.5 if projected_peak is not None else None
            ),
            "envelope_status": "event_local_half_peak_motion_not_airborne",
            "proxy_status": "foot_kinematics_not_takeoff_contact_or_load",
        },
    }


def first_step_phase_kinematics_from_series(
    timestamp_ms: np.ndarray,
    hip_position: np.ndarray,
    hip_velocity: np.ndarray,
    left_foot_position: np.ndarray,
    right_foot_position: np.ndarray,
    left_foot_velocity: np.ndarray,
    right_foot_velocity: np.ndarray,
    indexes: np.ndarray,
    body_scale: float,
    key_phases: dict[str, int | None] | None = None,
) -> dict[str, Any]:
    """Compute the shared FS02-M05 first-step kinematic proxies.

    The event's versioned ``first_step_slowdown_proxy_ms`` remains fixed when
    supplied.  Only legacy callers without that phase use the selected-foot
    speed slowdown as a fallback.  Production and no-extra-smoothing replay
    therefore share launch-side selection, the phase anchor, post-phase hip
    direction, and stance-width semantics without confusing a foot-motion
    proxy with observed landing or ground contact.
    """

    timestamp_input = np.asarray(timestamp_ms)
    sample_count = int(timestamp_input.size) if timestamp_input.ndim == 1 else 0
    empty_scalar = np.full(sample_count, np.nan, dtype=np.float64)
    invalid = {
        "launch_code": None,
        "selected_side": None,
        "selected_speed_body_s": empty_scalar.copy(),
        "speed_slowdown_index": None,
        "speed_drop_body_s": None,
        "speed_drop_evidence": (),
        "first_step_displacement_body": None,
        "first_step_displacement_evidence": (),
        "post_step_anchor_index": None,
        "post_indexes": np.asarray([], dtype=np.int64),
        "hip_launch_direction_cosine": empty_scalar.copy(),
        "post_direction_consistency": None,
        "post_direction_consistency_evidence": (),
        "post_direction_alignment_envelope": None,
        "post_direction_alignment_index": None,
        "slowdown_to_post_direction_ms": None,
        "slowdown_to_post_direction_evidence": (),
        "stance_width_body": empty_scalar.copy(),
        "post_stance_width_body": None,
        "post_stance_width_evidence": (),
        "launch": None,
        "slowdown_diagnostics": {"valid_samples": 0},
        "post_step_anchor_diagnostics": {
            "reason": "invalid_or_misaligned_first_step_kinematic_series",
            "sample_count": sample_count,
        },
        "diagnostics": {
            "reason": "invalid_or_misaligned_first_step_kinematic_series",
            "sample_count": sample_count,
        },
    }
    parsed = _validated_event_vector_series(
        timestamp_ms,
        indexes,
        {
            "hip_position": hip_position,
            "hip_velocity": hip_velocity,
            "left_foot_position": left_foot_position,
            "right_foot_position": right_foot_position,
            "left_foot_velocity": left_foot_velocity,
            "right_foot_velocity": right_foot_velocity,
        },
    )
    try:
        scale = _finite_positive_scale(body_scale)
    except (TypeError, ValueError, OverflowError):
        return invalid
    if parsed is None:
        return invalid
    timestamps, selected, series, validation = parsed

    launch = launch_foot_event_kinematics_from_series(
        timestamps,
        series["hip_position"],
        series["left_foot_position"],
        series["right_foot_position"],
        series["left_foot_velocity"],
        series["right_foot_velocity"],
        selected,
        scale,
    )
    selected_speed = launch["selected_speed_body_s"]
    slowdown_index, speed_drop, slowdown_diagnostics = (
        _speed_slowdown_index(selected_speed, selected)
        if launch["selected_side"] is not None
        else (None, None, {"valid_samples": 0})
    )
    phase_anchor_index, phase_diagnostics, phase_declared = (
        _event_phase_anchor_index(
            timestamps,
            selected,
            key_phases,
            "first_step_slowdown_proxy_ms",
        )
    )
    post_anchor_index = (
        phase_anchor_index
        if phase_anchor_index is not None
        else slowdown_index
        if not phase_declared
        else None
    )
    post_indexes = (
        selected[selected >= post_anchor_index]
        if post_anchor_index is not None
        else np.asarray([], dtype=np.int64)
    )

    hip_velocity_values = series["hip_velocity"]
    hip_speed = np.linalg.norm(hip_velocity_values, axis=1)
    alignment = empty_scalar.copy()
    direction_unit = launch["direction_unit"]
    if direction_unit is not None:
        finite_speed = np.isfinite(hip_velocity_values).all(axis=1) & (
            hip_speed > 1e-12
        )
        alignment[finite_speed] = (
            hip_velocity_values[finite_speed] @ direction_unit
            / hip_speed[finite_speed]
        )
    finite_alignment = post_indexes[np.isfinite(alignment[post_indexes])]
    direction_consistency = (
        float(np.mean(alignment[finite_alignment]))
        if finite_alignment.size
        else None
    )
    direction_consistency_evidence = (
        (
            int(
                finite_alignment[
                    np.argmin(
                        np.abs(
                            alignment[finite_alignment] - direction_consistency
                        )
                    )
                ]
            ),
        )
        if direction_consistency is not None
        else ()
    )
    if finite_alignment.size:
        alignment_envelope = float(np.median(alignment[finite_alignment]))
        alignment_candidates = finite_alignment[
            alignment[finite_alignment] >= alignment_envelope
        ]
        alignment_index = (
            int(alignment_candidates[0])
            if alignment_candidates.size
            else None
        )
    else:
        alignment_envelope = None
        alignment_index = None
    slowdown_to_direction = (
        int(timestamps[alignment_index] - timestamps[post_anchor_index])
        if alignment_index is not None and post_anchor_index is not None
        else None
    )
    slowdown_to_direction_evidence = tuple(
        item
        for item in (post_anchor_index, alignment_index)
        if item is not None
    )

    stance_width = np.linalg.norm(
        series["right_foot_position"] - series["left_foot_position"], axis=1
    ) / scale
    stance_width[
        ~(
            np.isfinite(series["left_foot_position"]).all(axis=1)
            & np.isfinite(series["right_foot_position"]).all(axis=1)
        )
    ] = np.nan
    finite_stance = post_indexes[np.isfinite(stance_width[post_indexes])]
    post_stance_width = (
        float(np.median(stance_width[finite_stance]))
        if finite_stance.size
        else None
    )
    post_stance_evidence = (
        (
            int(
                finite_stance[
                    np.argmin(
                        np.abs(stance_width[finite_stance] - post_stance_width)
                    )
                ]
            ),
        )
        if post_stance_width is not None
        else ()
    )
    post_anchor_diagnostics = {
        **slowdown_diagnostics,
        **phase_diagnostics,
        "computed_signal_slowdown_timestamp_ms": (
            int(timestamps[slowdown_index])
            if slowdown_index is not None
            else None
        ),
    }
    return {
        "launch_code": launch["launch_code"],
        "selected_side": launch["selected_side"],
        "selected_speed_body_s": selected_speed,
        "speed_slowdown_index": slowdown_index,
        "speed_drop_body_s": speed_drop,
        "speed_drop_evidence": tuple(
            item
            for item in (launch["speed_peak_index"], slowdown_index)
            if item is not None
        ),
        "first_step_displacement_body": launch["selected_displacement_body"],
        "first_step_displacement_evidence": launch[
            "selected_displacement_evidence"
        ],
        "post_step_anchor_index": post_anchor_index,
        "post_indexes": post_indexes,
        "hip_launch_direction_cosine": alignment,
        "post_direction_consistency": direction_consistency,
        "post_direction_consistency_evidence": direction_consistency_evidence,
        "post_direction_alignment_envelope": alignment_envelope,
        "post_direction_alignment_index": alignment_index,
        "slowdown_to_post_direction_ms": slowdown_to_direction,
        "slowdown_to_post_direction_evidence": slowdown_to_direction_evidence,
        "stance_width_body": stance_width,
        "post_stance_width_body": post_stance_width,
        "post_stance_width_evidence": post_stance_evidence,
        "launch": launch,
        "slowdown_diagnostics": slowdown_diagnostics,
        "post_step_anchor_diagnostics": post_anchor_diagnostics,
        "diagnostics": {
            **validation,
            "selected_side": launch["selected_side"],
            "launch_direction_deg": launch["direction_deg"],
            **post_anchor_diagnostics,
            "valid_post_direction_samples": int(finite_alignment.size),
            "valid_post_stance_samples": int(finite_stance.size),
            "post_slowdown_alignment_median_envelope": alignment_envelope,
            "envelope_status": (
                "event_local_direction_envelope_not_balance_threshold"
            ),
            "proxy_status": (
                "first_step_2d_kinematics_not_landing_contact_or_load"
            ),
        },
    }


def _longest_true_run(
    timestamp_ms: np.ndarray,
    indexes: np.ndarray,
    mask: np.ndarray,
) -> tuple[int | None, tuple[int, ...]]:
    indexes = np.asarray(indexes, dtype=np.int64)
    if indexes.size == 0:
        return None, ()
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
        return None, ()
    first = int(indexes[longest[0]])
    last = int(indexes[longest[1]])
    local_dt = np.diff(np.asarray(timestamp_ms, dtype=np.int64)[indexes])
    median_dt = int(np.median(local_dt)) if local_dt.size else 0
    duration = int(timestamp_ms[last] - timestamp_ms[first] + median_dt)
    return max(duration, 0), (first, last)


def _peak_index(values: np.ndarray, indexes: np.ndarray) -> tuple[int | None, float | None]:
    finite = indexes[np.isfinite(values[indexes])]
    if finite.size == 0:
        return None, None
    selected = int(finite[np.argmax(values[finite])])
    return selected, float(values[selected])


def _event_local_rise(
    positions: np.ndarray,
    indexes: np.ndarray,
) -> tuple[np.ndarray, float | None, int | None, dict[str, Any]]:
    finite = indexes[np.isfinite(positions[indexes]).all(axis=1)]
    values = np.full(positions.shape[0], np.nan, dtype=np.float64)
    if finite.size < 3:
        return values, None, None, {"valid_samples": int(finite.size)}
    count = max(2, int(math.ceil(finite.size * 0.25)))
    count = min(count, finite.size)
    early_y = float(np.median(positions[finite[:count], 1]))
    values[finite] = early_y - positions[finite, 1]
    peak_index, peak = _peak_index(values, indexes)
    return values, peak, peak_index, {
        "valid_samples": int(finite.size),
        "early_event_local_y_envelope": early_y,
        "edge_fraction": 0.25,
        "envelope_status": "event_local_kinematic_reference_not_ground_baseline",
    }


def _validated_event_vector_series(
    timestamp_ms: np.ndarray,
    indexes: np.ndarray,
    series_by_name: dict[str, np.ndarray],
) -> tuple[np.ndarray, np.ndarray, dict[str, np.ndarray], dict[str, Any]] | None:
    """Validate one timestamp grid shared by event-local 2D vector series.

    This deliberately validates shape and ordering rather than repairing an
    evidence payload.  Production and error-budget replay must fail closed on
    a shifted grid; neither path may infer a missing timestamp or fill a
    missing coordinate with zero.
    """

    try:
        timestamps = np.asarray(timestamp_ms, dtype=np.float64)
        raw_indexes = np.asarray(indexes)
        selected = np.asarray(indexes, dtype=np.int64)
        converted = {
            name: np.asarray(values, dtype=np.float64)
            for name, values in series_by_name.items()
        }
    except (TypeError, ValueError, OverflowError):
        return None
    sample_count = int(timestamps.size) if timestamps.ndim == 1 else 0
    indexes_are_integral = False
    if raw_indexes.ndim == 1:
        try:
            numeric_indexes = np.asarray(raw_indexes, dtype=np.float64)
            indexes_are_integral = bool(
                np.all(np.isfinite(numeric_indexes))
                and np.array_equal(numeric_indexes, selected.astype(np.float64))
            )
        except (TypeError, ValueError, OverflowError):
            indexes_are_integral = False
    if (
        timestamps.ndim != 1
        or sample_count == 0
        or selected.ndim != 1
        or selected.size == 0
        or not indexes_are_integral
        or np.any(selected < 0)
        or np.any(selected >= sample_count)
        or np.any(np.diff(selected) <= 0)
        or np.any(~np.isfinite(timestamps))
        or np.any(np.diff(timestamps) <= 0)
        or any(values.shape != (sample_count, 2) for values in converted.values())
    ):
        return None
    return timestamps, selected, converted, {
        "sample_count": sample_count,
        "event_sample_count": int(selected.size),
        "timestamp_source": "timestamp_ms",
        "validation_status": "strict_aligned_series",
    }


def _bilateral_foot_rise_components(
    timestamp_ms: np.ndarray,
    left_foot_position: np.ndarray,
    right_foot_position: np.ndarray,
    indexes: np.ndarray,
    body_scale: float,
) -> tuple[
    np.ndarray,
    np.ndarray,
    float | None,
    float | None,
    int | None,
    int | None,
    np.ndarray,
    dict[str, Any],
] | None:
    parsed = _validated_event_vector_series(
        timestamp_ms,
        indexes,
        {
            "left_foot_position": left_foot_position,
            "right_foot_position": right_foot_position,
        },
    )
    try:
        scale = _finite_positive_scale(body_scale)
    except (TypeError, ValueError, OverflowError):
        return None
    if parsed is None:
        return None
    timestamps, selected, series, validation = parsed
    left_rise, left_peak, left_index, left_diag = _event_local_rise(
        series["left_foot_position"], selected
    )
    right_rise, right_peak, right_index, right_diag = _event_local_rise(
        series["right_foot_position"], selected
    )
    return (
        left_rise / scale,
        right_rise / scale,
        left_peak,
        right_peak,
        left_index,
        right_index,
        selected,
        {
            **validation,
            "left": left_diag,
            "right": right_diag,
            "body_scale": scale,
            "timestamp_start_ms": float(timestamps[selected[0]]),
            "timestamp_end_ms": float(timestamps[selected[-1]]),
        },
    )


def bilateral_foot_rise_from_position_series(
    timestamp_ms: np.ndarray,
    left_foot_position: np.ndarray,
    right_foot_position: np.ndarray,
    indexes: np.ndarray,
    body_scale: float,
) -> tuple[
    float | None,
    np.ndarray,
    np.ndarray,
    tuple[int, ...],
    dict[str, Any],
]:
    """Return the minimum bilateral event-local rise on a strict grid."""

    sample_count = (
        int(np.asarray(timestamp_ms).size)
        if np.asarray(timestamp_ms).ndim == 1
        else 0
    )
    unavailable = np.full(sample_count, np.nan, dtype=np.float64)
    components = _bilateral_foot_rise_components(
        timestamp_ms,
        left_foot_position,
        right_foot_position,
        indexes,
        body_scale,
    )
    if components is None:
        return None, unavailable, unavailable.copy(), (), {
            "reason": "invalid_or_misaligned_bilateral_foot_position_series"
        }
    (
        left_rise_body,
        right_rise_body,
        left_peak,
        right_peak,
        left_index,
        right_index,
        _,
        diagnostics,
    ) = components
    scale = float(diagnostics["body_scale"])
    value = (
        min(left_peak, right_peak) / scale
        if left_peak is not None and right_peak is not None
        else None
    )
    evidence = tuple(
        item for item in (left_index, right_index) if item is not None
    )
    return value, left_rise_body, right_rise_body, evidence, {
        **diagnostics,
        "left_peak_upward_excursion_body": (
            left_peak / scale if left_peak is not None else None
        ),
        "right_peak_upward_excursion_body": (
            right_peak / scale if right_peak is not None else None
        ),
        "bilateral_aggregation": "minimum",
    }


def bilateral_foot_rise_synchrony_from_velocity_series(
    timestamp_ms: np.ndarray,
    left_foot_velocity: np.ndarray,
    right_foot_velocity: np.ndarray,
    indexes: np.ndarray,
    body_scale: float,
) -> tuple[
    int | None,
    np.ndarray,
    np.ndarray,
    tuple[int, ...],
    dict[str, Any],
]:
    """Return bilateral peak-upward-velocity timing separation in ms."""

    sample_count = (
        int(np.asarray(timestamp_ms).size)
        if np.asarray(timestamp_ms).ndim == 1
        else 0
    )
    unavailable = np.full(sample_count, np.nan, dtype=np.float64)
    parsed = _validated_event_vector_series(
        timestamp_ms,
        indexes,
        {
            "left_foot_velocity": left_foot_velocity,
            "right_foot_velocity": right_foot_velocity,
        },
    )
    try:
        scale = _finite_positive_scale(body_scale)
    except (TypeError, ValueError, OverflowError):
        parsed = None
    if parsed is None:
        return None, unavailable, unavailable.copy(), (), {
            "reason": "invalid_or_misaligned_bilateral_foot_velocity_series"
        }
    timestamps, selected, series, validation = parsed
    left_up = -series["left_foot_velocity"][:, 1] / scale
    right_up = -series["right_foot_velocity"][:, 1] / scale
    left_index, left_peak = _peak_index(left_up, selected)
    right_index, right_peak = _peak_index(right_up, selected)
    value = (
        abs(int(timestamps[left_index]) - int(timestamps[right_index]))
        if left_index is not None and right_index is not None
        else None
    )
    evidence = tuple(
        item for item in (left_index, right_index) if item is not None
    )
    return value, left_up, right_up, evidence, {
        **validation,
        "left_peak_upward_velocity_body_s": left_peak,
        "right_peak_upward_velocity_body_s": right_peak,
        "left_peak_index": left_index,
        "right_peak_index": right_index,
        "body_scale": scale,
        "timing_source": "timestamp_ms",
    }


def bilateral_foot_rise_duration_from_position_series(
    timestamp_ms: np.ndarray,
    left_foot_position: np.ndarray,
    right_foot_position: np.ndarray,
    indexes: np.ndarray,
    body_scale: float,
    bilateral_valid_mask: np.ndarray,
) -> tuple[
    int | None,
    np.ndarray,
    np.ndarray,
    np.ndarray,
    tuple[int, ...],
    dict[str, Any],
]:
    """Return the timestamp-based bilateral rise proxy duration.

    Numeric zero is returned only for a completely observed event with no
    simultaneous half-peak run.  Missing positions or an excessive timestamp
    gap remain unavailable and are never converted to zero.
    """

    sample_count = (
        int(np.asarray(timestamp_ms).size)
        if np.asarray(timestamp_ms).ndim == 1
        else 0
    )
    unavailable = np.full(sample_count, np.nan, dtype=np.float64)
    empty_mask = np.full(sample_count, np.nan, dtype=np.float64)
    components = _bilateral_foot_rise_components(
        timestamp_ms,
        left_foot_position,
        right_foot_position,
        indexes,
        body_scale,
    )
    try:
        valid_mask = np.asarray(bilateral_valid_mask, dtype=bool)
    except (TypeError, ValueError, OverflowError):
        valid_mask = np.asarray([], dtype=bool)
    if components is None or valid_mask.shape != (sample_count,):
        return None, unavailable, unavailable.copy(), empty_mask, (), {
            "reason": "invalid_or_misaligned_bilateral_foot_position_series",
            "duration_observation_status": (
                "unavailable_incomplete_bilateral_observation"
            ),
        }
    (
        left_rise_body,
        right_rise_body,
        left_peak,
        right_peak,
        left_index,
        right_index,
        selected,
        diagnostics,
    ) = components
    scale = float(diagnostics["body_scale"])
    mask = np.zeros(sample_count, dtype=bool)
    if (
        left_peak is not None
        and right_peak is not None
        and left_peak > _NORMALIZED_COORDINATE_NUMERICAL_ZERO
        and right_peak > _NORMALIZED_COORDINATE_NUMERICAL_ZERO
    ):
        mask[selected] = (
            left_rise_body[selected] >= 0.5 * left_peak / scale
        ) & (right_rise_body[selected] >= 0.5 * right_peak / scale)
    timestamps = np.asarray(timestamp_ms, dtype=np.float64)
    timestamp_gaps = np.diff(timestamps[selected])
    max_observed_gap_ms = (
        int(timestamp_gaps.max()) if timestamp_gaps.size else 0
    )
    temporal_coverage_complete = bool(
        selected.size
        and max_observed_gap_ms <= FS01_FS02_MAX_TEMPORAL_GAP_MS
    )
    duration, evidence = (
        _longest_true_run(timestamps, selected, mask)
        if temporal_coverage_complete
        else (None, ())
    )
    if duration is not None:
        duration = int(duration)
    complete_bilateral_observation = bool(
        selected.size
        and left_peak is not None
        and right_peak is not None
        and valid_mask[selected].all()
        and temporal_coverage_complete
    )
    observed_zero = duration is None and complete_bilateral_observation
    if observed_zero:
        duration = 0
        evidence = tuple(
            dict.fromkeys(
                int(item)
                for item in (
                    int(selected[0]),
                    left_index,
                    right_index,
                    int(selected[-1]),
                )
                if item is not None
            )
        )
    mask_payload = mask.astype(np.float64)
    mask_payload[~valid_mask] = np.nan
    reason = (
        "valid_pose_rise_duration_proxy_not_airborne"
        if duration is not None and duration > 0
        else "valid_observed_zero_simultaneous_bilateral_rise_proxy"
        if observed_zero
        else "bilateral_rise_proxy_temporal_gap_exceeds_smoothing_contract"
        if not temporal_coverage_complete
        else "bilateral_rise_proxy_not_observable"
    )
    return duration, left_rise_body, right_rise_body, mask_payload, evidence, {
        **diagnostics,
        "left_half_peak_envelope_body": (
            left_peak / scale * 0.5 if left_peak is not None else None
        ),
        "right_half_peak_envelope_body": (
            right_peak / scale * 0.5 if right_peak is not None else None
        ),
        "envelope_status": (
            "event_local_half_peak_kinematic_envelope_not_airborne_or_contact"
        ),
        "duration_observation_status": (
            "observed_positive_simultaneous_run"
            if duration is not None and duration > 0
            else "observed_zero_no_simultaneous_run"
            if observed_zero
            else "unavailable_incomplete_bilateral_observation"
        ),
        "zero_semantics": "observed_absence_not_missing" if observed_zero else None,
        "complete_bilateral_observation": complete_bilateral_observation,
        "temporal_coverage_complete": temporal_coverage_complete,
        "max_observed_gap_ms": max_observed_gap_ms,
        "allowed_max_gap_ms": FS01_FS02_MAX_TEMPORAL_GAP_MS,
        "normalized_coordinate_numerical_zero_tolerance": (
            _NORMALIZED_COORDINATE_NUMERICAL_ZERO
        ),
        "reason": reason,
    }


def hip_center_vertical_velocity_from_position_series(
    timestamp_ms: np.ndarray,
    hip_position: np.ndarray,
    indexes: np.ndarray,
    body_scale: float,
) -> tuple[float | None, np.ndarray, tuple[int, ...], dict[str, Any]]:
    """Return peak image-up hip velocity derived with actual timestamps."""

    sample_count = (
        int(np.asarray(timestamp_ms).size)
        if np.asarray(timestamp_ms).ndim == 1
        else 0
    )
    unavailable = np.full(sample_count, np.nan, dtype=np.float64)
    parsed = _validated_event_vector_series(
        timestamp_ms,
        indexes,
        {"hip_position": hip_position},
    )
    try:
        scale = _finite_positive_scale(body_scale)
    except (TypeError, ValueError, OverflowError):
        parsed = None
    if parsed is None:
        return None, unavailable, (), {
            "reason": "invalid_or_misaligned_hip_position_series"
        }
    timestamps, selected, series, validation = parsed
    velocity = irregular_derivative(timestamps, series["hip_position"])
    hip_up_velocity = -velocity[:, 1] / scale
    index, value = _peak_index(hip_up_velocity, selected)
    return value, hip_up_velocity, (index,) if index is not None else (), {
        **validation,
        "peak_index": index,
        "body_scale": scale,
        "sign_convention": "positive_image_up",
        "center_status": "hip_center_visual_proxy_not_biomechanical_com",
    }


def _vertical_slowdown_index(
    timestamp_ms: np.ndarray,
    positions: np.ndarray,
    indexes: np.ndarray,
) -> tuple[int | None, dict[str, Any]]:
    velocity_y = irregular_derivative(timestamp_ms, positions)[:, 1]
    finite = indexes[np.isfinite(velocity_y[indexes])]
    if finite.size < 3:
        return None, {"valid_samples": int(finite.size)}
    downward_peak_position = int(np.argmax(velocity_y[finite]))
    downward_peak_index = int(finite[downward_peak_position])
    tail = finite[downward_peak_position:]
    absolute_tail = np.abs(velocity_y[tail])
    envelope = float(np.percentile(absolute_tail, 35.0))
    candidates = tail[absolute_tail <= envelope]
    selected = int(candidates[0]) if candidates.size else None
    return selected, {
        "valid_samples": int(finite.size),
        "downward_velocity_peak_index": downward_peak_index,
        "absolute_vertical_velocity_p35_envelope": envelope,
        "envelope_status": "event_local_kinematic_slowdown_not_contact",
    }


def _bilateral_vertical_slowdown_components(
    timestamp_ms: np.ndarray,
    left_foot_position: np.ndarray,
    right_foot_position: np.ndarray,
    indexes: np.ndarray,
    body_scale: float,
) -> dict[str, Any] | None:
    parsed = _validated_event_vector_series(
        timestamp_ms,
        indexes,
        {
            "left_foot_position": left_foot_position,
            "right_foot_position": right_foot_position,
        },
    )
    try:
        scale = _finite_positive_scale(body_scale)
    except (TypeError, ValueError, OverflowError):
        return None
    if parsed is None:
        return None
    timestamps, selected, series, validation = parsed
    left_slowdown, left_diagnostics = _vertical_slowdown_index(
        timestamps, series["left_foot_position"], selected
    )
    right_slowdown, right_diagnostics = _vertical_slowdown_index(
        timestamps, series["right_foot_position"], selected
    )
    start = (
        max(left_slowdown, right_slowdown)
        if left_slowdown is not None and right_slowdown is not None
        else None
    )
    post_indexes = (
        selected[selected >= start]
        if start is not None
        else np.asarray([], dtype=np.int64)
    )
    left_velocity = irregular_derivative(
        timestamps, series["left_foot_position"]
    )[:, 1] / scale
    right_velocity = irregular_derivative(
        timestamps, series["right_foot_position"]
    )[:, 1] / scale
    return {
        "timestamps": timestamps,
        "indexes": selected,
        "left_foot_position": series["left_foot_position"],
        "right_foot_position": series["right_foot_position"],
        "left_slowdown": left_slowdown,
        "right_slowdown": right_slowdown,
        "post_indexes": post_indexes,
        "left_vertical_velocity_body_s": left_velocity,
        "right_vertical_velocity_body_s": right_velocity,
        "body_scale": scale,
        "diagnostics": {
            **validation,
            "left": left_diagnostics,
            "right": right_diagnostics,
            "left_vertical_slowdown_proxy_timestamp_ms": (
                int(timestamps[left_slowdown])
                if left_slowdown is not None
                else None
            ),
            "right_vertical_slowdown_proxy_timestamp_ms": (
                int(timestamps[right_slowdown])
                if right_slowdown is not None
                else None
            ),
            "post_slowdown_start_timestamp_ms": (
                int(timestamps[start]) if start is not None else None
            ),
            "timing_source": "timestamp_ms",
        },
    }


def bilateral_foot_vertical_slowdown_from_position_series(
    timestamp_ms: np.ndarray,
    left_foot_position: np.ndarray,
    right_foot_position: np.ndarray,
    indexes: np.ndarray,
    body_scale: float,
) -> tuple[
    int | None,
    np.ndarray,
    np.ndarray,
    tuple[int, ...],
    dict[str, Any],
]:
    """Return the bilateral vertical-slowdown timing offset Pose proxy."""

    sample_count = (
        int(np.asarray(timestamp_ms).size)
        if np.asarray(timestamp_ms).ndim == 1
        else 0
    )
    unavailable = np.full(sample_count, np.nan, dtype=np.float64)
    components = _bilateral_vertical_slowdown_components(
        timestamp_ms,
        left_foot_position,
        right_foot_position,
        indexes,
        body_scale,
    )
    if components is None:
        return None, unavailable, unavailable.copy(), (), {
            "reason": "invalid_or_misaligned_bilateral_foot_position_series"
        }
    left = components["left_slowdown"]
    right = components["right_slowdown"]
    value = (
        abs(
            int(components["timestamps"][left])
            - int(components["timestamps"][right])
        )
        if left is not None and right is not None
        else None
    )
    evidence = tuple(item for item in (left, right) if item is not None)
    return (
        value,
        components["left_vertical_velocity_body_s"],
        components["right_vertical_velocity_body_s"],
        evidence,
        components["diagnostics"],
    )


def post_slowdown_stance_width_from_position_series(
    timestamp_ms: np.ndarray,
    left_foot_position: np.ndarray,
    right_foot_position: np.ndarray,
    indexes: np.ndarray,
    body_scale: float,
) -> tuple[float | None, np.ndarray, tuple[int, ...], dict[str, Any]]:
    """Return median foot-reference width after both slowdown proxies."""

    sample_count = (
        int(np.asarray(timestamp_ms).size)
        if np.asarray(timestamp_ms).ndim == 1
        else 0
    )
    unavailable = np.full(sample_count, np.nan, dtype=np.float64)
    components = _bilateral_vertical_slowdown_components(
        timestamp_ms,
        left_foot_position,
        right_foot_position,
        indexes,
        body_scale,
    )
    if components is None:
        return None, unavailable, (), {
            "reason": "invalid_or_misaligned_bilateral_foot_position_series"
        }
    stance_width = np.linalg.norm(
        components["right_foot_position"] - components["left_foot_position"],
        axis=1,
    ) / float(components["body_scale"])
    post_indexes = components["post_indexes"]
    finite = post_indexes[np.isfinite(stance_width[post_indexes])]
    value = float(np.median(stance_width[finite])) if finite.size else None
    evidence = (
        (int(finite[np.argmin(np.abs(stance_width[finite] - value))]),)
        if value is not None
        else ()
    )
    return value, stance_width, evidence, {
        **components["diagnostics"],
        "valid_post_samples": int(finite.size),
    }


def hip_center_lateral_variability_from_position_series(
    timestamp_ms: np.ndarray,
    left_foot_position: np.ndarray,
    right_foot_position: np.ndarray,
    hip_position: np.ndarray,
    indexes: np.ndarray,
    body_scale: float,
) -> tuple[
    float | None,
    np.ndarray,
    tuple[int, ...],
    dict[str, Any],
    str,
]:
    """Return post-slowdown hip-x variability with explicit right censoring."""

    sample_count = (
        int(np.asarray(timestamp_ms).size)
        if np.asarray(timestamp_ms).ndim == 1
        else 0
    )
    unavailable = np.full(sample_count, np.nan, dtype=np.float64)
    parsed = _validated_event_vector_series(
        timestamp_ms,
        indexes,
        {
            "left_foot_position": left_foot_position,
            "right_foot_position": right_foot_position,
            "hip_position": hip_position,
        },
    )
    if parsed is None:
        return (
            None,
            unavailable,
            (),
            {"reason": "invalid_or_misaligned_foot_hip_position_series"},
            "bilateral_vertical_slowdown_proxy_unavailable",
        )
    timestamps, selected, series, _ = parsed
    components = _bilateral_vertical_slowdown_components(
        timestamps,
        series["left_foot_position"],
        series["right_foot_position"],
        selected,
        body_scale,
    )
    if components is None:
        return (
            None,
            unavailable,
            (),
            {"reason": "invalid_or_misaligned_foot_hip_position_series"},
            "bilateral_vertical_slowdown_proxy_unavailable",
        )
    scale = float(components["body_scale"])
    values = series["hip_position"][:, 0] / scale
    post_indexes = components["post_indexes"]
    finite_post = post_indexes[np.isfinite(values[post_indexes])]
    measurement_indexes = finite_post
    boundary_censored_pre_index: int | None = None
    boundary_censored_gap_ms: int | None = None
    slowdown_start = (
        max(components["left_slowdown"], components["right_slowdown"])
        if components["left_slowdown"] is not None
        and components["right_slowdown"] is not None
        else None
    )
    if slowdown_start is not None and finite_post.size == 1:
        preceding = selected[
            (selected < int(finite_post[0])) & np.isfinite(values[selected])
        ]
        if preceding.size:
            candidate = int(preceding[-1])
            gap_ms = int(timestamps[int(finite_post[0])] - timestamps[candidate])
            if 0 < gap_ms <= FS01_FS02_MAX_TEMPORAL_GAP_MS:
                boundary_censored_pre_index = candidate
                boundary_censored_gap_ms = gap_ms
                measurement_indexes = np.asarray(
                    [candidate, int(finite_post[0])], dtype=np.int64
                )
    value = (
        float(np.std(values[measurement_indexes], ddof=0))
        if measurement_indexes.size >= 2
        else None
    )
    evidence = (
        tuple(int(item) for item in measurement_indexes)
        if value is not None and boundary_censored_pre_index is not None
        else (
            int(
                measurement_indexes[
                    np.argmax(
                        np.abs(
                            values[measurement_indexes]
                            - np.median(values[measurement_indexes])
                        )
                    )
                ]
            ),
        )
        if value is not None
        else tuple(
            dict.fromkeys(
                int(item)
                for item in (
                    components["left_slowdown"],
                    components["right_slowdown"],
                    *(int(item) for item in finite_post),
                )
                if item is not None
            )
        )
    )
    reason = (
        "valid_pose_kinematic_proxy"
        if value is not None
        else "bilateral_vertical_slowdown_proxy_unavailable"
        if slowdown_start is None
        else "post_slowdown_window_insufficient"
    )
    return value, values, evidence, {
        **components["diagnostics"],
        "variability_statistic": "population_standard_deviation",
        "coordinate_status": "image_horizontal_fixed_camera_only",
        "valid_post_samples": int(finite_post.size),
        "minimum_post_samples": 2,
        "measurement_sample_count": int(measurement_indexes.size),
        "post_slowdown_window_status": (
            "fully_observed_post_slowdown_window"
            if finite_post.size >= 2
            else "boundary_censored_two_sample_window"
            if boundary_censored_pre_index is not None
            else "insufficient_post_slowdown_window"
        ),
        "boundary_censored_pre_sample_timestamp_ms": (
            int(timestamps[boundary_censored_pre_index])
            if boundary_censored_pre_index is not None
            else None
        ),
        "boundary_censored_pre_sample_gap_ms": boundary_censored_gap_ms,
        "boundary_censored_max_gap_ms": FS01_FS02_MAX_TEMPORAL_GAP_MS,
        "boundary_censored_semantics": (
            "nearest_pre_anchor_in_event_sample_only_not_full_post_landing_stability"
            if boundary_censored_pre_index is not None
            else None
        ),
    }, reason


def _speed_slowdown_index(
    speeds: np.ndarray,
    indexes: np.ndarray,
) -> tuple[int | None, float | None, dict[str, Any]]:
    finite = indexes[np.isfinite(speeds[indexes])]
    if finite.size < 3:
        return None, None, {"valid_samples": int(finite.size)}
    peak_position = int(np.argmax(speeds[finite]))
    peak_index = int(finite[peak_position])
    tail = finite[peak_position:]
    tail_values = speeds[tail]
    envelope = float(np.percentile(tail_values, 35.0))
    candidates = tail[tail_values <= envelope]
    selected = int(candidates[0]) if candidates.size else int(tail[-1])
    late_count = max(1, int(math.ceil(tail.size * 0.25)))
    late_median = float(np.median(tail_values[-late_count:]))
    return selected, float(speeds[peak_index] - late_median), {
        "valid_samples": int(finite.size),
        "peak_index": peak_index,
        "peak_speed": float(speeds[peak_index]),
        "post_peak_late_median_speed": late_median,
        "speed_p35_envelope": envelope,
        "envelope_status": "event_local_kinematic_slowdown_not_landing",
    }


def _event_series(
    sequence: PoseSequence,
) -> tuple[dict[str, np.ndarray], dict[str, np.ndarray], dict[str, Any]]:
    cache_key = id(sequence)
    cached = _EVENT_SERIES_CACHE.get(cache_key)
    if cached is not None and cached[0] is sequence:
        return cached[1], cached[2], cached[3]
    left_foot = foot_reference_series(sequence, "left")
    right_foot = foot_reference_series(sequence, "right")
    hips, hip_valid = hip_center(sequence)
    left_flexion, left_knee_valid = _knee_flexion(sequence, "left")
    right_flexion, right_knee_valid = _knee_flexion(sequence, "right")
    raw_positions = {
        "hip_position": _mask_rows(hips, hip_valid),
        "left_foot_position": _mask_rows(left_foot.values, left_foot.valid),
        "right_foot_position": _mask_rows(right_foot.values, right_foot.valid),
    }
    smooth_positions = {
        name: smooth_series(sequence.timestamp_ms, values)
        for name, values in raw_positions.items()
    }
    raw: dict[str, np.ndarray] = {**raw_positions}
    smoothed: dict[str, np.ndarray] = {**smooth_positions}
    for output, source in (
        ("hip_velocity", "hip_position"),
        ("left_foot_velocity", "left_foot_position"),
        ("right_foot_velocity", "right_foot_position"),
    ):
        raw[output] = irregular_derivative(sequence.timestamp_ms, raw[source])
        smoothed[output] = irregular_derivative(
            sequence.timestamp_ms, smoothed[source]
        )
    raw["hip_acceleration"] = irregular_derivative(
        sequence.timestamp_ms, raw["hip_velocity"]
    )
    smoothed["hip_acceleration"] = irregular_derivative(
        sequence.timestamp_ms, smoothed["hip_velocity"]
    )
    raw["left_foot_speed"] = np.linalg.norm(raw["left_foot_velocity"], axis=1)
    raw["right_foot_speed"] = np.linalg.norm(raw["right_foot_velocity"], axis=1)
    smoothed["left_foot_speed"] = np.linalg.norm(
        smoothed["left_foot_velocity"], axis=1
    )
    smoothed["right_foot_speed"] = np.linalg.norm(
        smoothed["right_foot_velocity"], axis=1
    )
    raw["left_knee_flexion"] = left_flexion
    raw["right_knee_flexion"] = right_flexion
    smoothed["left_knee_flexion"] = smooth_series(
        sequence.timestamp_ms, left_flexion
    )
    smoothed["right_knee_flexion"] = smooth_series(
        sequence.timestamp_ms, right_flexion
    )
    raw["left_knee_extension_velocity"] = -irregular_derivative(
        sequence.timestamp_ms, left_flexion[:, None]
    )[:, 0]
    raw["right_knee_extension_velocity"] = -irregular_derivative(
        sequence.timestamp_ms, right_flexion[:, None]
    )[:, 0]
    smoothed["left_knee_extension_velocity"] = -irregular_derivative(
        sequence.timestamp_ms, smoothed["left_knee_flexion"][:, None]
    )[:, 0]
    smoothed["right_knee_extension_velocity"] = -irregular_derivative(
        sequence.timestamp_ms, smoothed["right_knee_flexion"][:, None]
    )[:, 0]
    raw["stance_width"] = np.linalg.norm(
        raw["right_foot_position"] - raw["left_foot_position"], axis=1
    )
    smoothed["stance_width"] = np.linalg.norm(
        smoothed["right_foot_position"] - smoothed["left_foot_position"], axis=1
    )
    raw_valid = {
        "hip": hip_valid,
        "left_foot": left_foot.valid,
        "right_foot": right_foot.valid,
        "both_feet": left_foot.valid & right_foot.valid,
        "left_knee": left_knee_valid,
        "right_knee": right_knee_valid,
        "all_knees_feet": (
            left_knee_valid
            & right_knee_valid
            & left_foot.valid
            & right_foot.valid
        ),
    }
    modes = {
        "left": left_foot.mode,
        "right": right_foot.mode,
        "confidence_multiplier": min(
            left_foot.confidence_multiplier,
            right_foot.confidence_multiplier,
        ),
        "left_required_joints": list(left_foot.required_joints),
        "right_required_joints": list(right_foot.required_joints),
        "left_fine_mask": left_foot.fine_mask,
        "right_fine_mask": right_foot.fine_mask,
        "left_ankle_fallback_mask": left_foot.ankle_fallback_mask,
        "right_ankle_fallback_mask": right_foot.ankle_fallback_mask,
        "left_unaligned_ankle_fallback_mask": (
            left_foot.unaligned_ankle_fallback_mask
        ),
        "right_unaligned_ankle_fallback_mask": (
            right_foot.unaligned_ankle_fallback_mask
        ),
        "left_missing_mask": left_foot.missing_mask,
        "right_missing_mask": right_foot.missing_mask,
        "left_fine_topology_available": left_foot.fine_topology_available,
        "right_fine_topology_available": right_foot.fine_topology_available,
        "left_alignment_method": left_foot.alignment_method,
        "right_alignment_method": right_foot.alignment_method,
        "left_alignment_overlap_count": left_foot.alignment_overlap_count,
        "right_alignment_overlap_count": right_foot.alignment_overlap_count,
        "left_fine_minus_ankle_offset_xy": (
            left_foot.fine_minus_ankle_offset_xy
        ),
        "right_fine_minus_ankle_offset_xy": (
            right_foot.fine_minus_ankle_offset_xy
        ),
        "raw_valid": raw_valid,
    }
    _EVENT_SERIES_CACHE[cache_key] = (sequence, raw, smoothed, modes)
    return raw, smoothed, modes


def clear_fs01_fs02_feature_cache(sequence: PoseSequence | None = None) -> None:
    """Release cached full-sequence kinematics without changing feature output."""

    if sequence is None:
        _EVENT_SERIES_CACHE.clear()
        return
    for key, cached in list(_EVENT_SERIES_CACHE.items()):
        if cached[0] is sequence:
            _EVENT_SERIES_CACHE.pop(key, None)


def _event_foot_reference_provenance(
    modes: dict[str, Any],
    indexes: np.ndarray,
    sides: tuple[str, ...],
) -> tuple[dict[str, Any], tuple[str, ...], float]:
    """Summarize actual per-frame reference sources inside one event."""

    event_indexes = np.asarray(indexes, dtype=np.int64)
    side_payload: dict[str, Any] = {}
    required: list[str] = []
    multipliers: list[float] = []
    for side in sides:
        fine_count = int(modes[f"{side}_fine_mask"][event_indexes].sum())
        fallback_count = int(
            modes[f"{side}_ankle_fallback_mask"][event_indexes].sum()
        )
        unaligned_count = int(
            modes[f"{side}_unaligned_ankle_fallback_mask"][event_indexes].sum()
        )
        missing_count = int(modes[f"{side}_missing_mask"][event_indexes].sum())
        valid_count = fine_count + fallback_count
        reference_candidate_count = valid_count + unaligned_count
        multiplier = (
            (
                fine_count
                + ANKLE_FALLBACK_CONFIDENCE_MULTIPLIER * fallback_count
            )
            / reference_candidate_count
            if reference_candidate_count
            else 0.0
        )
        if fine_count:
            required.extend(
                (f"{side}_heel", f"{side}_big_toe", f"{side}_small_toe")
            )
        if fallback_count or unaligned_count:
            required.append(f"{side}_ankle")
        if fine_count and fallback_count:
            event_mode = "mixed_fine_foot_with_per_frame_ankle_fallback"
        elif fine_count and unaligned_count:
            event_mode = "fine_foot_with_unaligned_ankle_fallback_unusable"
        elif fine_count:
            event_mode = "fine_foot_centroid_heel_big_toe_small_toe"
        elif modes[f"{side}_fine_topology_available"] and unaligned_count:
            event_mode = "fine_foot_ankle_fallback_unaligned_unusable"
        elif modes[f"{side}_fine_topology_available"]:
            event_mode = "fine_foot_topology_no_valid_reference"
        else:
            event_mode = "coco17_ankle_fallback"
        side_payload[side] = {
            "mode": event_mode,
            "fine_topology_available": bool(
                modes[f"{side}_fine_topology_available"]
            ),
            "event_frame_count": int(event_indexes.size),
            "fine_foot_frame_count": fine_count,
            "ankle_fallback_frame_count": fallback_count,
            "unaligned_ankle_fallback_frame_count": unaligned_count,
            "missing_frame_count": missing_count,
            "fine_foot_ratio_of_valid": (
                round(fine_count / valid_count, 8) if valid_count else 0.0
            ),
            "ankle_fallback_ratio_of_valid": (
                round(fallback_count / valid_count, 8) if valid_count else 0.0
            ),
            "ankle_fallback_ratio_of_event": (
                round(fallback_count / event_indexes.size, 8)
                if event_indexes.size
                else 0.0
            ),
            "unaligned_ankle_fallback_ratio_of_event": (
                round(unaligned_count / event_indexes.size, 8)
                if event_indexes.size
                else 0.0
            ),
            "alignment": {
                "method": modes[f"{side}_alignment_method"],
                "overlap_frame_count_full_sequence": int(
                    modes[f"{side}_alignment_overlap_count"]
                ),
                "fine_centroid_minus_ankle_offset_xy": (
                    list(modes[f"{side}_fine_minus_ankle_offset_xy"])
                    if modes[f"{side}_fine_minus_ankle_offset_xy"] is not None
                    else None
                ),
                "unaligned_fallback_unusable_for_derivatives": bool(
                    unaligned_count
                ),
            },
            "confidence_multiplier": round(float(multiplier), 8),
        }
        multipliers.append(float(multiplier))
    aggregate_multiplier = min(multipliers) if multipliers else 1.0
    aggregate = {
        "sides": list(sides),
        "confidence_multiplier_policy": (
            "minimum_side_weighted_mean_1.0_fine_0.65_aligned_ankle_0.0_unaligned_ankle"
        ),
        "confidence_multiplier": round(aggregate_multiplier, 8),
        "ankle_fallback_is_not_missing": True,
        "ankle_fallback_is_not_contact_evidence": True,
        "unaligned_fallback_is_excluded_from_kinematic_series": True,
    }
    return (
        {"sides": side_payload, "aggregate": aggregate},
        tuple(dict.fromkeys(required)),
        aggregate_multiplier,
    )


def _summary(
    *,
    value: float | int | None,
    valid_mask: np.ndarray,
    evidence: tuple[int, ...],
    raw: dict[str, np.ndarray],
    smoothed: dict[str, np.ndarray],
    diagnostics: dict[str, Any],
    required_joints: tuple[str, ...],
    confidence_multiplier: float = 1.0,
    reason: str | None = None,
) -> FS01FS02EventSummary:
    return FS01FS02EventSummary(
        value=value,
        valid_mask=valid_mask,
        evidence_indexes=tuple(sorted(set(evidence))),
        raw_series=raw,
        smoothed_series=smoothed,
        diagnostics=diagnostics,
        required_joints=required_joints,
        confidence_multiplier=confidence_multiplier,
        reason=(
            reason
            if reason is not None
            else "valid_pose_kinematic_proxy"
            if value is not None
            else "required_kinematic_proxy_unavailable"
        ),
    )


def _side_selection(
    left_value: float | None,
    right_value: float | None,
) -> tuple[int | None, str | None, str]:
    if left_value is None or right_value is None:
        return None, None, "bilateral_values_unavailable"
    if left_value <= 0.0 and right_value <= 0.0:
        return 0, None, "no_positive_bilateral_signal"
    if left_value > right_value:
        return -1, "left", "left_has_larger_positive_signal"
    if right_value > left_value:
        return 1, "right", "right_has_larger_positive_signal"
    return 0, None, "equal_positive_bilateral_signal"


def drive_measurement_candidate_side(
    left_peak_extension: float | None,
    right_peak_extension: float | None,
) -> tuple[str | None, str]:
    """Choose a side for retaining observed drive-related evidence.

    Positive knee extension remains the primary drive-side proxy.  If both
    sides are fully observed but neither extends, the less-negative (stronger)
    peak is still useful evidence that the named behavior was weak or absent.
    This helper therefore selects that side for numeric measurement while the
    public ``drive_side_code`` remains 0/indeterminate.  Missing or exactly
    tied bilateral evidence remains unavailable.
    """

    code, side, reason = _side_selection(
        left_peak_extension, right_peak_extension
    )
    if side is not None:
        return side, reason
    if (
        code == 0
        and left_peak_extension is not None
        and right_peak_extension is not None
        and left_peak_extension != right_peak_extension
    ):
        if left_peak_extension > right_peak_extension:
            return "left", "no_positive_extension_left_is_less_negative"
        return "right", "no_positive_extension_right_is_less_negative"
    return None, reason


def drive_side_from_knee_extension_peaks(
    left_peak_extension: float | None,
    right_peak_extension: float | None,
) -> tuple[int | None, str | None, str]:
    """Public pure-function contract for the FS02 drive-side diagnostic.

    The returned code/side is a 2D kinematic proxy only.  Keeping this helper
    public lets the error-budget evaluator replay exactly the same semantics
    from serialized raw evidence instead of maintaining a second copy.
    """

    return _side_selection(left_peak_extension, right_peak_extension)


def _event_phase_anchor_index(
    timestamp_ms: np.ndarray,
    indexes: np.ndarray,
    key_phases: dict[str, int | None] | None,
    phase_name: str,
) -> tuple[int | None, dict[str, Any], bool]:
    """Map an event phase timestamp to the nearest in-event source sample.

    Detector phases are normally exact frame timestamps.  Manual boundaries may
    be recorded between frames, so the same 160 ms temporal-gap contract used by
    smoothing is the maximum allowed nearest-sample alignment.  A declared but
    unalignable phase fails closed; only legacy callers that omit the phase may
    use the pre-v0.4 signal-derived fallback.
    """

    phase_declared = bool(
        isinstance(key_phases, dict)
        and phase_name in key_phases
        and key_phases[phase_name] is not None
    )
    if not phase_declared:
        return (
            None,
            {
                "post_step_anchor_source": "computed_signal_slowdown_fallback",
                "event_phase_name": phase_name,
                "event_phase_timestamp_ms": None,
                "matched_source_timestamp_ms": None,
                "phase_alignment_error_ms": None,
                "maximum_phase_alignment_error_ms": FS01_FS02_MAX_TEMPORAL_GAP_MS,
                "phase_anchor_status": "event_phase_not_provided_legacy_fallback",
            },
            False,
        )
    phase_value = key_phases[phase_name] if key_phases is not None else None
    if (
        isinstance(phase_value, bool)
        or not isinstance(phase_value, (int, np.integer))
        or indexes.size == 0
    ):
        return (
            None,
            {
                "post_step_anchor_source": "event_key_phase",
                "event_phase_name": phase_name,
                "event_phase_timestamp_ms": (
                    int(phase_value)
                    if isinstance(phase_value, (int, np.integer))
                    and not isinstance(phase_value, bool)
                    else None
                ),
                "matched_source_timestamp_ms": None,
                "phase_alignment_error_ms": None,
                "maximum_phase_alignment_error_ms": FS01_FS02_MAX_TEMPORAL_GAP_MS,
                "phase_anchor_status": "event_phase_invalid_or_event_empty",
            },
            True,
        )
    event_timestamps = np.asarray(timestamp_ms, dtype=np.int64)[indexes]
    nearest_position = int(np.argmin(np.abs(event_timestamps - int(phase_value))))
    nearest_index = int(indexes[nearest_position])
    matched_timestamp = int(timestamp_ms[nearest_index])
    alignment_error = abs(matched_timestamp - int(phase_value))
    aligned = alignment_error <= FS01_FS02_MAX_TEMPORAL_GAP_MS
    return (
        nearest_index if aligned else None,
        {
            "post_step_anchor_source": "event_key_phase",
            "event_phase_name": phase_name,
            "event_phase_timestamp_ms": int(phase_value),
            "matched_source_timestamp_ms": matched_timestamp,
            "phase_alignment_error_ms": alignment_error,
            "maximum_phase_alignment_error_ms": FS01_FS02_MAX_TEMPORAL_GAP_MS,
            "phase_anchor_status": (
                "matched_event_phase_to_source_sample"
                if aligned
                else "event_phase_outside_temporal_alignment_contract"
            ),
        },
        True,
    )


def summarize_fs01_fs02_event_feature(
    feature_name: str,
    sequence: PoseSequence,
    indexes: np.ndarray,
    body_scale: float,
    key_phases: dict[str, int | None] | None = None,
) -> FS01FS02EventSummary:
    """Compute an event-local, non-scoring FS01/FS02 Pose-only proxy."""

    if feature_name not in FS01_FS02_EVENT_FEATURE_NAMES:
        raise ValueError(f"unknown FS01/FS02 event feature: {feature_name}")
    indexes = np.asarray(indexes, dtype=np.int64)
    scale = _finite_positive_scale(body_scale)
    raw, smoothed, modes = _event_series(sequence)
    validity = modes["raw_valid"]
    (
        foot_reference_provenance,
        bilateral_foot_joints,
        foot_multiplier,
    ) = _event_foot_reference_provenance(
        modes, indexes, ("left", "right")
    )
    hip_joints = ("left_hip", "right_hip")
    side_counts = foot_reference_provenance["sides"]
    fallback_count = sum(
        value["ankle_fallback_frame_count"] for value in side_counts.values()
    )
    unaligned_fallback_count = sum(
        value["unaligned_ankle_fallback_frame_count"]
        for value in side_counts.values()
    )
    fine_count = sum(
        value["fine_foot_frame_count"] for value in side_counts.values()
    )
    foot_diagnostics = {
        "foot_reference_mode": {
            side: value["mode"] for side, value in side_counts.items()
        },
        "foot_reference_provenance": foot_reference_provenance,
        "topology_confidence_multiplier": foot_multiplier,
        "fallback_status": (
            "fine_foot_only"
            if fallback_count == 0 and fine_count > 0
            else "mixed_per_frame_ankle_fallback_lower_confidence_not_contact"
            if fallback_count > 0 and fine_count > 0
            else "ankle_fallback_only_lower_confidence_not_contact"
            if fallback_count > 0
            else "unaligned_ankle_fallback_excluded_from_kinematics"
            if unaligned_fallback_count > 0
            else "foot_reference_unavailable"
        ),
        "measurement_status": (
            "pose_kinematic_proxy_not_contact_force_or_calibrated_grade"
        ),
    }
    # These prepared velocity series are also reused by the FS02 drive-to-foot
    # phase proxy below.  The FS01 synchrony branch calls the public pure
    # function, but keeping this shared preparation avoids changing any FS02
    # value semantics while the evidence contract is upgraded.
    left_up_velocity = -smoothed["left_foot_velocity"][:, 1] / scale
    right_up_velocity = -smoothed["right_foot_velocity"][:, 1] / scale
    left_up_index, _ = _peak_index(left_up_velocity, indexes)
    right_up_index, _ = _peak_index(right_up_velocity, indexes)

    if feature_name == "bilateral_foot_rise_min_body":
        (
            value,
            left_rise_body,
            right_rise_body,
            evidence,
            rise_diagnostics,
        ) = bilateral_foot_rise_from_position_series(
            sequence.timestamp_ms,
            smoothed["left_foot_position"],
            smoothed["right_foot_position"],
            indexes,
            scale,
        )
        return _summary(
            value=value,
            valid_mask=validity["both_feet"],
            evidence=evidence,
            raw={
                "left_foot_position": raw["left_foot_position"],
                "right_foot_position": raw["right_foot_position"],
            },
            smoothed={
                "left_foot_position": smoothed["left_foot_position"],
                "right_foot_position": smoothed["right_foot_position"],
                "left_upward_excursion_body": left_rise_body,
                "right_upward_excursion_body": right_rise_body,
            },
            diagnostics={
                **foot_diagnostics,
                **rise_diagnostics,
            },
            required_joints=bilateral_foot_joints,
            confidence_multiplier=foot_multiplier,
        )

    if feature_name == "bilateral_foot_rise_synchrony_ms":
        (
            value,
            left_up_velocity,
            right_up_velocity,
            evidence,
            synchrony_diagnostics,
        ) = bilateral_foot_rise_synchrony_from_velocity_series(
            sequence.timestamp_ms,
            smoothed["left_foot_velocity"],
            smoothed["right_foot_velocity"],
            indexes,
            scale,
        )
        return _summary(
            value=value,
            valid_mask=validity["both_feet"],
            evidence=evidence,
            raw={
                "left_foot_velocity": raw["left_foot_velocity"],
                "right_foot_velocity": raw["right_foot_velocity"],
            },
            smoothed={
                "left_foot_velocity": smoothed["left_foot_velocity"],
                "right_foot_velocity": smoothed["right_foot_velocity"],
                "left_upward_velocity_body_s": left_up_velocity,
                "right_upward_velocity_body_s": right_up_velocity,
            },
            diagnostics={
                **foot_diagnostics,
                **synchrony_diagnostics,
            },
            required_joints=bilateral_foot_joints,
            confidence_multiplier=foot_multiplier,
        )

    if feature_name == "bilateral_foot_rise_proxy_duration_ms":
        (
            duration,
            left_rise_body,
            right_rise_body,
            mask_payload,
            evidence,
            duration_diagnostics,
        ) = bilateral_foot_rise_duration_from_position_series(
            sequence.timestamp_ms,
            smoothed["left_foot_position"],
            smoothed["right_foot_position"],
            indexes,
            scale,
            validity["both_feet"],
        )
        return _summary(
            value=duration,
            valid_mask=validity["both_feet"],
            evidence=evidence,
            raw={
                "left_foot_position": raw["left_foot_position"],
                "right_foot_position": raw["right_foot_position"],
            },
            smoothed={
                "left_foot_position": smoothed["left_foot_position"],
                "right_foot_position": smoothed["right_foot_position"],
                "left_upward_excursion_body": left_rise_body,
                "right_upward_excursion_body": right_rise_body,
                "simultaneous_rise_proxy_mask": mask_payload,
            },
            diagnostics={
                **foot_diagnostics,
                **duration_diagnostics,
            },
            required_joints=bilateral_foot_joints,
            confidence_multiplier=foot_multiplier,
            reason=str(duration_diagnostics["reason"]),
        )

    if feature_name == "hip_center_vertical_velocity_body_s":
        value, hip_up_velocity, evidence, hip_diagnostics = (
            hip_center_vertical_velocity_from_position_series(
                sequence.timestamp_ms,
                smoothed["hip_position"],
                indexes,
                scale,
            )
        )
        return _summary(
            value=value,
            valid_mask=validity["hip"],
            evidence=evidence,
            raw={"hip_position": raw["hip_position"]},
            smoothed={
                "hip_position": smoothed["hip_position"],
                "hip_image_up_velocity_body_s": hip_up_velocity,
            },
            diagnostics=hip_diagnostics,
            required_joints=hip_joints,
        )

    if feature_name == "bilateral_foot_vertical_slowdown_time_offset_ms":
        (
            value,
            left_vertical_velocity,
            right_vertical_velocity,
            evidence,
            slowdown_diagnostics,
        ) = bilateral_foot_vertical_slowdown_from_position_series(
            sequence.timestamp_ms,
            smoothed["left_foot_position"],
            smoothed["right_foot_position"],
            indexes,
            scale,
        )
        return _summary(
            value=value,
            valid_mask=validity["both_feet"],
            evidence=evidence,
            raw={
                "left_foot_position": raw["left_foot_position"],
                "right_foot_position": raw["right_foot_position"],
            },
            smoothed={
                "left_foot_position": smoothed["left_foot_position"],
                "right_foot_position": smoothed["right_foot_position"],
                "left_vertical_velocity_body_s": left_vertical_velocity,
                "right_vertical_velocity_body_s": right_vertical_velocity,
            },
            diagnostics={
                **foot_diagnostics,
                **slowdown_diagnostics,
            },
            required_joints=bilateral_foot_joints,
            confidence_multiplier=foot_multiplier,
        )

    if feature_name == "post_slowdown_stance_width_body":
        value, stance_width, evidence, stance_diagnostics = (
            post_slowdown_stance_width_from_position_series(
                sequence.timestamp_ms,
                smoothed["left_foot_position"],
                smoothed["right_foot_position"],
                indexes,
                scale,
            )
        )
        return _summary(
            value=value,
            valid_mask=validity["both_feet"],
            evidence=evidence,
            raw={
                "left_foot_position": raw["left_foot_position"],
                "right_foot_position": raw["right_foot_position"],
            },
            smoothed={
                "left_foot_position": smoothed["left_foot_position"],
                "right_foot_position": smoothed["right_foot_position"],
                "stance_width_body": stance_width,
            },
            diagnostics={
                **foot_diagnostics,
                **stance_diagnostics,
            },
            required_joints=bilateral_foot_joints,
            confidence_multiplier=foot_multiplier,
        )

    if feature_name == "hip_center_lateral_variability_body":
        value, values, evidence, variability_diagnostics, reason = (
            hip_center_lateral_variability_from_position_series(
                sequence.timestamp_ms,
                smoothed["left_foot_position"],
                smoothed["right_foot_position"],
                smoothed["hip_position"],
                indexes,
                scale,
            )
        )
        return _summary(
            value=value,
            valid_mask=validity["hip"] & validity["both_feet"],
            evidence=evidence,
            raw={
                "left_foot_position": raw["left_foot_position"],
                "right_foot_position": raw["right_foot_position"],
                "hip_position": raw["hip_position"],
            },
            smoothed={
                "left_foot_position": smoothed["left_foot_position"],
                "right_foot_position": smoothed["right_foot_position"],
                "hip_position": smoothed["hip_position"],
                "hip_center_x_body": values,
            },
            diagnostics={
                **foot_diagnostics,
                **variability_diagnostics,
            },
            required_joints=hip_joints + bilateral_foot_joints,
            confidence_multiplier=foot_multiplier,
            reason=reason,
        )

    stance_width = smoothed["stance_width"] / scale
    direction_unit, direction_deg, direction_evidence, direction_diag = (
        launch_direction_from_hip_motion(
            sequence.timestamp_ms, smoothed["hip_position"], indexes
        )
    )
    if feature_name == "launch_direction_deg":
        return _summary(
            value=direction_deg,
            valid_mask=validity["hip"],
            evidence=direction_evidence,
            raw={"hip_position": raw["hip_position"]},
            smoothed={"hip_position": smoothed["hip_position"]},
            diagnostics=direction_diag,
            required_joints=hip_joints,
        )

    left_extension = smoothed["left_knee_extension_velocity"]
    right_extension = smoothed["right_knee_extension_velocity"]
    left_extension_index, left_extension_peak = _peak_index(left_extension, indexes)
    right_extension_index, right_extension_peak = _peak_index(right_extension, indexes)
    drive_code, drive_side, drive_reason = drive_side_from_knee_extension_peaks(
        left_extension_peak, right_extension_peak
    )
    drive_measurement_side, drive_measurement_reason = (
        drive_measurement_candidate_side(
            left_extension_peak, right_extension_peak
        )
    )
    knee_joints = (
        "left_hip",
        "left_knee",
        "left_ankle",
        "right_hip",
        "right_knee",
        "right_ankle",
    )
    drive_diagnostics = {
        "code_map": {"-1": "left", "0": "indeterminate", "1": "right"},
        "selected_side": drive_side,
        "selection_reason": drive_reason,
        "measurement_candidate_side": drive_measurement_side,
        "measurement_candidate_reason": drive_measurement_reason,
        "nonpositive_candidate_does_not_assert_support_or_force": True,
        "left_peak_knee_extension_velocity_deg_s": left_extension_peak,
        "right_peak_knee_extension_velocity_deg_s": right_extension_peak,
        "proxy_status": "2d_knee_kinematics_not_support_force_or_load",
    }
    if feature_name == "drive_side_code":
        return _summary(
            value=drive_code,
            valid_mask=validity["left_knee"] & validity["right_knee"],
            evidence=tuple(
                item
                for item in (left_extension_index, right_extension_index)
                if item is not None
            ),
            raw={
                "left_knee_flexion_deg": raw["left_knee_flexion"],
                "right_knee_flexion_deg": raw["right_knee_flexion"],
            },
            smoothed={
                "left_knee_extension_velocity_deg_s": left_extension,
                "right_knee_extension_velocity_deg_s": right_extension,
            },
            diagnostics=drive_diagnostics,
            required_joints=knee_joints,
            reason=(
                "valid_kinematic_drive_side_proxy_not_force"
                if drive_code is not None
                else drive_reason
            ),
        )

    if feature_name == "support_knee_extension_velocity_deg_s":
        value = (
            left_extension_peak
            if drive_measurement_side == "left"
            else right_extension_peak
            if drive_measurement_side == "right"
            else None
        )
        evidence = (
            (left_extension_index,)
            if drive_measurement_side == "left" and left_extension_index is not None
            else (right_extension_index,)
            if drive_measurement_side == "right" and right_extension_index is not None
            else ()
        )
        return _summary(
            value=value,
            valid_mask=validity["left_knee"] & validity["right_knee"],
            evidence=evidence,
            raw={
                "left_knee_flexion_deg": raw["left_knee_flexion"],
                "right_knee_flexion_deg": raw["right_knee_flexion"],
            },
            smoothed={
                "left_knee_extension_velocity_deg_s": left_extension,
                "right_knee_extension_velocity_deg_s": right_extension,
            },
            diagnostics=drive_diagnostics,
            required_joints=knee_joints,
            reason=(
                "valid_selected_knee_extension_proxy_not_force"
                if value is not None and drive_side is not None
                else "valid_observed_nonpositive_knee_extension_evidence_not_force"
                if value is not None
                else "drive_side_indeterminate"
            ),
        )

    if feature_name == "hip_acceleration_along_launch_direction_body_s2":
        value, projection, evidence, acceleration_diagnostics = (
            hip_acceleration_along_launch_direction_from_series(
                sequence.timestamp_ms,
                smoothed["hip_position"],
                smoothed["hip_acceleration"] / scale,
                indexes,
            )
        )
        return _summary(
            value=value,
            valid_mask=validity["hip"],
            evidence=evidence,
            raw={
                "hip_position": raw["hip_position"],
                "hip_acceleration_xy_body_s2": raw["hip_acceleration"] / scale,
            },
            smoothed={
                "hip_position": smoothed["hip_position"],
                "hip_acceleration_xy_body_s2": (
                    smoothed["hip_acceleration"] / scale
                ),
                "hip_acceleration_along_launch_direction_body_s2": projection,
            },
            diagnostics={
                **acceleration_diagnostics,
                "evidence_contract": (
                    "raw_and_smoothed_hip_position_acceleration_series_v1"
                ),
            },
            required_joints=hip_joints,
            reason=(
                "valid_hip_acceleration_projection_proxy_not_force"
                if value is not None
                else "launch_direction_or_hip_acceleration_unavailable"
            ),
        )

    if feature_name == "support_drive_to_moving_foot_rise_proxy_ms":
        drive_index = (
            left_extension_index
            if drive_measurement_side == "left"
            else right_extension_index
            if drive_measurement_side == "right"
            else None
        )
        moving_index = (
            right_up_index
            if drive_measurement_side == "left"
            else left_up_index
            if drive_measurement_side == "right"
            else None
        )
        value = (
            int(sequence.timestamp_ms[moving_index] - sequence.timestamp_ms[drive_index])
            if drive_index is not None and moving_index is not None
            else None
        )
        return _summary(
            value=value,
            valid_mask=validity["all_knees_feet"],
            evidence=tuple(
                item for item in (drive_index, moving_index) if item is not None
            ),
            raw={
                "left_knee_extension_velocity_deg_s": raw["left_knee_extension_velocity"],
                "right_knee_extension_velocity_deg_s": raw["right_knee_extension_velocity"],
                "left_foot_upward_velocity_body_s": -raw["left_foot_velocity"][:, 1] / scale,
                "right_foot_upward_velocity_body_s": -raw["right_foot_velocity"][:, 1] / scale,
            },
            smoothed={
                "left_knee_extension_velocity_deg_s": left_extension,
                "right_knee_extension_velocity_deg_s": right_extension,
                "left_foot_upward_velocity_body_s": left_up_velocity,
                "right_foot_upward_velocity_body_s": right_up_velocity,
            },
            diagnostics={
                **foot_diagnostics,
                **drive_diagnostics,
                "sign_convention": "moving_foot_peak_up_time_minus_drive_knee_extension_time",
            },
            required_joints=knee_joints + bilateral_foot_joints,
            confidence_multiplier=foot_multiplier,
            reason=(
                "valid_pose_phase_proxy_not_force_takeoff_or_contact"
                if value is not None and drive_side is not None
                else "valid_nonpositive_drive_candidate_to_foot_rise_timing_not_force_or_contact"
                if value is not None
                else "drive_side_or_moving_foot_rise_proxy_unavailable"
            ),
        )

    launch_components = launch_foot_event_kinematics_from_series(
        sequence.timestamp_ms,
        smoothed["hip_position"],
        smoothed["left_foot_position"],
        smoothed["right_foot_position"],
        smoothed["left_foot_velocity"],
        smoothed["right_foot_velocity"],
        indexes,
        scale,
    )
    launch_code = launch_components["launch_code"]
    launch_side = launch_components["selected_side"]
    left_projected = launch_components["left_projected_displacement_body"]
    right_projected = launch_components["right_projected_displacement_body"]
    left_disp_evidence = launch_components["left_displacement_evidence"]
    right_disp_evidence = launch_components["right_displacement_evidence"]
    launch_component_diagnostics = launch_components["diagnostics"]
    launch_diagnostics = {
        **foot_diagnostics,
        "code_map": launch_component_diagnostics["code_map"],
        "selected_side": launch_side,
        "selection_reason": launch_component_diagnostics["selection_reason"],
        "left_projected_displacement_body": left_projected,
        "right_projected_displacement_body": right_projected,
        "left_displacement_summary": launch_component_diagnostics[
            "left_displacement_summary"
        ],
        "right_displacement_summary": launch_component_diagnostics[
            "right_displacement_summary"
        ],
        "launch_direction_deg": launch_component_diagnostics[
            "launch_direction_deg"
        ],
        "direction_source": launch_component_diagnostics["direction_source"],
        "proxy_status": launch_component_diagnostics["proxy_status"],
    }
    if feature_name == "launch_side_code":
        return _summary(
            value=launch_code,
            valid_mask=validity["hip"] & validity["both_feet"],
            evidence=launch_components["launch_side_evidence"],
            raw={
                "hip_position": raw["hip_position"],
                "left_foot_position": raw["left_foot_position"],
                "right_foot_position": raw["right_foot_position"],
                "left_foot_velocity": raw["left_foot_velocity"],
                "right_foot_velocity": raw["right_foot_velocity"],
            },
            smoothed={
                "hip_position": smoothed["hip_position"],
                "left_foot_position": smoothed["left_foot_position"],
                "right_foot_position": smoothed["right_foot_position"],
                "left_foot_velocity": smoothed["left_foot_velocity"],
                "right_foot_velocity": smoothed["right_foot_velocity"],
            },
            diagnostics=launch_diagnostics,
            required_joints=hip_joints + bilateral_foot_joints,
            confidence_multiplier=foot_multiplier,
            reason=(
                "valid_kinematic_launch_side_proxy_not_takeoff"
                if launch_code is not None
                else launch_components["diagnostics"]["selection_reason"]
            ),
        )

    selected_speed = launch_components["selected_speed_body_s"]
    selected_foot_valid = (
        validity[f"{launch_side}_foot"]
        if launch_side is not None
        else validity["both_feet"]
    )
    selected_foot_joints = (
        _event_foot_reference_provenance(
            modes, indexes, (launch_side,)
        )[1]
        if launch_side is not None
        else bilateral_foot_joints
    )
    speed_index = launch_components["speed_peak_index"]
    speed_peak = launch_components["speed_peak_body_s"]
    if feature_name == "launch_foot_speed_peak_body_s":
        return _summary(
            value=speed_peak,
            valid_mask=selected_foot_valid & validity["hip"],
            evidence=(speed_index,) if speed_index is not None else (),
            raw={
                "hip_position": raw["hip_position"],
                "left_foot_position": raw["left_foot_position"],
                "right_foot_position": raw["right_foot_position"],
                "left_foot_velocity": raw["left_foot_velocity"],
                "right_foot_velocity": raw["right_foot_velocity"],
            },
            smoothed={
                "hip_position": smoothed["hip_position"],
                "left_foot_position": smoothed["left_foot_position"],
                "right_foot_position": smoothed["right_foot_position"],
                "left_foot_velocity": smoothed["left_foot_velocity"],
                "right_foot_velocity": smoothed["right_foot_velocity"],
                "launch_foot_speed_body_s": selected_speed,
            },
            diagnostics=launch_diagnostics,
            required_joints=hip_joints + selected_foot_joints,
            confidence_multiplier=foot_multiplier,
            reason=(
                "valid_selected_foot_speed_proxy"
                if speed_peak is not None
                else "launch_side_or_foot_speed_unavailable"
            ),
        )

    if feature_name == "launch_foot_relative_displacement_body":
        value = launch_components["relative_displacement_body"]
        return _summary(
            value=value,
            valid_mask=validity["hip"] & validity["both_feet"],
            evidence=launch_components["relative_displacement_evidence"],
            raw={
                "hip_position": raw["hip_position"],
                "left_foot_position": raw["left_foot_position"],
                "right_foot_position": raw["right_foot_position"],
                "left_foot_velocity": raw["left_foot_velocity"],
                "right_foot_velocity": raw["right_foot_velocity"],
            },
            smoothed={
                "hip_position": smoothed["hip_position"],
                "left_foot_position": smoothed["left_foot_position"],
                "right_foot_position": smoothed["right_foot_position"],
                "left_foot_velocity": smoothed["left_foot_velocity"],
                "right_foot_velocity": smoothed["right_foot_velocity"],
            },
            diagnostics=launch_diagnostics,
            required_joints=hip_joints + bilateral_foot_joints,
            confidence_multiplier=foot_multiplier,
            reason=(
                "valid_relative_foot_displacement_proxy"
                if value is not None
                else "launch_side_or_relative_displacement_unavailable"
            ),
        )

    projected_velocity = launch_components["projected_velocity_body_s"]
    projected_peak = launch_components["projected_velocity_peak_body_s"]
    if feature_name == "launch_foot_motion_duration_ms":
        mask = launch_components["motion_proxy_mask"]
        duration = launch_components["motion_duration_ms"]
        evidence = launch_components["motion_duration_evidence"]
        payload = mask.astype(np.float64)
        payload[~selected_foot_valid] = np.nan
        return _summary(
            value=duration,
            valid_mask=selected_foot_valid & validity["hip"],
            evidence=evidence,
            raw={
                "hip_position": raw["hip_position"],
                "left_foot_position": raw["left_foot_position"],
                "right_foot_position": raw["right_foot_position"],
                "left_foot_velocity": raw["left_foot_velocity"],
                "right_foot_velocity": raw["right_foot_velocity"],
            },
            smoothed={
                "hip_position": smoothed["hip_position"],
                "left_foot_position": smoothed["left_foot_position"],
                "right_foot_position": smoothed["right_foot_position"],
                "left_foot_velocity": smoothed["left_foot_velocity"],
                "right_foot_velocity": smoothed["right_foot_velocity"],
                "launch_foot_projected_velocity_body_s": projected_velocity,
                "launch_foot_motion_proxy_mask": payload,
            },
            diagnostics={
                **launch_diagnostics,
                "half_peak_projected_velocity_envelope_body_s": (
                    projected_peak * 0.5 if projected_peak is not None else None
                ),
                "envelope_status": "event_local_half_peak_motion_not_airborne",
            },
            required_joints=hip_joints + selected_foot_joints,
            confidence_multiplier=foot_multiplier,
            reason=(
                "valid_motion_duration_proxy_not_airborne"
                if duration is not None
                else "positive_launch_foot_motion_proxy_unavailable"
            ),
        )

    first_step_components = first_step_phase_kinematics_from_series(
        sequence.timestamp_ms,
        smoothed["hip_position"],
        smoothed["hip_velocity"],
        smoothed["left_foot_position"],
        smoothed["right_foot_position"],
        smoothed["left_foot_velocity"],
        smoothed["right_foot_velocity"],
        indexes,
        scale,
        key_phases,
    )
    slowdown_index = first_step_components["speed_slowdown_index"]
    speed_drop = first_step_components["speed_drop_body_s"]
    slowdown_diag = first_step_components["slowdown_diagnostics"]
    post_step_anchor_index = first_step_components["post_step_anchor_index"]
    post_step_anchor_diagnostics = first_step_components[
        "post_step_anchor_diagnostics"
    ]
    first_step_raw_series = {
        "hip_position": raw["hip_position"],
        "hip_velocity": raw["hip_velocity"],
        "left_foot_position": raw["left_foot_position"],
        "right_foot_position": raw["right_foot_position"],
        "left_foot_velocity": raw["left_foot_velocity"],
        "right_foot_velocity": raw["right_foot_velocity"],
    }
    first_step_smoothed_series = {
        "hip_position": smoothed["hip_position"],
        "hip_velocity": smoothed["hip_velocity"],
        "left_foot_position": smoothed["left_foot_position"],
        "right_foot_position": smoothed["right_foot_position"],
        "left_foot_velocity": smoothed["left_foot_velocity"],
        "right_foot_velocity": smoothed["right_foot_velocity"],
    }
    if feature_name == "launch_foot_speed_drop_body_s":
        return _summary(
            value=speed_drop,
            valid_mask=selected_foot_valid & validity["hip"],
            evidence=first_step_components["speed_drop_evidence"],
            raw=first_step_raw_series,
            smoothed=first_step_smoothed_series,
            diagnostics={
                **launch_diagnostics,
                **post_step_anchor_diagnostics,
            },
            required_joints=hip_joints + selected_foot_joints,
            confidence_multiplier=foot_multiplier,
            reason=(
                "valid_foot_speed_drop_proxy_not_landing"
                if speed_drop is not None
                else "launch_side_or_speed_slowdown_unavailable"
            ),
        )

    if feature_name == "first_step_displacement_body":
        value = first_step_components["first_step_displacement_body"]
        return _summary(
            value=value,
            valid_mask=selected_foot_valid & validity["hip"],
            evidence=first_step_components["first_step_displacement_evidence"],
            raw=first_step_raw_series,
            smoothed=first_step_smoothed_series,
            diagnostics={
                **launch_diagnostics,
                **post_step_anchor_diagnostics,
            },
            required_joints=hip_joints + selected_foot_joints,
            confidence_multiplier=foot_multiplier,
            reason=(
                "valid_projected_step_displacement_proxy_not_footfall"
                if value is not None
                else "launch_side_or_step_displacement_unavailable"
            ),
        )

    alignment = first_step_components["hip_launch_direction_cosine"]
    post_indexes = first_step_components["post_indexes"]
    if feature_name == "post_step_hip_direction_consistency":
        value = first_step_components["post_direction_consistency"]
        return _summary(
            value=value,
            valid_mask=validity["hip"] & selected_foot_valid,
            evidence=first_step_components[
                "post_direction_consistency_evidence"
            ],
            raw=first_step_raw_series,
            smoothed=first_step_smoothed_series,
            diagnostics={
                **launch_diagnostics,
                **post_step_anchor_diagnostics,
                "valid_post_samples": int(
                    first_step_components["diagnostics"][
                        "valid_post_direction_samples"
                    ]
                ),
                "range": [-1.0, 1.0],
            },
            required_joints=hip_joints + selected_foot_joints,
            confidence_multiplier=foot_multiplier,
        )

    if feature_name == "launch_foot_slowdown_to_post_hip_direction_ms":
        envelope = first_step_components["post_direction_alignment_envelope"]
        value = first_step_components["slowdown_to_post_direction_ms"]
        return _summary(
            value=value,
            valid_mask=validity["hip"] & selected_foot_valid,
            evidence=first_step_components[
                "slowdown_to_post_direction_evidence"
            ],
            raw=first_step_raw_series,
            smoothed=first_step_smoothed_series,
            diagnostics={
                **launch_diagnostics,
                **post_step_anchor_diagnostics,
                "post_slowdown_alignment_median_envelope": envelope,
                "envelope_status": "event_local_direction_envelope_not_balance_threshold",
            },
            required_joints=hip_joints + selected_foot_joints,
            confidence_multiplier=foot_multiplier,
            reason=(
                "valid_pose_phase_proxy_not_landing_or_balance"
                if value is not None
                else "slowdown_or_post_direction_proxy_unavailable"
            ),
        )

    if feature_name == "post_step_stance_width_body":
        value = first_step_components["post_stance_width_body"]
        return _summary(
            value=value,
            valid_mask=validity["hip"] & validity["both_feet"],
            evidence=first_step_components["post_stance_width_evidence"],
            raw=first_step_raw_series,
            smoothed=first_step_smoothed_series,
            diagnostics={
                **launch_diagnostics,
                **post_step_anchor_diagnostics,
                "post_step_start_timestamp_ms": (
                    int(sequence.timestamp_ms[post_step_anchor_index])
                    if post_step_anchor_index is not None
                    else None
                ),
                "valid_post_samples": int(
                    first_step_components["diagnostics"][
                        "valid_post_stance_samples"
                    ]
                ),
            },
            required_joints=hip_joints + bilateral_foot_joints,
            confidence_multiplier=foot_multiplier,
        )

    raise ValueError(f"unhandled FS01/FS02 event feature: {feature_name}")
