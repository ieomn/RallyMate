from __future__ import annotations

import hashlib
from typing import Any

import numpy as np

from rallymate_events.schemas import validate_event_record
from rallymate_features.coordinates import body_center, body_scale, hip_center, point_series
from rallymate_features.kinematics import irregular_derivative, scalar_acceleration, speed
from rallymate_features.schemas import PoseSequence
from rallymate_features.smoothing import smooth_series


EVENT_DETECTOR_VERSION = "pose-motion-bout-v0.4.1"
PHASE_CANDIDATE_VERSION = "pose-event-phase-proxies-v0.3.0"

PHASE_RIGHT_CENSORED_PEAK_PREFIX = "phase_proxy_right_censored_peak:"
PHASE_LOW_SAMPLE_PEAK_PREFIX = "phase_proxy_low_sample_peak:"
LEAD_FOOT_SIDE_AMBIGUOUS_FLAG = "lead_foot_side_proxy_ambiguous"

# Conservative event-candidate noise guards.  These are versioned detector
# parameters, not A--E grading thresholds.  Their job is to prevent adaptive
# percentiles from turning arbitrarily small, incoherent pose jitter into a
# motion bout.  Event-truth evaluation is still required to validate recall.
MOTION_BOUT_MIN_PERSISTENT_DISPLACEMENT_BODY = 0.10
MOTION_BOUT_MIN_PATH_EFFICIENCY = 0.20
MOTION_BOUT_MIN_DIRECTION_COHERENCE = 0.80
MOTION_BOUT_ENDPOINT_FRACTION = 0.20
EVENT_KINEMATIC_MIN_COVERAGE = 0.75
PHASE_RIGHT_BOUNDARY_CONFIRMATION_MAX_GAP_MS = 160

PHASE_DEFINITIONS = {
    "takeoff_proxy_ms": (
        "ankle image-plane vertical-activity onset proxy; not physical takeoff "
        "and not ground-contact evidence"
    ),
    "landing_proxy_ms": (
        "ankle image-plane vertical-activity slowdown proxy; not physical landing "
        "and not ground-contact evidence"
    ),
    "redistribution_ms": (
        "hip-center motion relative to the ankle-support midpoint; image-plane proxy"
    ),
    "initiation_ms": "aligned event-motion-reference adaptive bout onset candidate",
    "direction_conversion_ms": (
        "first reliable image-plane travel heading or strongest in-bout heading change"
    ),
    "support_extension_proxy_ms": (
        "hip-to-ankle image-plane span extension-rate proxy; not force production"
    ),
    "lead_foot_motion_onset_proxy_ms": (
        "higher-prominence ankle image-plane speed onset; lead-foot identity is a proxy"
    ),
    "first_step_slowdown_proxy_ms": (
        "first lead-ankle image-plane speed-decrease candidate; not foot contact"
    ),
    "peak_speed_ms": (
        "aligned event-motion-reference image-plane speed peak within the accepted motion bout; "
        "not a calibrated performance threshold"
    ),
    "deceleration_peak_ms": (
        "peak aligned event-motion-reference image-plane deceleration within the FS09 interval; "
        "not force or impulse"
    ),
    "restabilization_onset_ms": (
        "first post-peak event-motion-reference speed sample below the event-adaptive "
        "activity threshold; candidate only, not confirmed stable control"
    ),
    "stable_control_onset_ms": (
        "provisional alias of restabilization onset until manually labelled "
        "stable-control duration is available"
    ),
}


def _runs(mask: np.ndarray) -> list[tuple[int, int]]:
    values = np.asarray(mask, dtype=bool)
    result = []
    start = None
    for index, value in enumerate(np.append(values, False)):
        if value and start is None:
            start = index
        elif not value and start is not None:
            result.append((start, index - 1))
            start = None
    return result


def _close_short_gaps(
    mask: np.ndarray, timestamp_ms: np.ndarray, max_gap_ms: int
) -> np.ndarray:
    output = np.asarray(mask, dtype=bool).copy()
    false_runs = _runs(~output)
    for start, end in false_runs:
        if start == 0 or end == output.size - 1:
            continue
        gap_ms = int(timestamp_ms[end] - timestamp_ms[start])
        if gap_ms <= max_gap_ms:
            output[start : end + 1] = True
    return output


def _motion_bout_diagnostics(
    center_body: np.ndarray,
    start: int,
    end: int,
) -> dict[str, Any]:
    """Measure amplitude and temporal coherence of one candidate bout."""

    points = np.asarray(center_body[start : end + 1], dtype=np.float64)
    points = points[np.isfinite(points).all(axis=1)]
    diagnostics: dict[str, Any] = {
        "valid_center_sample_count": int(points.shape[0]),
        "minimum_persistent_displacement_body": (
            MOTION_BOUT_MIN_PERSISTENT_DISPLACEMENT_BODY
        ),
        "minimum_path_efficiency": MOTION_BOUT_MIN_PATH_EFFICIENCY,
        "minimum_direction_coherence_cosine": (
            MOTION_BOUT_MIN_DIRECTION_COHERENCE
        ),
        "endpoint_fraction": MOTION_BOUT_ENDPOINT_FRACTION,
        "semantics": (
            "event_candidate_noise_rejection_only_not_A_to_E_scoring_threshold"
        ),
    }
    if points.shape[0] < 4:
        diagnostics.update(
            {
                "status": "insufficient_center_samples",
                "candidate_accepted": False,
            }
        )
        return diagnostics

    endpoint_count = max(
        1, int(np.ceil(points.shape[0] * MOTION_BOUT_ENDPOINT_FRACTION))
    )
    first_center = np.median(points[:endpoint_count], axis=0)
    last_center = np.median(points[-endpoint_count:], axis=0)
    persistent_displacement = float(np.linalg.norm(last_center - first_center))

    vectors = np.diff(points, axis=0)
    step_lengths = np.linalg.norm(vectors, axis=1)
    path_length = float(np.sum(step_lengths))
    path_efficiency = (
        persistent_displacement / path_length if path_length > 1e-12 else 0.0
    )
    usable_vectors = vectors[step_lengths > 1e-12]
    if usable_vectors.shape[0] < 2:
        direction_coherence = None
    else:
        left = usable_vectors[:-1]
        right = usable_vectors[1:]
        denominator = np.linalg.norm(left, axis=1) * np.linalg.norm(right, axis=1)
        cosines = np.sum(left * right, axis=1) / denominator
        cosines = np.clip(cosines, -1.0, 1.0)
        direction_coherence = float(np.mean(cosines))

    accepted = (
        persistent_displacement
        >= MOTION_BOUT_MIN_PERSISTENT_DISPLACEMENT_BODY
        and path_efficiency >= MOTION_BOUT_MIN_PATH_EFFICIENCY
        and direction_coherence is not None
        and direction_coherence >= MOTION_BOUT_MIN_DIRECTION_COHERENCE
    )
    diagnostics.update(
        {
            "status": "candidate" if accepted else "motion_signal_incoherent",
            "candidate_accepted": accepted,
            "persistent_displacement_body": round(persistent_displacement, 8),
            "path_length_body": round(path_length, 8),
            "path_efficiency": round(path_efficiency, 8),
            "mean_adjacent_direction_cosine": (
                round(direction_coherence, 8)
                if direction_coherence is not None
                else None
            ),
        }
    )
    return diagnostics


def _event_id(source_id: str, bout_index: int, event_code: str) -> str:
    digest = hashlib.sha1(
        f"{source_id}|{bout_index}|{event_code}|{EVENT_DETECTOR_VERSION}".encode()
    ).hexdigest()[:12]
    return f"{event_code.lower()}-{bout_index:03d}-{digest}"


def _confidence(
    valid: np.ndarray,
    start: int,
    end: int,
    peak_speed: float,
    threshold: float,
) -> float:
    coverage = float(np.mean(valid[start : end + 1]))
    prominence = max(0.0, peak_speed - threshold) / max(threshold, 1e-6)
    return round(max(0.0, min(1.0, coverage * (0.6 + 0.4 * min(prominence, 1.0)))), 6)


def _event_kinematic_coverage(
    valid: np.ndarray,
    start: int,
    end: int,
) -> dict[str, Any]:
    """Summarise the detector signal coverage on one emitted event interval.

    FS01, FS02 and FS09 share one parent motion bout but do not share the same
    temporal evidence.  Coverage is therefore measured on the exact inclusive
    interval written to that event, so missing post-motion evidence cannot
    invalidate an otherwise complete FS02 (and vice versa).  The existing
    0.75 quality threshold is preserved; this is not an A--E score threshold.
    """

    values = np.asarray(valid, dtype=bool)
    if values.size == 0:
        raise ValueError("valid coverage series must be non-empty")
    effective_start = max(0, min(int(start), values.size - 1))
    effective_end = max(effective_start, min(int(end), values.size - 1))
    interval = values[effective_start : effective_end + 1]
    valid_count = int(np.count_nonzero(interval))
    sample_count = int(interval.size)
    fraction = valid_count / sample_count if sample_count else 0.0
    return {
        "scope": "emitted_event_interval_inclusive",
        "start_index": effective_start,
        "end_index": effective_end,
        "sample_count": sample_count,
        "valid_sample_count": valid_count,
        "coverage_fraction": round(float(fraction), 8),
        "minimum_required_fraction": EVENT_KINEMATIC_MIN_COVERAGE,
        "status": "sufficient" if fraction >= EVENT_KINEMATIC_MIN_COVERAGE else "low",
        "semantics": "event_detection_signal_quality_only_not_A_to_E_threshold",
    }


def _event_motion_reference(
    sequence: PoseSequence,
) -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray, dict[str, Any]]:
    """Build the event-candidate motion reference without synthesising poses.

    The preferred signal is the existing shoulder/hip body centre.  When both
    hips remain observed but one or both shoulders are missing, an aligned hip
    centre can preserve the translational motion evidence needed by the event
    candidate layer.  The alignment is a robust, full-sequence median offset
    measured only on frames where both references are directly observable.

    If a clip never contains a valid body centre, the directly observed hip
    centre may be used on its own.  This is deliberately limited to provisional
    event segmentation; it is not a biomechanical centre of mass, it does not
    repair feature keypoints, and it is never an A--E scoring threshold.
    """

    body_values, body_valid = body_center(sequence)
    hip_values, hip_valid = hip_center(sequence)
    body_source = body_valid & np.isfinite(body_values).all(axis=1)
    hip_source = hip_valid & np.isfinite(hip_values).all(axis=1)

    reference = np.full_like(body_values, np.nan, dtype=np.float64)
    reference[body_source] = body_values[body_source]
    hip_fallback = (~body_source) & hip_source
    overlap = body_source & hip_source
    offset: np.ndarray | None = None

    if body_source.any():
        # body_center validity already implies hip_center validity, so an
        # overlap normally exists.  Keep the explicit guard fail-closed in case
        # a future topology changes that invariant.
        if overlap.any():
            offset = np.median(body_values[overlap] - hip_values[overlap], axis=0)
            if np.isfinite(offset).all():
                reference[hip_fallback] = hip_values[hip_fallback] + offset
            else:
                hip_fallback[:] = False
                offset = None
        else:
            hip_fallback[:] = False
        mode = (
            "body_center_with_aligned_hip_center_fallback"
            if hip_fallback.any()
            else "body_center_only"
        )
        alignment_status = "estimated_from_direct_overlap" if offset is not None else "not_used"
    elif hip_source.any():
        reference[hip_source] = hip_values[hip_source]
        hip_fallback = hip_source.copy()
        mode = "direct_hip_center_only"
        alignment_status = "not_applicable_no_body_center_observation"
    else:
        hip_fallback[:] = False
        mode = "unavailable"
        alignment_status = "no_body_or_hip_center_observation"

    valid = np.isfinite(reference).all(axis=1) & (body_source | hip_fallback)
    diagnostics = {
        "mode": mode,
        "full_sequence_sample_count": int(sequence.timestamp_ms.size),
        "full_sequence_body_center_sample_count": int(np.count_nonzero(body_source)),
        "full_sequence_hip_center_fallback_sample_count": int(
            np.count_nonzero(hip_fallback)
        ),
        "full_sequence_missing_sample_count": int(np.count_nonzero(~valid)),
        "alignment": {
            "method": "robust_median_body_center_minus_hip_center_xy",
            "status": alignment_status,
            "direct_overlap_sample_count": int(np.count_nonzero(overlap)),
            "offset_xy": (
                [round(float(offset[0]), 8), round(float(offset[1]), 8)]
                if offset is not None
                else None
            ),
        },
        "semantics": (
            "event_candidate_motion_reference_only_not_biomechanical_center_of_mass_"
            "not_keypoint_imputation_and_not_A_to_E_scoring"
        ),
    }
    return reference, valid, body_source, hip_fallback, diagnostics


def _event_motion_reference_provenance(
    *,
    start: int,
    end: int,
    body_source: np.ndarray,
    hip_fallback: np.ndarray,
    diagnostics: dict[str, Any],
) -> dict[str, Any]:
    sample_count = int(end - start + 1)
    body_count = int(np.count_nonzero(body_source[start : end + 1]))
    fallback_count = int(np.count_nonzero(hip_fallback[start : end + 1]))
    return {
        "mode": diagnostics["mode"],
        "scope": "emitted_event_interval_inclusive",
        "start_index": int(start),
        "end_index": int(end),
        "sample_count": sample_count,
        "body_center_sample_count": body_count,
        "hip_center_fallback_sample_count": fallback_count,
        "missing_sample_count": sample_count - body_count - fallback_count,
        "alignment": diagnostics["alignment"],
        "full_sequence_sample_count": diagnostics["full_sequence_sample_count"],
        "full_sequence_body_center_sample_count": diagnostics[
            "full_sequence_body_center_sample_count"
        ],
        "full_sequence_hip_center_fallback_sample_count": diagnostics[
            "full_sequence_hip_center_fallback_sample_count"
        ],
        "full_sequence_missing_sample_count": diagnostics[
            "full_sequence_missing_sample_count"
        ],
        "semantics": diagnostics["semantics"],
    }


def _event_quality_flags(
    base_flags: list[str],
    kinematic_coverage: dict[str, Any],
) -> list[str]:
    flags = list(base_flags)
    if kinematic_coverage.get("status") == "low":
        flags.append("pose_kinematic_coverage_low")
    return flags


def _finite_row_max(*arrays: np.ndarray) -> np.ndarray:
    if not arrays:
        return np.empty(0, dtype=np.float64)
    stacked = np.column_stack([np.asarray(value, dtype=np.float64) for value in arrays])
    output = np.full(stacked.shape[0], np.nan, dtype=np.float64)
    finite = np.isfinite(stacked)
    usable = finite.any(axis=1)
    if usable.any():
        output[usable] = np.max(
            np.where(finite[usable], stacked[usable], -np.inf), axis=1
        )
    return output


def _adaptive_signal_threshold(
    signal: np.ndarray,
    indexes: np.ndarray,
    *,
    threshold_fraction: float = 0.35,
) -> tuple[float | None, dict[str, Any]]:
    values = np.asarray(signal, dtype=np.float64)[indexes]
    finite = values[np.isfinite(values)]
    metadata: dict[str, Any] = {
        "sample_count": int(finite.size),
        "threshold_fraction_of_event_prominence": threshold_fraction,
    }
    if finite.size < 3:
        metadata["status"] = "insufficient_samples"
        return None, metadata
    q20, q80 = np.percentile(finite, [20, 80])
    peak = float(np.max(finite))
    prominence = peak - float(q20)
    metadata.update(
        {
            "q20": round(float(q20), 8),
            "q80": round(float(q80), 8),
            "peak": round(peak, 8),
            "prominence": round(prominence, 8),
        }
    )
    numerical_tolerance = max(abs(peak), abs(float(q20)), 1.0) * 1e-9
    if not np.isfinite(prominence) or prominence <= numerical_tolerance:
        metadata["status"] = "no_signal_prominence"
        return None, metadata
    threshold = float(q20 + threshold_fraction * prominence)
    metadata.update({"status": "candidate", "threshold": round(threshold, 8)})
    return threshold, metadata


def _activity_onset_peak_and_slowdown(
    signal: np.ndarray,
    indexes: np.ndarray,
) -> tuple[int | None, int | None, int | None, dict[str, Any]]:
    threshold, metadata = _adaptive_signal_threshold(signal, indexes)
    if threshold is None:
        return None, None, None, metadata
    local_values = np.asarray(signal, dtype=np.float64)[indexes]
    active = np.isfinite(local_values) & (local_values >= threshold)
    active_runs = _runs(active)
    if not active_runs:
        metadata["status"] = "threshold_not_crossed"
        return None, None, None, metadata
    local_peak = int(np.nanargmax(local_values))
    selected_run = next(
        ((start, end) for start, end in active_runs if start <= local_peak <= end),
        max(active_runs, key=lambda run: float(np.nanmax(local_values[run[0] : run[1] + 1]))),
    )
    run_start, run_end = selected_run
    peak_local = run_start + int(
        np.nanargmax(local_values[run_start : run_end + 1])
    )
    slowdown_local = run_end + 1 if run_end + 1 < indexes.size else None
    metadata.update(
        {
            "active_run_start_ms_index": int(indexes[run_start]),
            "active_run_end_ms_index": int(indexes[run_end]),
        }
    )
    return (
        int(indexes[run_start]),
        int(indexes[peak_local]),
        int(indexes[slowdown_local]) if slowdown_local is not None else None,
        metadata,
    )


def _right_censored_peak_only_phase(
    *,
    candidate: int | None,
    peak: int | None,
    indexes: np.ndarray,
    metadata: dict[str, Any],
    phase_key: str,
) -> tuple[int | None, dict[str, Any], str | None]:
    """Use the observed peak only when the activity run hits the right edge.

    This is a censoring-bound fallback, not an inferred slowdown or contact
    time.  The returned flag is consumed by the quality policy to preserve F2
    measurement while preventing a production grade.
    """

    updated = dict(metadata)
    if (
        candidate is not None
        or peak is None
        or indexes.size == 0
        or updated.get("status") != "candidate"
        or updated.get("active_run_end_ms_index") != int(indexes[-1])
    ):
        return candidate, updated, None
    updated.update(
        {
            "status": "right_censored_peak_only_proxy",
            "fallback_anchor_ms_index": int(peak),
            "fallback_reason": "activity_run_reaches_event_right_boundary",
            "fallback_semantics": (
                "observed_activity_peak_only_not_slowdown_contact_or_landing"
            ),
        }
    )
    return (
        int(peak),
        updated,
        f"{PHASE_RIGHT_CENSORED_PEAK_PREFIX}{phase_key}",
    )


def _low_sample_peak_only_phase(
    *,
    candidate: int | None,
    signal: np.ndarray,
    indexes: np.ndarray,
    metadata: dict[str, Any],
    phase_key: str,
) -> tuple[int | None, dict[str, Any], str | None]:
    """Return an observed peak for a two-sample, threshold-ineligible tail."""

    updated = dict(metadata)
    if candidate is not None or updated.get("status") != "insufficient_samples":
        return candidate, updated, None
    finite_indexes = indexes[np.isfinite(np.asarray(signal)[indexes])]
    if finite_indexes.size < 2:
        return None, updated, None
    anchor = int(finite_indexes[np.argmax(np.asarray(signal)[finite_indexes])])
    updated.update(
        {
            "status": "low_sample_peak_only_proxy",
            "fallback_anchor_ms_index": anchor,
            "fallback_finite_sample_count": int(finite_indexes.size),
            "fallback_reason": "adaptive_envelope_requires_three_samples",
            "fallback_semantics": (
                "observed_deceleration_peak_only_not_contact_or_landing"
            ),
        }
    )
    return anchor, updated, f"{PHASE_LOW_SAMPLE_PEAK_PREFIX}{phase_key}"


def _right_boundary_speed_peak_confirmed_slowdown_phase(
    *,
    candidate: int | None,
    speed_signal: np.ndarray,
    timestamps: np.ndarray,
    peak: int | None,
    event_end_index: int,
    confirmation_end_index: int,
    metadata: dict[str, Any],
    phase_key: str,
) -> tuple[int | None, dict[str, Any], str | None]:
    """Confirm a right-censored slowdown onset without inventing contact.

    The phase timestamp remains the observed speed peak at the event boundary.
    Two immediately following, finite, monotonically decreasing speed samples
    are used only to confirm that the boundary peak really begins a slowdown.
    They are never copied into the event feature sequence. Timestamp gaps are
    checked directly, so this helper does not assume a fixed frame rate.
    """

    updated = dict(metadata)
    values = np.asarray(speed_signal, dtype=np.float64)
    times = np.asarray(timestamps, dtype=np.int64)
    if (
        candidate is not None
        or peak is None
        or peak != event_end_index
        or confirmation_end_index <= event_end_index
        or event_end_index < 0
        or confirmation_end_index >= values.size
        or values.size != times.size
    ):
        return candidate, updated, None

    confirmation_indexes = np.arange(
        event_end_index + 1,
        min(confirmation_end_index, event_end_index + 2) + 1,
        dtype=np.int64,
    )
    if confirmation_indexes.size != 2:
        return None, updated, None
    observed_indexes = np.concatenate(
        (np.asarray([event_end_index], dtype=np.int64), confirmation_indexes)
    )
    observed_speeds = values[observed_indexes]
    observed_times = times[observed_indexes]
    gaps = np.diff(observed_times)
    numerical_tolerance = max(abs(float(observed_speeds[0])), 1.0) * 1e-9
    confirmed = bool(
        np.isfinite(observed_speeds).all()
        and np.all(gaps > 0)
        and int(gaps.max()) <= PHASE_RIGHT_BOUNDARY_CONFIRMATION_MAX_GAP_MS
        and observed_speeds[0] > observed_speeds[1] + numerical_tolerance
        and observed_speeds[1] > observed_speeds[2] + numerical_tolerance
    )
    updated.update(
        {
            "right_boundary_confirmation_status": (
                "confirmed_monotonic_speed_decrease" if confirmed else "not_confirmed"
            ),
            "right_boundary_confirmation_indexes": [
                int(index) for index in confirmation_indexes
            ],
            "right_boundary_confirmation_timestamps_ms": [
                int(value) for value in observed_times
            ],
            "right_boundary_confirmation_speeds_body_s": [
                round(float(value), 8) if np.isfinite(value) else None
                for value in observed_speeds
            ],
            "right_boundary_confirmation_max_gap_ms": (
                int(gaps.max()) if gaps.size else None
            ),
            "right_boundary_confirmation_allowed_max_gap_ms": (
                PHASE_RIGHT_BOUNDARY_CONFIRMATION_MAX_GAP_MS
            ),
            "right_boundary_confirmation_semantics": (
                "post_event_samples_confirm_speed_decrease_only_not_part_of_event_"
                "features_not_contact_or_landing"
            ),
        }
    )
    if not confirmed:
        return None, updated, None
    updated.update(
        {
            "status": "right_censored_speed_peak_confirmed_by_post_event_samples",
            "fallback_anchor_ms_index": int(peak),
            "fallback_reason": (
                "event_boundary_speed_peak_followed_by_two_observed_decreases"
            ),
            "fallback_semantics": (
                "observed_speed_peak_as_slowdown_onset_proxy_not_contact_or_landing"
            ),
        }
    )
    return (
        int(peak),
        updated,
        f"{PHASE_RIGHT_CENSORED_PEAK_PREFIX}{phase_key}",
    )


def _adaptive_peak_candidate(
    signal: np.ndarray,
    indexes: np.ndarray,
) -> tuple[int | None, dict[str, Any]]:
    threshold, metadata = _adaptive_signal_threshold(signal, indexes)
    if threshold is None:
        return None, metadata
    local = np.asarray(signal, dtype=np.float64)[indexes]
    eligible = np.flatnonzero(np.isfinite(local) & (local >= threshold))
    if eligible.size == 0:
        metadata["status"] = "threshold_not_crossed"
        return None, metadata
    local_index = int(eligible[np.nanargmax(local[eligible])])
    return int(indexes[local_index]), metadata


def _missing_phase_flags(phases: dict[str, int | None]) -> list[str]:
    return [
        f"{name[:-3] if name.endswith('_ms') else name}_not_observed"
        for name, index in phases.items()
        if index is None
    ]


def _phase_signals(
    sequence: PoseSequence,
    *,
    scale: float,
) -> dict[str, np.ndarray]:
    timestamps = sequence.timestamp_ms
    hips, hip_valid = hip_center(sequence)
    left_ankle, left_valid = point_series(sequence, "left_ankle")
    right_ankle, right_valid = point_series(sequence, "right_ankle")
    left_hip, left_hip_valid = point_series(sequence, "left_hip")
    right_hip, right_hip_valid = point_series(sequence, "right_hip")

    left_ankle_smoothed = smooth_series(
        timestamps, left_ankle, max_gap_ms=200, radius_ms=80
    )
    right_ankle_smoothed = smooth_series(
        timestamps, right_ankle, max_gap_ms=200, radius_ms=80
    )
    hips_smoothed = smooth_series(
        timestamps, hips, max_gap_ms=200, radius_ms=80
    )
    left_hip_smoothed = smooth_series(
        timestamps, left_hip, max_gap_ms=200, radius_ms=80
    )
    right_hip_smoothed = smooth_series(
        timestamps, right_hip, max_gap_ms=200, radius_ms=80
    )
    left_ankle_relative = (left_ankle_smoothed - hips_smoothed) / scale
    right_ankle_relative = (right_ankle_smoothed - hips_smoothed) / scale
    left_ankle_relative[~(left_valid & hip_valid)] = np.nan
    right_ankle_relative[~(right_valid & hip_valid)] = np.nan
    left_ankle_speed = speed(timestamps, left_ankle_relative)
    right_ankle_speed = speed(timestamps, right_ankle_relative)
    left_ankle_velocity = irregular_derivative(timestamps, left_ankle_smoothed) / scale
    right_ankle_velocity = irregular_derivative(timestamps, right_ankle_smoothed) / scale
    foot_upward_activity = _finite_row_max(
        np.maximum(-left_ankle_velocity[:, 1], 0.0),
        np.maximum(-right_ankle_velocity[:, 1], 0.0),
    )
    foot_downward_activity = _finite_row_max(
        np.maximum(left_ankle_velocity[:, 1], 0.0),
        np.maximum(right_ankle_velocity[:, 1], 0.0),
    )

    support_valid = left_valid & right_valid & hip_valid
    support_axis = right_ankle_smoothed - left_ankle_smoothed
    support_axis_squared = np.sum(support_axis * support_axis, axis=1)
    support_projection = np.full(timestamps.size, np.nan, dtype=np.float64)
    projection_valid = support_valid & (support_axis_squared > 1e-9)
    support_projection[projection_valid] = np.sum(
        (hips_smoothed[projection_valid] - left_ankle_smoothed[projection_valid])
        * support_axis[projection_valid],
        axis=1,
    ) / support_axis_squared[projection_valid]
    support_projection = smooth_series(
        timestamps, support_projection, max_gap_ms=200, radius_ms=80
    )
    redistribution_velocity = irregular_derivative(
        timestamps, support_projection[:, None]
    )[:, 0]
    redistribution_speed = np.abs(redistribution_velocity)

    def extension_rate(
        hip: np.ndarray,
        ankle: np.ndarray,
        valid_mask: np.ndarray,
    ) -> np.ndarray:
        length = np.linalg.norm(ankle - hip, axis=1) / scale
        length[~valid_mask] = np.nan
        length = smooth_series(timestamps, length, max_gap_ms=200, radius_ms=80)
        rate = irregular_derivative(timestamps, length[:, None])[:, 0]
        return np.where(np.isfinite(rate), np.maximum(rate, 0.0), np.nan)

    left_extension_rate = extension_rate(
        left_hip_smoothed, left_ankle_smoothed, left_hip_valid & left_valid
    )
    right_extension_rate = extension_rate(
        right_hip_smoothed, right_ankle_smoothed, right_hip_valid & right_valid
    )
    return {
        "foot_upward_activity_body_s": foot_upward_activity,
        "foot_downward_activity_body_s": foot_downward_activity,
        "hip_support_redistribution_speed_body_s": redistribution_speed,
        "left_ankle_speed_body_s": left_ankle_speed,
        "right_ankle_speed_body_s": right_ankle_speed,
        "left_support_extension_rate_body_s": left_extension_rate,
        "right_support_extension_rate_body_s": right_extension_rate,
    }


def _make_event(
    *,
    source_id: str,
    video_id: str | None,
    bout_index: int,
    event_code: str,
    start: int,
    end: int,
    timestamps: np.ndarray,
    phases: dict[str, int | None],
    confidence: float,
    boundary_uncertainty_ms: int,
    flags: list[str],
    thresholds: dict[str, float],
    kinematic_coverage: dict[str, Any],
    motion_reference: dict[str, Any],
    phase_metadata: dict[str, Any] | None = None,
) -> dict[str, Any]:
    if end <= start:
        end = min(start + 1, len(timestamps) - 1)
    record = {
        "schema_version": "1.0.0",
        "event_id": _event_id(source_id, bout_index, event_code),
        "person_track_id": 1,
        "event_code": event_code,
        "start_ms": int(timestamps[start]),
        "end_ms": int(timestamps[end]),
        "key_phases_ms": {
            name: int(timestamps[index]) if index is not None else None
            for name, index in phases.items()
        },
        "confidence": confidence,
        "boundary_uncertainty_ms": int(boundary_uncertainty_ms),
        "quality_flags": sorted(set(flags)),
        "provenance": {
            "detector_version": EVENT_DETECTOR_VERSION,
            "source_id": source_id,
            "pose_topology": "coco17_compatible_body_joints",
            "primary_player_id": 1,
            "rule_family": "event_adaptive_image_plane_motion_bout",
            "adaptive_thresholds": thresholds,
            "event_kinematic_coverage": kinematic_coverage,
            "event_motion_reference": motion_reference,
            "threshold_semantics": "event_candidate_segmentation_only_not_A_to_E_scoring",
            "ground_truth_status": "ground_truth_required",
            "phase_candidate_version": PHASE_CANDIDATE_VERSION,
            "phase_candidate_semantics": (
                "timestamp_ms_based_image_plane_pose_proxies_not_ground_contact_or_force"
            ),
            "phase_candidates": phase_metadata or {},
        },
    }
    if video_id is not None:
        record["video_id"] = video_id
    validate_event_record(record)
    return record


def detect_pose_events(
    sequence: PoseSequence,
    *,
    source_id: str,
    video_id: str | None = None,
) -> list[dict[str, Any]]:
    if not isinstance(source_id, str) or not source_id:
        raise ValueError("source_id must be a non-empty run/source identifier")
    if video_id is not None and (not isinstance(video_id, str) or not video_id):
        raise ValueError("video_id must be null or a non-empty canonical identity")
    timestamps = sequence.timestamp_ms
    if timestamps.size < 8:
        return []
    (
        center,
        center_valid,
        motion_body_source,
        motion_hip_fallback,
        motion_reference_diagnostics,
    ) = _event_motion_reference(sequence)
    scale_values, scale_valid = body_scale(sequence)
    scale_candidates = scale_values[np.isfinite(scale_values) & (scale_values > 1e-6)]
    if scale_candidates.size == 0:
        return []
    scale = float(np.median(scale_candidates))
    center_smoothed = smooth_series(
        timestamps, center, max_gap_ms=200, radius_ms=120
    )
    motion_speed = speed(timestamps, center_smoothed) / scale
    motion_speed = smooth_series(
        timestamps, motion_speed, max_gap_ms=200, radius_ms=120
    )
    valid = np.isfinite(motion_speed) & center_valid & scale_valid
    speed_values = motion_speed[valid]
    if speed_values.size < 8:
        return []
    q20, q80 = np.percentile(speed_values, [20, 80])
    active_threshold = float(q20 + 0.45 * max(q80 - q20, 1e-6))
    active = valid & (motion_speed >= active_threshold)
    active = _close_short_gaps(active, timestamps, max_gap_ms=240)
    candidate_bouts = [
        (start, end)
        for start, end in _runs(active)
        if int(timestamps[end] - timestamps[start]) >= 240
    ]
    # A missing motion bout is negative evidence, not a reason to manufacture
    # one around the numerically largest (possibly zero) sample.  The former
    # unconditional peak fallback emitted FS01/FS02/FS09 for a perfectly
    # static pose sequence.  Candidate recall must be evaluated against manual
    # event truth; it is safer and auditable to return no candidate here than
    # to force an event into every clip.
    if not candidate_bouts:
        return []
    center_body = center_smoothed / scale
    bouts: list[tuple[int, int, dict[str, Any]]] = []
    for start, end in candidate_bouts:
        diagnostics = _motion_bout_diagnostics(center_body, start, end)
        if diagnostics["candidate_accepted"]:
            bouts.append((start, end, diagnostics))
    if not bouts:
        return []
    acceleration = scalar_acceleration(timestamps, motion_speed)
    hips, hip_valid = hip_center(sequence)
    phase_signals = _phase_signals(sequence, scale=scale)
    center_velocity = irregular_derivative(timestamps, center_smoothed) / scale
    heading_change_deg = np.full(timestamps.size, np.nan, dtype=np.float64)
    for index in range(1, timestamps.size):
        previous = center_velocity[index - 1]
        current = center_velocity[index]
        if not np.isfinite(previous).all() or not np.isfinite(current).all():
            continue
        previous_norm = float(np.linalg.norm(previous))
        current_norm = float(np.linalg.norm(current))
        if previous_norm < active_threshold or current_norm < active_threshold:
            continue
        cross = float(previous[0] * current[1] - previous[1] * current[0])
        dot = float(np.dot(previous, current))
        heading_change_deg[index] = abs(float(np.degrees(np.arctan2(cross, dot))))
    median_dt = int(np.median(np.diff(timestamps)))
    uncertainty = max(2 * median_dt, 40)
    events = []
    for bout_index, (onset, active_end, bout_diagnostics) in enumerate(bouts):
        next_onset = bouts[bout_index + 1][0] if bout_index + 1 < len(bouts) else len(timestamps) - 1
        previous_end = bouts[bout_index - 1][1] if bout_index else 0
        pre_candidates = np.flatnonzero(
            (timestamps >= max(timestamps[previous_end], timestamps[onset] - 1000))
            & (timestamps <= timestamps[onset])
        )
        pre_start = int(pre_candidates[0]) if pre_candidates.size else max(0, onset - 1)
        if pre_start == onset:
            pre_start = max(0, onset - 1)
        active_slice = np.arange(onset, active_end + 1)
        peak = int(active_slice[np.nanargmax(motion_speed[active_slice])])
        post_end_time = min(
            int(timestamps[active_end] + 1200),
            int(timestamps[next_onset]),
            int(timestamps[-1]),
        )
        post_end = int(np.searchsorted(timestamps, post_end_time, side="right") - 1)
        post_end = max(post_end, min(len(timestamps) - 1, active_end + 1))
        fs09_start = min(peak, post_end - 1)
        pre_hip = hips[pre_start : onset + 1, 1]
        pre_valid = hip_valid[pre_start : onset + 1] & np.isfinite(pre_hip)
        preload = (
            pre_start + int(np.nanargmax(np.where(pre_valid, pre_hip, np.nan)))
            if pre_valid.any()
            else None
        )
        takeoff, _, _, takeoff_metadata = _activity_onset_peak_and_slowdown(
            phase_signals["foot_upward_activity_body_s"], pre_candidates
        )
        _, landing_peak, landing, landing_metadata = _activity_onset_peak_and_slowdown(
            phase_signals["foot_downward_activity_body_s"], pre_candidates
        )
        fs01_phase_flags: list[str] = []
        # The selected downward-activity run can be right-censored exactly at
        # the FS01 initiation boundary.  In that case the observed peak is a
        # useful event-local anchor, while the post-peak slowdown is genuinely
        # unobserved.  Preserve that distinction: expose the peak-only proxy,
        # bind an explicit scoring-block flag, and never call it ground contact.
        landing, landing_metadata, landing_fallback_flag = (
            _right_censored_peak_only_phase(
                candidate=landing,
                peak=landing_peak,
                indexes=pre_candidates,
                metadata=landing_metadata,
                phase_key="landing_proxy_ms",
            )
        )
        if landing_fallback_flag is not None:
            fs01_phase_flags.append(landing_fallback_flag)
        redistribution, _, _, redistribution_metadata = (
            _activity_onset_peak_and_slowdown(
                phase_signals["hip_support_redistribution_speed_body_s"],
                pre_candidates,
            )
        )
        initiation = onset if valid[onset] else None

        direction_conversion, direction_metadata = _adaptive_peak_candidate(
            heading_change_deg, active_slice
        )
        if direction_conversion is None:
            reliable_heading = active_slice[
                np.isfinite(center_velocity[active_slice]).all(axis=1)
                & np.isfinite(motion_speed[active_slice])
                & (motion_speed[active_slice] >= active_threshold)
            ]
            if reliable_heading.size:
                direction_conversion = int(reliable_heading[0])
                direction_metadata["status"] = "motion_onset_heading_fallback"

        support_extension_signal = _finite_row_max(
            phase_signals["left_support_extension_rate_body_s"],
            phase_signals["right_support_extension_rate_body_s"],
        )
        support_extension, support_extension_metadata = _adaptive_peak_candidate(
            support_extension_signal, active_slice
        )
        support_side_proxy = None
        if support_extension is not None:
            left_value = phase_signals["left_support_extension_rate_body_s"][
                support_extension
            ]
            right_value = phase_signals["right_support_extension_rate_body_s"][
                support_extension
            ]
            if np.isfinite(left_value) or np.isfinite(right_value):
                support_side_proxy = (
                    "left"
                    if np.nan_to_num(left_value, nan=-np.inf)
                    >= np.nan_to_num(right_value, nan=-np.inf)
                    else "right"
                )

        left_ankle_speed = phase_signals["left_ankle_speed_body_s"]
        right_ankle_speed = phase_signals["right_ankle_speed_body_s"]

        def local_peak_value(signal: np.ndarray) -> float | None:
            values = signal[active_slice]
            finite_values = values[np.isfinite(values)]
            return float(np.max(finite_values)) if finite_values.size else None

        left_peak = local_peak_value(left_ankle_speed)
        right_peak = local_peak_value(right_ankle_speed)
        lead_side_proxy = None
        lead_speed = None
        lead_side_ambiguous = False
        lead_side_min_speed = 0.02 * max(active_threshold, 1e-9)
        if left_peak is None and right_peak is not None and right_peak > lead_side_min_speed:
            lead_side_proxy = "right"
            lead_speed = right_ankle_speed
        elif right_peak is None and left_peak is not None and left_peak > lead_side_min_speed:
            lead_side_proxy = "left"
            lead_speed = left_ankle_speed
        elif left_peak is not None and right_peak is not None:
            dominant_peak = max(left_peak, right_peak)
            relative_difference = abs(left_peak - right_peak) / max(
                dominant_peak, 1e-9
            )
            if dominant_peak > lead_side_min_speed:
                if left_peak >= right_peak:
                    lead_side_proxy = "left"
                    lead_speed = left_ankle_speed
                else:
                    lead_side_proxy = "right"
                    lead_speed = right_ankle_speed
                lead_side_ambiguous = relative_difference < 0.05
        lead_motion_onset = None
        lead_motion_peak = None
        lead_motion_metadata: dict[str, Any] = {"status": "ankle_signal_missing"}
        first_step_slowdown = None
        slowdown_metadata: dict[str, Any] = {"status": "lead_motion_missing"}
        fs02_phase_flags: list[str] = []
        if lead_side_ambiguous:
            fs02_phase_flags.append(LEAD_FOOT_SIDE_AMBIGUOUS_FLAG)
        if lead_speed is not None:
            (
                lead_motion_onset,
                lead_motion_peak,
                _,
                lead_motion_metadata,
            ) = _activity_onset_peak_and_slowdown(lead_speed, active_slice)
            if lead_motion_peak is not None:
                lead_deceleration = -scalar_acceleration(timestamps, lead_speed)
                lead_deceleration = np.where(
                    np.isfinite(lead_deceleration),
                    np.maximum(lead_deceleration, 0.0),
                    np.nan,
                )
                slowdown_indexes = active_slice[active_slice >= lead_motion_peak]
                (
                    first_step_slowdown,
                    _,
                    _,
                    slowdown_metadata,
                ) = _activity_onset_peak_and_slowdown(
                    lead_deceleration, slowdown_indexes
                )
                # A short event can leave exactly two finite deceleration
                # samples after the lead-foot speed peak.  Two samples are not
                # enough to estimate an adaptive activity envelope, but their
                # observed maximum is still a timestamp-based slowdown anchor.
                # Emit it only as a low-support proxy and block formal scoring.
                (
                    first_step_slowdown,
                    slowdown_metadata,
                    slowdown_fallback_flag,
                ) = _low_sample_peak_only_phase(
                    candidate=first_step_slowdown,
                    signal=lead_deceleration,
                    indexes=slowdown_indexes,
                    metadata=slowdown_metadata,
                    phase_key="first_step_slowdown_proxy_ms",
                )
                if slowdown_fallback_flag is not None:
                    fs02_phase_flags.append(slowdown_fallback_flag)
                if first_step_slowdown is None:
                    (
                        first_step_slowdown,
                        slowdown_metadata,
                        slowdown_fallback_flag,
                    ) = _right_boundary_speed_peak_confirmed_slowdown_phase(
                        candidate=first_step_slowdown,
                        speed_signal=lead_speed,
                        timestamps=timestamps,
                        peak=lead_motion_peak,
                        event_end_index=active_end,
                        confirmation_end_index=post_end,
                        metadata=slowdown_metadata,
                        phase_key="first_step_slowdown_proxy_ms",
                    )
                    if slowdown_fallback_flag is not None:
                        fs02_phase_flags.append(slowdown_fallback_flag)

        fs01_phases = {
            "preload_ms": preload,
            "takeoff_proxy_ms": takeoff,
            "landing_proxy_ms": landing,
            "redistribution_ms": redistribution,
            "initiation_ms": initiation,
        }
        fs02_phases = {
            "direction_conversion_ms": direction_conversion,
            "peak_speed_ms": peak,
            "support_extension_proxy_ms": support_extension,
            "lead_foot_motion_onset_proxy_ms": lead_motion_onset,
            "first_step_slowdown_proxy_ms": first_step_slowdown,
        }
        fs01_phase_metadata = {
            "definitions": {
                name: PHASE_DEFINITIONS[name]
                for name in (
                    "takeoff_proxy_ms",
                    "landing_proxy_ms",
                    "redistribution_ms",
                    "initiation_ms",
                )
            },
            "signal_diagnostics": {
                "takeoff": takeoff_metadata,
                "landing": landing_metadata,
                "redistribution": redistribution_metadata,
                "initiation": {
                    "status": "candidate" if initiation is not None else "not_observed",
                    "source": "adaptive_body_center_motion_bout_onset",
                },
            },
        }
        fs02_phase_metadata = {
            "definitions": {
                name: PHASE_DEFINITIONS[name]
                for name in (
                    "direction_conversion_ms",
                    "support_extension_proxy_ms",
                    "lead_foot_motion_onset_proxy_ms",
                    "first_step_slowdown_proxy_ms",
                )
            },
            "lead_foot_side_proxy": lead_side_proxy,
            "lead_side_selection": {
                "left_peak_relative_ankle_speed_body_s": (
                    round(left_peak, 8) if left_peak is not None else None
                ),
                "right_peak_relative_ankle_speed_body_s": (
                    round(right_peak, 8) if right_peak is not None else None
                ),
                "minimum_speed_from_event_active_threshold": round(
                    lead_side_min_speed, 8
                ),
                "minimum_relative_peak_difference": 0.05,
                "status": (
                    "ambiguous_peak_difference_proxy"
                    if lead_side_ambiguous
                    else "dominant_peak_proxy"
                    if lead_side_proxy is not None
                    else "not_observed"
                ),
                "semantics": "adaptive_candidate_selection_not_true_lead_foot_identity",
            },
            "support_side_proxy": support_side_proxy,
            "signal_diagnostics": {
                "direction_conversion": direction_metadata,
                "support_extension": support_extension_metadata,
                "lead_foot_motion": lead_motion_metadata,
                "first_step_slowdown": slowdown_metadata,
            },
        }
        fs09_indexes = np.arange(fs09_start, post_end + 1)
        deceleration = -acceleration[fs09_indexes]
        max_deceleration = (
            int(fs09_indexes[np.nanargmax(deceleration)])
            if np.isfinite(deceleration).any()
            else None
        )
        stable_candidates = fs09_indexes[
            valid[fs09_indexes] & (motion_speed[fs09_indexes] < active_threshold)
        ]
        restabilization = int(stable_candidates[0]) if stable_candidates.size else None
        bout_peak = float(np.nanmax(motion_speed[active_slice]))
        thresholds = {
            "speed_q20_body_s": round(float(q20), 8),
            "speed_q80_body_s": round(float(q80), 8),
            "active_speed_body_s": round(active_threshold, 8),
            "gap_close_ms": 240.0,
            "minimum_bout_ms": 240.0,
            "minimum_persistent_displacement_body": (
                MOTION_BOUT_MIN_PERSISTENT_DISPLACEMENT_BODY
            ),
            "minimum_path_efficiency": MOTION_BOUT_MIN_PATH_EFFICIENCY,
            "minimum_direction_coherence_cosine": (
                MOTION_BOUT_MIN_DIRECTION_COHERENCE
            ),
            "candidate_persistent_displacement_body": bout_diagnostics[
                "persistent_displacement_body"
            ],
            "candidate_path_efficiency": bout_diagnostics["path_efficiency"],
            "candidate_mean_adjacent_direction_cosine": bout_diagnostics[
                "mean_adjacent_direction_cosine"
            ],
        }
        common_flags = ["provisional_rule_baseline", "event_ground_truth_missing"]
        if bout_peak <= active_threshold * 1.15:
            common_flags.append("low_motion_prominence")
        event_specs = {
            "FS01": (pre_start, onset),
            "FS02": (onset, active_end),
            "FS09": (fs09_start, post_end),
        }
        event_coverages = {
            code: _event_kinematic_coverage(valid, start, end)
            for code, (start, end) in event_specs.items()
        }
        event_motion_references = {
            code: _event_motion_reference_provenance(
                start=start,
                end=end,
                body_source=motion_body_source,
                hip_fallback=motion_hip_fallback,
                diagnostics=motion_reference_diagnostics,
            )
            for code, (start, end) in event_specs.items()
        }

        # Confidence remains capped while event ground truth is absent, but
        # each emitted event now uses its own temporal coverage rather than the
        # shared FS02 activity interval.
        event_confidences = {
            code: min(
                0.75,
                _confidence(valid, start, end, bout_peak, active_threshold),
            )
            for code, (start, end) in event_specs.items()
        }
        events.extend(
            [
                _make_event(
                    source_id=source_id,
                    video_id=video_id,
                    bout_index=bout_index,
                    event_code="FS01",
                    start=pre_start,
                    end=onset,
                    timestamps=timestamps,
                    phases=fs01_phases,
                    confidence=event_confidences["FS01"],
                    boundary_uncertainty_ms=uncertainty,
                    flags=(
                        _event_quality_flags(common_flags, event_coverages["FS01"])
                        + fs01_phase_flags
                        + _missing_phase_flags(fs01_phases)
                    ),
                    thresholds=thresholds,
                    kinematic_coverage=event_coverages["FS01"],
                    motion_reference=event_motion_references["FS01"],
                    phase_metadata=fs01_phase_metadata,
                ),
                _make_event(
                    source_id=source_id,
                    video_id=video_id,
                    bout_index=bout_index,
                    event_code="FS02",
                    start=onset,
                    end=active_end,
                    timestamps=timestamps,
                    phases=fs02_phases,
                    confidence=event_confidences["FS02"],
                    boundary_uncertainty_ms=uncertainty,
                    flags=(
                        _event_quality_flags(common_flags, event_coverages["FS02"])
                        + ["tactical_target_direction_not_observed"]
                        + fs02_phase_flags
                        + _missing_phase_flags(fs02_phases)
                    ),
                    thresholds=thresholds,
                    kinematic_coverage=event_coverages["FS02"],
                    motion_reference=event_motion_references["FS02"],
                    phase_metadata=fs02_phase_metadata,
                ),
                _make_event(
                    source_id=source_id,
                    video_id=video_id,
                    bout_index=bout_index,
                    event_code="FS09",
                    start=fs09_start,
                    end=post_end,
                    timestamps=timestamps,
                    phases={
                        "peak_speed_ms": peak,
                        "deceleration_peak_ms": max_deceleration,
                        "restabilization_onset_ms": restabilization,
                        "stable_control_onset_ms": restabilization,
                    },
                    confidence=event_confidences["FS09"],
                    boundary_uncertainty_ms=uncertainty,
                    flags=(
                        _event_quality_flags(common_flags, event_coverages["FS09"])
                        + (["restabilization_not_observed"] if restabilization is None else [])
                    ),
                    thresholds=thresholds,
                    kinematic_coverage=event_coverages["FS09"],
                    motion_reference=event_motion_references["FS09"],
                    phase_metadata={
                        "definitions": {
                            name: PHASE_DEFINITIONS[name]
                            for name in (
                                "peak_speed_ms",
                                "deceleration_peak_ms",
                                "restabilization_onset_ms",
                                "stable_control_onset_ms",
                            )
                        },
                        "signal_diagnostics": {
                            "peak_speed": {
                                "status": "candidate",
                                "source": "accepted_motion_bout_body_center_speed_argmax",
                            },
                            "deceleration": {
                                "status": (
                                    "candidate"
                                    if max_deceleration is not None
                                    else "not_observed"
                                ),
                                "source": "timestamp_ms_body_center_speed_derivative",
                            },
                            "restabilization": {
                                "status": (
                                    "candidate"
                                    if restabilization is not None
                                    else "not_observed"
                                ),
                                "source": "event_adaptive_body_center_speed_threshold",
                            },
                        },
                    },
                ),
            ]
        )
    return sorted(events, key=lambda item: (item["start_ms"], item["event_code"]))
