from __future__ import annotations

import math
from collections import Counter, defaultdict
from typing import Any

import numpy as np

from rallymate_events.evaluation import evaluate_events
from rallymate_features.event_features import (
    FEATURE_DEFINITIONS,
    clear_feature_cache,
    compute_feature,
    stability_duration_from_series,
)
from rallymate_features.fs01_fs02_features import (
    bilateral_foot_rise_duration_from_position_series,
    bilateral_foot_rise_from_position_series,
    bilateral_foot_rise_synchrony_from_velocity_series,
    bilateral_foot_vertical_slowdown_from_position_series,
    drive_measurement_candidate_side,
    drive_side_from_knee_extension_peaks,
    hip_acceleration_along_launch_direction_from_series,
    hip_center_lateral_variability_from_position_series,
    hip_center_vertical_velocity_from_position_series,
    first_step_phase_kinematics_from_series,
    launch_direction_from_hip_motion,
    launch_foot_event_kinematics_from_series,
    post_slowdown_stance_width_from_position_series,
)
from rallymate_features.fs09_features import (
    braking_ankle_slowdown_to_hip_deceleration_from_series,
    braking_side_from_speed_drops,
    hip_deceleration_to_double_support_proxy_from_series,
)
from rallymate_features.kinematics import irregular_derivative
from rallymate_features.schemas import EventInterval, FeatureResult, PoseSequence
from rallymate_scoring.scoring_context import (
    SCORING_CONTEXT_FEATURE_DEFINITIONS,
    TARGET_DIRECTION_ALIGNMENT_FEATURE_NAME,
    TARGET_DIRECTION_INDICATOR_ID,
    TARGET_DIRECTION_SEMANTIC_KEY,
    build_target_direction_alignment_feature,
)


def _interval(record: dict[str, Any]) -> EventInterval:
    return EventInterval(
        event_id=record["event_id"],
        event_code=record["event_code"],
        start_ms=int(record["start_ms"]),
        end_ms=int(record["end_ms"]),
        person_track_id=int(record["person_track_id"]),
        key_phases=record.get("key_phases_ms"),
    )


def _numeric(value: Any) -> float | None:
    if isinstance(value, (int, float)) and math.isfinite(float(value)):
        return float(value)
    return None


def _feature_definition(feature_name: str) -> dict[str, Any]:
    definition = FEATURE_DEFINITIONS.get(feature_name)
    if definition is None:
        definition = SCORING_CONTEXT_FEATURE_DEFINITIONS.get(feature_name)
    if definition is None:
        raise ValueError(f"unknown feature definition: {feature_name}")
    return definition


def _feature_payload(result: FeatureResult) -> dict[str, Any]:
    return {
        "feature_name": result.feature_name,
        "feature_version": result.feature_version,
        "value": result.value,
        "unit": result.unit,
        "confidence": result.confidence,
        "valid": result.valid,
        "reason": result.reason,
        "source_frames": list(result.source_frames),
    }


def _target_semantics_by_event(
    records: list[dict[str, Any]] | None,
) -> dict[str, dict[str, Any]]:
    result: dict[str, dict[str, Any]] = {}
    for index, record in enumerate(records or []):
        if not isinstance(record, dict):
            raise ValueError(f"semantic_ground_truth[{index}] must be an object")
        if (
            record.get("semantic_key") != TARGET_DIRECTION_SEMANTIC_KEY
            or record.get("indicator_id") != TARGET_DIRECTION_INDICATOR_ID
        ):
            continue
        event_id = record.get("event_id")
        if not isinstance(event_id, str) or not event_id:
            raise ValueError(
                f"semantic_ground_truth[{index}].event_id must be non-empty"
            )
        if event_id in result:
            raise ValueError(
                f"duplicate target_direction semantic ground truth for {event_id}"
            )
        if record.get("adjudication_status") != "accepted":
            raise ValueError(
                f"semantic_ground_truth[{index}] must be accepted adjudicated truth"
            )
        annotator = record.get("annotator_id")
        reviewer = record.get("reviewer_id")
        if (
            not isinstance(annotator, str)
            or not annotator
            or not isinstance(reviewer, str)
            or not reviewer
            or annotator == reviewer
        ):
            raise ValueError(
                f"semantic_ground_truth[{index}] requires an independent reviewer"
            )
        result[event_id] = record
    return result


def _target_reference(
    record: dict[str, Any] | None,
) -> tuple[float | None, float | None, str]:
    if record is None:
        return None, None, "manual_target_direction_missing"
    if record.get("observable") is not True:
        reason = record.get("null_reason")
        return (
            None,
            None,
            str(reason) if isinstance(reason, str) and reason else "manual_target_direction_unobservable",
        )
    value = record.get("value")
    if not isinstance(value, dict):
        return None, None, "manual_target_direction_value_invalid"
    if value.get("coordinate_frame") != "image_plane":
        return None, None, "target_direction_image_plane_transform_required"
    target = _numeric(value.get("direction_deg"))
    confidence = _numeric(record.get("annotation_confidence"))
    if (
        target is None
        or target < -180.0
        or target > 180.0
        or confidence is None
        or confidence < 0.0
        or confidence > 1.0
    ):
        return None, None, "manual_target_direction_value_invalid"
    return target, confidence, "valid"


def _is_series_payload(value: Any) -> bool:
    return isinstance(value, list) and all(
        isinstance(item, dict) and "value" in item for item in value
    )


def _series_map(value: Any) -> dict[str, list[dict[str, Any]]]:
    """Return only serialized numeric evidence series from a feature payload.

    Legacy features serialize one series directly as a list.  Event-summary
    features serialize one or more named raw series and nest the corresponding
    smoothed series below ``smoothed_value.series``.  Diagnostic lists and
    masks are deliberately not treated as interchangeable numeric series.
    """

    if _is_series_payload(value):
        return {"__single__": value}
    if not isinstance(value, dict):
        return {}
    candidate = value.get("series")
    if isinstance(candidate, dict):
        value = candidate
    return {
        str(name): payload
        for name, payload in value.items()
        if _is_series_payload(payload)
    }


def _series_values(payload: list[dict[str, Any]]) -> np.ndarray:
    values = []
    for item in payload:
        value = _numeric(item.get("value"))
        values.append(np.nan if value is None else value)
    return np.asarray(values, dtype=np.float64)


def _vector_series_values(
    payload: list[dict[str, Any]],
    *,
    dimensions: int,
) -> tuple[np.ndarray, np.ndarray] | None:
    """Parse a timestamped vector series without filling missing components."""

    timestamps = _series_timestamps(payload)
    if timestamps is None:
        return None
    rows: list[list[float]] = []
    for item in payload:
        value = item.get("value")
        if not isinstance(value, list) or len(value) != dimensions:
            return None
        rows.append(
            [
                np.nan if _numeric(component) is None else float(component)
                for component in value
            ]
        )
    values = np.asarray(rows, dtype=np.float64)
    if values.shape != (len(payload), dimensions):
        return None
    return timestamps, values


def _aggregate_series(
    payload: list[dict[str, Any]],
    aggregation: str | None,
    *,
    absolute: bool = False,
) -> float | None:
    values = _series_values(payload)
    values = values[np.isfinite(values)]
    if values.size == 0:
        return None
    if absolute:
        values = np.abs(values)
    if aggregation == "median":
        return float(np.median(values))
    if aggregation in {"peak", "peak_positive"}:
        return float(np.max(values))
    if aggregation == "peak_deviation_from_mid_support":
        return float(values[np.argmax(np.abs(values - 0.5))])
    if aggregation == "peak_absolute_signed":
        return float(values[np.argmax(np.abs(values))])
    if aggregation == "circular_mean":
        radians = np.radians(values)
        sine = float(np.mean(np.sin(radians)))
        cosine = float(np.mean(np.cos(radians)))
        if abs(sine) <= 1e-12 and abs(cosine) <= 1e-12:
            return None
        return float(math.degrees(math.atan2(sine, cosine)))
    if aggregation in {
        "event_early_median_minus_late_median",
        "event_late_median_minus_early_median",
        "event_late_absolute_median_minus_early_absolute_median",
    }:
        if values.size < 4:
            return None
        count = max(2, int(math.ceil(values.size * 0.25)))
        count = min(count, values.size // 2)
        early = float(np.median(values[:count]))
        late = float(np.median(values[-count:]))
        if aggregation == "event_early_median_minus_late_median":
            return early - late
        return late - early
    if aggregation == "event_population_standard_deviation":
        if values.size < 2:
            return None
        return float(np.std(values, ddof=0))
    return None


def _series_sample_keys(payload: list[dict[str, Any]]) -> set[tuple[Any, Any]]:
    keys = set()
    for item in payload:
        timestamp = item.get("timestamp_ms")
        source_frame = item.get("source_frame")
        if timestamp is not None or source_frame is not None:
            keys.add((timestamp, source_frame))
    return keys


def _series_overlap(
    raw_payload: list[dict[str, Any]],
    smoothed_payload: list[dict[str, Any]],
) -> bool:
    raw_keys = _series_sample_keys(raw_payload)
    smoothed_keys = _series_sample_keys(smoothed_payload)
    # Some imported feature payloads predate timestamp/source-frame evidence.
    # In that case the shared semantic name is the only available match key.
    return not raw_keys or not smoothed_keys or bool(raw_keys & smoothed_keys)


def _series_grid_exact(
    left_payload: list[dict[str, Any]],
    right_payload: list[dict[str, Any]],
) -> bool:
    """Require identical timestamp/source-frame evidence grids."""

    left_timestamps = _series_timestamps(left_payload)
    right_timestamps = _series_timestamps(right_payload)
    return bool(
        left_timestamps is not None
        and right_timestamps is not None
        and np.array_equal(left_timestamps, right_timestamps)
        and _series_sample_keys(left_payload) == _series_sample_keys(right_payload)
    )


def _edge_drop(payload: list[dict[str, Any]]) -> float | None:
    return _aggregate_series(
        payload,
        "event_early_median_minus_late_median",
    )


def _braking_side(left_drop: float | None, right_drop: float | None) -> int | None:
    if left_drop is None or right_drop is None:
        return None
    if left_drop <= 0.0 and right_drop <= 0.0:
        return 0
    if left_drop > right_drop:
        return -1
    if right_drop > left_drop:
        return 1
    return 0


def _peak_slowdown(payload: list[dict[str, Any]]) -> float | None:
    """Return the strongest positive no-smoothing slowdown in a raw series.

    FS09 v0.2 chooses a braking-side proxy from positive event-edge speed
    drop first, then from a positive local slowdown when both net drops are
    non-positive.  The counterfactual deliberately differentiates the
    serialized raw ankle-speed evidence without applying another smoother.
    """

    timestamps = _series_timestamps(payload)
    if timestamps is None:
        return None
    values = _series_values(payload)
    slowdown = -irregular_derivative(timestamps, values[:, None])[:, 0]
    positive = slowdown[np.isfinite(slowdown) & (slowdown > 0.0)]
    if positive.size == 0:
        return None
    return float(np.max(positive))


def _fs09_v2_braking_side(
    left_payload: list[dict[str, Any]],
    right_payload: list[dict[str, Any]],
) -> int | None:
    """Replay the authoritative FS09 v0.2 side-selection semantics."""

    left_drop = _edge_drop(left_payload)
    right_drop = _edge_drop(right_payload)
    side, _ = braking_side_from_speed_drops(
        left_drop,
        right_drop,
        left_peak_slowdown=_peak_slowdown(left_payload),
        right_peak_slowdown=_peak_slowdown(right_payload),
    )
    return side


def _series_timestamps(payload: list[dict[str, Any]]) -> np.ndarray | None:
    timestamps = []
    for item in payload:
        timestamp = item.get("timestamp_ms")
        if not isinstance(timestamp, (int, float)) or not math.isfinite(float(timestamp)):
            return None
        timestamps.append(float(timestamp))
    values = np.asarray(timestamps, dtype=np.float64)
    if values.size < 2 or np.any(np.diff(values) <= 0):
        return None
    return values


def _aligned_scalar_series(
    series: dict[str, list[dict[str, Any]]],
    names: set[str],
) -> tuple[np.ndarray, dict[str, np.ndarray]] | None:
    """Parse named scalar series only when their sample grids match exactly."""

    if not names.issubset(series):
        return None
    ordered_names = sorted(names)
    reference_payload = series[ordered_names[0]]
    reference_keys = _series_sample_keys(reference_payload)
    timestamps = _series_timestamps(reference_payload)
    if timestamps is None:
        return None
    values: dict[str, np.ndarray] = {}
    for name in ordered_names:
        payload = series[name]
        candidate_timestamps = _series_timestamps(payload)
        if (
            candidate_timestamps is None
            or not np.array_equal(timestamps, candidate_timestamps)
            or _series_sample_keys(payload) != reference_keys
        ):
            return None
        values[name] = _series_values(payload)
    return timestamps, values


def _aligned_vector_series(
    series: dict[str, list[dict[str, Any]]],
    names: set[str],
    *,
    dimensions: int,
) -> tuple[np.ndarray, dict[str, np.ndarray]] | None:
    """Parse named vector series only when timestamp and source grids match."""

    if not names.issubset(series):
        return None
    ordered_names = sorted(names)
    reference_payload = series[ordered_names[0]]
    reference_keys = _series_sample_keys(reference_payload)
    parsed_reference = _vector_series_values(
        reference_payload,
        dimensions=dimensions,
    )
    if parsed_reference is None:
        return None
    timestamps = parsed_reference[0]
    values: dict[str, np.ndarray] = {}
    for name in ordered_names:
        payload = series[name]
        parsed = _vector_series_values(payload, dimensions=dimensions)
        if (
            parsed is None
            or not np.array_equal(timestamps, parsed[0])
            or _series_sample_keys(payload) != reference_keys
        ):
            return None
        values[name] = parsed[1]
    return timestamps, values


def _peak_sample(
    payload: list[dict[str, Any]],
    *,
    negative_irregular_derivative: bool = False,
) -> tuple[float, float] | None:
    """Return ``(timestamp_ms, peak)`` using production first-max semantics."""

    timestamps = _series_timestamps(payload)
    if timestamps is None:
        return None
    values = _series_values(payload)
    if negative_irregular_derivative:
        values = -irregular_derivative(timestamps, values[:, None])[:, 0]
    finite = np.flatnonzero(np.isfinite(values))
    if finite.size == 0:
        return None
    selected = int(finite[np.argmax(values[finite])])
    return float(timestamps[selected]), float(values[selected])


def _raw_drive_peaks_from_flexion(
    raw_series: dict[str, list[dict[str, Any]]],
) -> tuple[tuple[float, float] | None, tuple[float, float] | None]:
    return (
        _peak_sample(
            raw_series["left_knee_flexion_deg"],
            negative_irregular_derivative=True,
        ),
        _peak_sample(
            raw_series["right_knee_flexion_deg"],
            negative_irregular_derivative=True,
        ),
    )


def _low_motion_duration_ms(
    left_payload: list[dict[str, Any]],
    right_payload: list[dict[str, Any]],
) -> float | None:
    if len(left_payload) != len(right_payload):
        return None
    timestamps = _series_timestamps(left_payload)
    right_timestamps = _series_timestamps(right_payload)
    if (
        timestamps is None
        or right_timestamps is None
        or not np.array_equal(timestamps, right_timestamps)
    ):
        return None
    left = _series_values(left_payload)
    right = _series_values(right_payload)
    valid = np.isfinite(left) & np.isfinite(right)
    if int(valid.sum()) < 3:
        return None
    low_motion = valid & (left <= np.median(left[valid])) & (
        right <= np.median(right[valid])
    )
    longest: tuple[int, int] | None = None
    start: int | None = None
    for index, active in enumerate(np.append(low_motion, False)):
        if active and start is None:
            start = index
        elif not active and start is not None:
            end = index - 1
            if longest is None or end - start > longest[1] - longest[0]:
                longest = (start, end)
            start = None
    if longest is None:
        return 0.0
    median_dt = float(np.median(np.diff(timestamps)))
    return max(
        0.0,
        float(timestamps[longest[1]] - timestamps[longest[0]] + median_dt),
    )


def compute_smoothing_counterfactual(result: FeatureResult) -> dict[str, Any]:
    """Compare the production value with a raw, no-smoothing counterpart.

    The function only computes a value when the serialized raw evidence can be
    tied to the smoothed evidence by an exact semantic name, a documented
    transform, or a supported multi-series aggregation.  It never selects the
    first dictionary member as a guess: ambiguous features return ``None`` and
    a machine-readable reason instead.
    """

    production_value = _numeric(result.value)
    if production_value is None:
        return {
            "value": None,
            "raw_counterfactual_value": None,
            "matched_series": [],
            "reason": "production_feature_value_unavailable",
        }
    raw_series = _series_map(result.raw_value)
    if not raw_series:
        return {
            "value": None,
            "raw_counterfactual_value": None,
            "matched_series": [],
            "reason": "raw_numeric_series_unavailable",
        }
    smoothed_series = _series_map(result.smoothed_value)
    if not smoothed_series:
        return {
            "value": None,
            "raw_counterfactual_value": None,
            "matched_series": [],
            "reason": "smoothed_numeric_series_unavailable",
        }
    aggregation = result.provenance.get("aggregation")
    exact_names = sorted(set(raw_series) & set(smoothed_series))
    exact_names = [
        name
        for name in exact_names
        if _series_overlap(raw_series[name], smoothed_series[name])
    ]
    matches = [
        {"raw": name, "smoothed": name, "transform": "identity"}
        for name in exact_names
    ]

    raw_counterfactual: float | None = None
    computed_reason = "computed_from_exact_series_match"
    if raw_series.keys() == {"__single__"} and smoothed_series.keys() == {
        "__single__"
    }:
        raw_counterfactual = _aggregate_series(
            raw_series["__single__"], aggregation
        )
        computed_reason = "computed_from_legacy_single_series"
    elif aggregation in {
        "median",
        "peak",
        "peak_positive",
        "peak_deviation_from_mid_support",
        "peak_absolute_signed",
        "circular_mean",
        "event_early_median_minus_late_median",
        "event_late_median_minus_early_median",
        "event_population_standard_deviation",
    } and len(exact_names) == 1:
        raw_counterfactual = _aggregate_series(
            raw_series[exact_names[0]], aggregation
        )
    elif aggregation in {
        "minimum_of_left_and_right_event_local_upward_excursion",
        "longest_simultaneous_half_peak_upward_excursion_run",
    }:
        required = {"left_foot_position", "right_foot_position"}
        parsed = _aligned_vector_series(raw_series, required, dimensions=2)
        grids_match_prepared = bool(
            required.issubset(raw_series)
            and required.issubset(smoothed_series)
            and all(
                _series_grid_exact(raw_series[name], smoothed_series[name])
                for name in required
            )
        )
        scale = _numeric(result.provenance.get("body_scale"))
        if parsed is not None and grids_match_prepared and scale is not None:
            timestamps, positions = parsed
            indexes = np.arange(timestamps.size, dtype=np.int64)
            if aggregation == (
                "minimum_of_left_and_right_event_local_upward_excursion"
            ):
                raw_value, _, _, _, _ = bilateral_foot_rise_from_position_series(
                    timestamps,
                    positions["left_foot_position"],
                    positions["right_foot_position"],
                    indexes,
                    scale,
                )
                computed_reason = (
                    "computed_from_production_fs01_bilateral_rise_pure_function"
                )
            else:
                bilateral_valid = np.isfinite(
                    positions["left_foot_position"]
                ).all(axis=1) & np.isfinite(
                    positions["right_foot_position"]
                ).all(axis=1)
                raw_value, _, _, _, _, _ = (
                    bilateral_foot_rise_duration_from_position_series(
                        timestamps,
                        positions["left_foot_position"],
                        positions["right_foot_position"],
                        indexes,
                        scale,
                        bilateral_valid,
                    )
                )
                computed_reason = (
                    "computed_from_production_fs01_bilateral_rise_duration_"
                    "pure_function"
                )
            raw_counterfactual = None if raw_value is None else float(raw_value)
            matches = [
                {
                    "raw": name,
                    "smoothed": name,
                    "transform": (
                        "production_event_local_rise_from_position_series_"
                        "with_timestamp_ms_and_body_scale"
                    ),
                }
                for name in sorted(required)
            ]
    elif aggregation == "absolute_peak_upward_velocity_time_difference":
        required = {"left_foot_velocity", "right_foot_velocity"}
        parsed = _aligned_vector_series(raw_series, required, dimensions=2)
        grids_match_prepared = bool(
            required.issubset(raw_series)
            and required.issubset(smoothed_series)
            and all(
                _series_grid_exact(raw_series[name], smoothed_series[name])
                for name in required
            )
        )
        scale = _numeric(result.provenance.get("body_scale"))
        if parsed is not None and grids_match_prepared and scale is not None:
            timestamps, velocity = parsed
            indexes = np.arange(timestamps.size, dtype=np.int64)
            raw_value, _, _, _, _ = (
                bilateral_foot_rise_synchrony_from_velocity_series(
                    timestamps,
                    velocity["left_foot_velocity"],
                    velocity["right_foot_velocity"],
                    indexes,
                    scale,
                )
            )
            raw_counterfactual = None if raw_value is None else float(raw_value)
            matches = [
                {
                    "raw": name,
                    "smoothed": name,
                    "transform": (
                        "production_peak_image_up_velocity_timing_from_"
                        "timestamp_ms_and_body_scale"
                    ),
                }
                for name in sorted(required)
            ]
            computed_reason = (
                "computed_from_production_fs01_bilateral_rise_synchrony_"
                "pure_function"
            )
    elif aggregation == "peak_positive_image_up_velocity":
        required = {"hip_position"}
        parsed = _aligned_vector_series(raw_series, required, dimensions=2)
        grids_match_prepared = bool(
            required.issubset(raw_series)
            and required.issubset(smoothed_series)
            and _series_grid_exact(
                raw_series["hip_position"],
                smoothed_series["hip_position"],
            )
        )
        scale = _numeric(result.provenance.get("body_scale"))
        if parsed is not None and grids_match_prepared and scale is not None:
            timestamps, position = parsed
            indexes = np.arange(timestamps.size, dtype=np.int64)
            raw_value, _, _, _ = hip_center_vertical_velocity_from_position_series(
                timestamps,
                position["hip_position"],
                indexes,
                scale,
            )
            raw_counterfactual = None if raw_value is None else float(raw_value)
            matches = [
                {
                    "raw": "hip_position",
                    "smoothed": "hip_position",
                    "transform": (
                        "negative_irregular_derivative_timestamp_ms_then_"
                        "production_peak_with_body_scale"
                    ),
                }
            ]
            computed_reason = (
                "computed_from_production_fs01_hip_vertical_velocity_pure_function"
            )
    elif aggregation in {
        "absolute_event_local_vertical_slowdown_time_difference",
        "post_bilateral_vertical_slowdown_median",
        "post_bilateral_vertical_slowdown_population_standard_deviation_with_boundary_censored_nearest_pre_sample",
    }:
        required = {"left_foot_position", "right_foot_position"}
        if aggregation == (
            "post_bilateral_vertical_slowdown_population_standard_deviation_"
            "with_boundary_censored_nearest_pre_sample"
        ):
            required.add("hip_position")
        parsed = _aligned_vector_series(raw_series, required, dimensions=2)
        grids_match_prepared = bool(
            required.issubset(raw_series)
            and required.issubset(smoothed_series)
            and all(
                _series_grid_exact(raw_series[name], smoothed_series[name])
                for name in required
            )
        )
        scale = _numeric(result.provenance.get("body_scale"))
        if parsed is not None and grids_match_prepared and scale is not None:
            timestamps, position = parsed
            indexes = np.arange(timestamps.size, dtype=np.int64)
            if aggregation == (
                "absolute_event_local_vertical_slowdown_time_difference"
            ):
                raw_value, _, _, _, _ = (
                    bilateral_foot_vertical_slowdown_from_position_series(
                        timestamps,
                        position["left_foot_position"],
                        position["right_foot_position"],
                        indexes,
                        scale,
                    )
                )
                computed_reason = (
                    "computed_from_production_fs01_bilateral_slowdown_"
                    "timing_pure_function"
                )
            elif aggregation == "post_bilateral_vertical_slowdown_median":
                raw_value, _, _, _ = (
                    post_slowdown_stance_width_from_position_series(
                        timestamps,
                        position["left_foot_position"],
                        position["right_foot_position"],
                        indexes,
                        scale,
                    )
                )
                computed_reason = (
                    "computed_from_production_fs01_post_slowdown_stance_"
                    "pure_function"
                )
            else:
                raw_value, _, _, _, _ = (
                    hip_center_lateral_variability_from_position_series(
                        timestamps,
                        position["left_foot_position"],
                        position["right_foot_position"],
                        position["hip_position"],
                        indexes,
                        scale,
                    )
                )
                computed_reason = (
                    "computed_from_production_fs01_post_slowdown_hip_"
                    "variability_pure_function"
                )
            raw_counterfactual = None if raw_value is None else float(raw_value)
            matches = [
                {
                    "raw": name,
                    "smoothed": name,
                    "transform": (
                        "production_bilateral_vertical_slowdown_anchor_from_"
                        "position_series_timestamp_ms"
                    ),
                }
                for name in sorted(required)
            ]
    elif aggregation in {
        "larger_positive_foot_displacement_along_launch_direction",
        "selected_launch_foot_peak_speed",
        "launch_minus_other_foot_projected_event_displacement",
        "longest_half_peak_positive_projected_velocity_run",
    }:
        required = {
            "hip_position",
            "left_foot_position",
            "right_foot_position",
            "left_foot_velocity",
            "right_foot_velocity",
        }
        parsed = _aligned_vector_series(raw_series, required, dimensions=2)
        grids_match_prepared = bool(
            required.issubset(raw_series)
            and required.issubset(smoothed_series)
            and all(
                _series_grid_exact(raw_series[name], smoothed_series[name])
                for name in required
            )
        )
        scale = _numeric(result.provenance.get("body_scale"))
        if parsed is not None and grids_match_prepared and scale is not None:
            timestamps, series = parsed
            components = launch_foot_event_kinematics_from_series(
                timestamps,
                series["hip_position"],
                series["left_foot_position"],
                series["right_foot_position"],
                series["left_foot_velocity"],
                series["right_foot_velocity"],
                np.arange(timestamps.size, dtype=np.int64),
                scale,
            )
            field_by_aggregation = {
                "larger_positive_foot_displacement_along_launch_direction": (
                    "launch_code"
                ),
                "selected_launch_foot_peak_speed": "speed_peak_body_s",
                "launch_minus_other_foot_projected_event_displacement": (
                    "relative_displacement_body"
                ),
                "longest_half_peak_positive_projected_velocity_run": (
                    "motion_duration_ms"
                ),
            }
            raw_value = components[field_by_aggregation[aggregation]]
            raw_counterfactual = (
                None if raw_value is None else float(raw_value)
            )
            matches = [
                {
                    "raw": name,
                    "smoothed": name,
                    "transform": (
                        "production_fs02_m04_launch_foot_kinematics_from_"
                        "aligned_position_velocity_timestamp_ms_series"
                    ),
                }
                for name in sorted(required)
            ]
            computed_reason = (
                "computed_from_production_fs02_m04_launch_foot_kinematics_"
                "pure_function"
            )
    elif aggregation in {
        "peak_speed_minus_post_peak_late_median",
        "selected_launch_foot_projected_event_displacement",
        "post_event_first_step_slowdown_phase_mean_velocity_cosine",
        "event_first_step_slowdown_phase_to_alignment_envelope_start_time_delta",
        "post_event_first_step_slowdown_phase_median",
    }:
        required = {
            "hip_position",
            "hip_velocity",
            "left_foot_position",
            "right_foot_position",
            "left_foot_velocity",
            "right_foot_velocity",
        }
        parsed = _aligned_vector_series(raw_series, required, dimensions=2)
        grids_match_prepared = bool(
            required.issubset(raw_series)
            and required.issubset(smoothed_series)
            and all(
                _series_grid_exact(raw_series[name], smoothed_series[name])
                for name in required
            )
        )
        scale = _numeric(result.provenance.get("body_scale"))
        phase_required = aggregation in {
            "post_event_first_step_slowdown_phase_mean_velocity_cosine",
            "event_first_step_slowdown_phase_to_alignment_envelope_start_time_delta",
            "post_event_first_step_slowdown_phase_median",
        }
        summary = (
            result.smoothed_value.get("summary", {})
            if isinstance(result.smoothed_value, dict)
            else {}
        )
        key_phases: dict[str, int | None] | None = {}
        phase_evidence_valid = True
        if phase_required:
            anchor_source = summary.get("post_step_anchor_source")
            phase_timestamp = summary.get("event_phase_timestamp_ms")
            if (
                anchor_source == "event_key_phase"
                and isinstance(phase_timestamp, int)
                and not isinstance(phase_timestamp, bool)
            ):
                key_phases = {
                    "first_step_slowdown_proxy_ms": int(phase_timestamp)
                }
            elif (
                anchor_source == "computed_signal_slowdown_fallback"
                and summary.get("phase_anchor_status")
                == "event_phase_not_provided_legacy_fallback"
            ):
                key_phases = {}
            else:
                phase_evidence_valid = False
        if (
            parsed is not None
            and grids_match_prepared
            and scale is not None
            and phase_evidence_valid
        ):
            timestamps, series = parsed
            components = first_step_phase_kinematics_from_series(
                timestamps,
                series["hip_position"],
                series["hip_velocity"],
                series["left_foot_position"],
                series["right_foot_position"],
                series["left_foot_velocity"],
                series["right_foot_velocity"],
                np.arange(timestamps.size, dtype=np.int64),
                scale,
                key_phases,
            )
            field_by_aggregation = {
                "peak_speed_minus_post_peak_late_median": (
                    "speed_drop_body_s"
                ),
                "selected_launch_foot_projected_event_displacement": (
                    "first_step_displacement_body"
                ),
                "post_event_first_step_slowdown_phase_mean_velocity_cosine": (
                    "post_direction_consistency"
                ),
                "event_first_step_slowdown_phase_to_alignment_envelope_start_time_delta": (
                    "slowdown_to_post_direction_ms"
                ),
                "post_event_first_step_slowdown_phase_median": (
                    "post_stance_width_body"
                ),
            }
            raw_value = components[field_by_aggregation[aggregation]]
            raw_counterfactual = (
                None if raw_value is None else float(raw_value)
            )
            matches = [
                {
                    "raw": name,
                    "smoothed": name,
                    "transform": (
                        "production_fs02_m05_first_step_phase_kinematics_"
                        "from_aligned_series_and_fixed_event_phase"
                    ),
                }
                for name in sorted(required)
            ]
            computed_reason = (
                "computed_from_production_fs02_m05_first_step_phase_"
                "kinematics_pure_function"
            )
    elif aggregation == "event_late_absolute_median_minus_early_absolute_median":
        raw_name = "shoulder_hip_angular_velocity"
        smoothed_name = "absolute_angular_velocity"
        if (
            raw_name in raw_series
            and smoothed_name in smoothed_series
            and _series_overlap(raw_series[raw_name], smoothed_series[smoothed_name])
        ):
            raw_counterfactual = _aggregate_series(
                raw_series[raw_name], aggregation, absolute=True
            )
            matches = [
                {
                    "raw": raw_name,
                    "smoothed": smoothed_name,
                    "transform": "absolute_value",
                }
            ]
            computed_reason = "computed_from_compatible_series_match"
    elif aggregation == "event_hip_displacement_velocity_composite_direction":
        if "hip_position" in exact_names:
            parsed = _vector_series_values(
                raw_series["hip_position"],
                dimensions=2,
            )
            if parsed is not None:
                timestamps, hip_positions = parsed
                _, direction_deg, _, _ = launch_direction_from_hip_motion(
                    timestamps,
                    hip_positions,
                    np.arange(timestamps.size, dtype=np.int64),
                )
                raw_counterfactual = direction_deg
                matches = [
                    {
                        "raw": "hip_position",
                        "smoothed": "hip_position",
                        "transform": (
                            "production_launch_direction_from_raw_hip_position_"
                            "timestamp_ms"
                        ),
                    }
                ]
                computed_reason = "computed_from_production_direction_pure_function"
    elif aggregation == "peak_positive_projection_on_event_launch_direction":
        required_names = {"hip_position", "hip_acceleration_xy_body_s2"}
        if required_names.issubset(exact_names):
            parsed_position = _vector_series_values(
                raw_series["hip_position"],
                dimensions=2,
            )
            parsed_acceleration = _vector_series_values(
                raw_series["hip_acceleration_xy_body_s2"],
                dimensions=2,
            )
            sample_keys_match = _series_sample_keys(
                raw_series["hip_position"]
            ) == _series_sample_keys(raw_series["hip_acceleration_xy_body_s2"])
            if parsed_position is not None and parsed_acceleration is not None:
                position_timestamps, hip_positions = parsed_position
                acceleration_timestamps, hip_acceleration = parsed_acceleration
                if sample_keys_match and np.array_equal(
                    position_timestamps, acceleration_timestamps
                ):
                    raw_counterfactual, _, _, _ = (
                        hip_acceleration_along_launch_direction_from_series(
                            position_timestamps,
                            hip_positions,
                            hip_acceleration,
                            np.arange(position_timestamps.size, dtype=np.int64),
                        )
                    )
                    matches = [
                        {
                            "raw": "hip_position",
                            "smoothed": "hip_position",
                            "transform": (
                                "production_launch_direction_from_raw_hip_"
                                "position_timestamp_ms"
                            ),
                        },
                        {
                            "raw": "hip_acceleration_xy_body_s2",
                            "smoothed": "hip_acceleration_xy_body_s2",
                            "transform": (
                                "projection_on_raw_event_launch_direction_"
                                "then_production_peak"
                            ),
                        },
                    ]
                    computed_reason = (
                        "computed_from_production_hip_acceleration_projection_"
                        "pure_function"
                    )
    elif aggregation in {
        "larger_event_local_knee_extension_velocity",
        "selected_positive_drive_side_or_strongest_observed_knee_extension_velocity",
    }:
        required_raw = {"left_knee_flexion_deg", "right_knee_flexion_deg"}
        required_smoothed = {
            "left_knee_extension_velocity_deg_s",
            "right_knee_extension_velocity_deg_s",
        }
        if required_raw.issubset(raw_series) and required_smoothed.issubset(
            smoothed_series
        ):
            left_peak, right_peak = _raw_drive_peaks_from_flexion(raw_series)
            left_value = left_peak[1] if left_peak is not None else None
            right_value = right_peak[1] if right_peak is not None else None
            drive_code, _, _ = drive_side_from_knee_extension_peaks(
                left_value, right_value
            )
            if aggregation == "larger_event_local_knee_extension_velocity":
                raw_counterfactual = (
                    None if drive_code is None else float(drive_code)
                )
            else:
                measurement_side, _ = drive_measurement_candidate_side(
                    left_value, right_value
                )
                raw_counterfactual = (
                    left_value
                    if measurement_side == "left"
                    else right_value
                    if measurement_side == "right"
                    else None
                )
            matches = [
                {
                    "raw": f"{side}_knee_flexion_deg",
                    "smoothed": f"{side}_knee_extension_velocity_deg_s",
                    "transform": "negative_irregular_derivative_timestamp_ms",
                }
                for side in ("left", "right")
            ]
            computed_reason = "computed_from_supported_multi_series_match"
    elif aggregation == (
        "moving_foot_peak_up_velocity_time_minus_positive_drive_or_"
        "strongest_observed_knee_peak_time"
    ):
        required = {
            "left_knee_extension_velocity_deg_s",
            "right_knee_extension_velocity_deg_s",
            "left_foot_upward_velocity_body_s",
            "right_foot_upward_velocity_body_s",
        }
        if required.issubset(exact_names):
            left_knee = _peak_sample(
                raw_series["left_knee_extension_velocity_deg_s"]
            )
            right_knee = _peak_sample(
                raw_series["right_knee_extension_velocity_deg_s"]
            )
            left_foot = _peak_sample(
                raw_series["left_foot_upward_velocity_body_s"]
            )
            right_foot = _peak_sample(
                raw_series["right_foot_upward_velocity_body_s"]
            )
            left_value = left_knee[1] if left_knee is not None else None
            right_value = right_knee[1] if right_knee is not None else None
            measurement_side, _ = drive_measurement_candidate_side(
                left_value, right_value
            )
            drive_peak = (
                left_knee
                if measurement_side == "left"
                else right_knee
                if measurement_side == "right"
                else None
            )
            moving_foot_peak = (
                right_foot
                if measurement_side == "left"
                else left_foot
                if measurement_side == "right"
                else None
            )
            if drive_peak is not None and moving_foot_peak is not None:
                raw_counterfactual = moving_foot_peak[0] - drive_peak[0]
            computed_reason = "computed_from_supported_multi_series_match"
    elif aggregation == "peak_time_delta":
        required = {"left_ankle_speed", "right_ankle_speed", "hip_speed"}
        parsed = _aligned_scalar_series(raw_series, required)
        if parsed is not None:
            timestamps, values = parsed
            indexes = np.arange(timestamps.size, dtype=np.int64)
            left_slowdown = -irregular_derivative(
                timestamps, values["left_ankle_speed"][:, None]
            )[:, 0]
            right_slowdown = -irregular_derivative(
                timestamps, values["right_ankle_speed"][:, None]
            )[:, 0]
            hip_slowdown = -irregular_derivative(
                timestamps, values["hip_speed"][:, None]
            )[:, 0]
            raw_value, _, _ = (
                braking_ankle_slowdown_to_hip_deceleration_from_series(
                    timestamps,
                    values["left_ankle_speed"],
                    values["right_ankle_speed"],
                    left_slowdown,
                    right_slowdown,
                    hip_slowdown,
                    indexes,
                )
            )
            raw_counterfactual = (
                None if raw_value is None else float(raw_value)
            )
            matches = [
                {
                    "raw": name,
                    "smoothed": (
                        name
                        if name != "hip_speed"
                        else "hip_slowdown"
                    ),
                    "transform": (
                        "identity_for_event_edge_and_negative_irregular_"
                        "derivative_timestamp_ms_for_peak_timing"
                    ),
                }
                for name in sorted(required)
            ]
            computed_reason = (
                "computed_from_production_fs09_braking_phase_timing_pure_function"
            )
    elif aggregation == "phase_proxy_start_time_delta":
        required = {"left_ankle_speed", "right_ankle_speed", "hip_speed"}
        parsed = _aligned_scalar_series(raw_series, required)
        if parsed is not None:
            timestamps, values = parsed
            indexes = np.arange(timestamps.size, dtype=np.int64)
            hip_slowdown = -irregular_derivative(
                timestamps, values["hip_speed"][:, None]
            )[:, 0]
            raw_value, _, _, _ = (
                hip_deceleration_to_double_support_proxy_from_series(
                    timestamps,
                    values["left_ankle_speed"],
                    values["right_ankle_speed"],
                    hip_slowdown,
                    indexes,
                )
            )
            raw_counterfactual = (
                None if raw_value is None else float(raw_value)
            )
            matches = [
                {
                    "raw": name,
                    "smoothed": (
                        name
                        if name != "hip_speed"
                        else "hip_slowdown"
                    ),
                    "transform": (
                        "event_median_low_motion_proxy_or_negative_irregular_"
                        "derivative_timestamp_ms_for_hip_peak"
                    ),
                }
                for name in sorted(required)
            ]
            computed_reason = (
                "computed_from_production_fs09_support_phase_timing_pure_function"
            )
    elif aggregation in {
        "larger_positive_ankle_speed_drop",
        "positive_net_drop_then_positive_local_slowdown",
        "selected_ankle_early_median_minus_late_median",
    }:
        required = {"left_ankle_speed", "right_ankle_speed"}
        if required.issubset(exact_names):
            left_drop = _edge_drop(raw_series["left_ankle_speed"])
            right_drop = _edge_drop(raw_series["right_ankle_speed"])
            if aggregation == "larger_positive_ankle_speed_drop":
                # Backward-compatible replay for FS09 v0.1 artifacts.
                side = _braking_side(left_drop, right_drop)
            else:
                side = _fs09_v2_braking_side(
                    raw_series["left_ankle_speed"],
                    raw_series["right_ankle_speed"],
                )
            if aggregation == "larger_positive_ankle_speed_drop":
                raw_counterfactual = None if side is None else float(side)
            elif aggregation == "positive_net_drop_then_positive_local_slowdown":
                raw_counterfactual = None if side is None else float(side)
            elif side == -1:
                raw_counterfactual = left_drop
            elif side == 1:
                raw_counterfactual = right_drop
            computed_reason = "computed_from_supported_multi_series_match"
    elif aggregation == "longest_simultaneous_event_low_ankle_motion_run":
        required = {"left_ankle_speed", "right_ankle_speed"}
        if required.issubset(exact_names):
            raw_counterfactual = _low_motion_duration_ms(
                raw_series["left_ankle_speed"],
                raw_series["right_ankle_speed"],
            )
            computed_reason = "computed_from_supported_multi_series_match"
    elif aggregation == "longest_contiguous_stable_run":
        required = {"speed", "absolute_angular_velocity"}
        if required.issubset(exact_names):
            timestamps = _series_timestamps(raw_series["speed"])
            angular_timestamps = _series_timestamps(
                raw_series["absolute_angular_velocity"]
            )
            if (
                timestamps is not None
                and angular_timestamps is not None
                and np.array_equal(timestamps, angular_timestamps)
            ):
                duration, _, _ = stability_duration_from_series(
                    timestamps.astype(np.int64),
                    _series_values(raw_series["speed"]),
                    _series_values(raw_series["absolute_angular_velocity"]),
                )
                raw_counterfactual = (
                    None if duration is None else float(duration)
                )
                matches = [
                    {
                        "raw": name,
                        "smoothed": name,
                        "transform": (
                            "production_provisional_stability_envelope_"
                            "recomputed_on_raw_series"
                        ),
                    }
                    for name in sorted(required)
                ]
                computed_reason = "computed_from_production_stability_pure_function"

    if raw_counterfactual is None:
        if aggregation in {"peak_time_delta", "phase_proxy_start_time_delta"}:
            reason = "derived_timing_series_counterfactual_not_reconstructable"
        elif not exact_names and not matches:
            reason = "no_semantically_matching_raw_smoothed_series"
        elif aggregation in {
            "minimum_of_left_and_right_event_local_upward_excursion",
            "absolute_peak_upward_velocity_time_difference",
            "longest_simultaneous_half_peak_upward_excursion_run",
            "peak_positive_image_up_velocity",
            "absolute_event_local_vertical_slowdown_time_difference",
            "post_bilateral_vertical_slowdown_median",
            "post_bilateral_vertical_slowdown_population_standard_deviation_with_boundary_censored_nearest_pre_sample",
            "larger_positive_foot_displacement_along_launch_direction",
            "selected_launch_foot_peak_speed",
            "launch_minus_other_foot_projected_event_displacement",
            "longest_half_peak_positive_projected_velocity_run",
            "peak_speed_minus_post_peak_late_median",
            "selected_launch_foot_projected_event_displacement",
            "post_event_first_step_slowdown_phase_mean_velocity_cosine",
            "event_first_step_slowdown_phase_to_alignment_envelope_start_time_delta",
            "post_event_first_step_slowdown_phase_median",
            "larger_positive_ankle_speed_drop",
            "positive_net_drop_then_positive_local_slowdown",
            "selected_ankle_early_median_minus_late_median",
            "longest_simultaneous_event_low_ankle_motion_run",
            "larger_event_local_knee_extension_velocity",
            "selected_positive_drive_side_or_strongest_observed_knee_extension_velocity",
            "moving_foot_peak_up_velocity_time_minus_positive_drive_or_strongest_observed_knee_peak_time",
            "longest_contiguous_stable_run",
        }:
            reason = (
                "required_fs01_m03_series_counterfactual_unavailable"
                if aggregation
                in {
                    "minimum_of_left_and_right_event_local_upward_excursion",
                    "absolute_peak_upward_velocity_time_difference",
                    "longest_simultaneous_half_peak_upward_excursion_run",
                    "peak_positive_image_up_velocity",
                }
                else "required_fs01_m04_series_counterfactual_unavailable"
                if aggregation
                in {
                    "absolute_event_local_vertical_slowdown_time_difference",
                    "post_bilateral_vertical_slowdown_median",
                    "post_bilateral_vertical_slowdown_population_standard_deviation_with_boundary_censored_nearest_pre_sample",
                }
                else "required_fs02_m04_series_counterfactual_unavailable"
                if aggregation
                in {
                    "larger_positive_foot_displacement_along_launch_direction",
                    "selected_launch_foot_peak_speed",
                    "launch_minus_other_foot_projected_event_displacement",
                    "longest_half_peak_positive_projected_velocity_run",
                }
                else "required_fs02_m05_series_or_phase_counterfactual_unavailable"
                if aggregation
                in {
                    "peak_speed_minus_post_peak_late_median",
                    "selected_launch_foot_projected_event_displacement",
                    "post_event_first_step_slowdown_phase_mean_velocity_cosine",
                    "event_first_step_slowdown_phase_to_alignment_envelope_start_time_delta",
                    "post_event_first_step_slowdown_phase_median",
                }
                else "required_multi_series_counterfactual_unavailable"
            )
        else:
            reason = f"raw_counterfactual_unavailable_for_aggregation:{aggregation}"
        return {
            "value": None,
            "raw_counterfactual_value": None,
            "matched_series": matches,
            "reason": reason,
        }
    return {
        "value": production_value - raw_counterfactual,
        "raw_counterfactual_value": raw_counterfactual,
        "matched_series": matches,
        "reason": computed_reason,
    }


def _aggregate_raw(result: FeatureResult) -> float | None:
    """Backward-compatible scalar accessor for legacy callers/tests."""

    counterfactual = compute_smoothing_counterfactual(result)
    value = counterfactual["raw_counterfactual_value"]
    if value is None:
        return None
    return float(value)


def _metric(values: list[float], eligible: int) -> dict[str, Any]:
    if not values:
        return {
            "mae": None,
            "p95": None,
            "bias": None,
            "valid_count": 0,
            "eligible_count": eligible,
            "valid_rate": 0.0,
        }
    array = np.asarray(values, dtype=np.float64)
    return {
        "mae": round(float(np.mean(np.abs(array))), 8),
        "p95": round(float(np.percentile(np.abs(array), 95)), 8),
        "bias": round(float(np.mean(array)), 8),
        "valid_count": int(array.size),
        "eligible_count": eligible,
        "valid_rate": round(float(array.size / max(eligible, 1)), 8),
    }


def evaluate_feature_errors(
    model_sequence: PoseSequence,
    corrected_sequence: PoseSequence | None,
    predictions: list[dict[str, Any]],
    ground_truth_events: list[dict[str, Any]] | None,
    feature_names_by_event: dict[str, list[str]],
    *,
    context_feature_names_by_event: dict[str, list[str]] | None = None,
    semantic_ground_truth: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    if corrected_sequence is None or not ground_truth_events:
        return {
            "schema_version": "1.0.0",
            "status": "ground_truth_required",
            "event_metrics": {
                "event_f1": None,
                "mean_segment_iou": None,
                "boundary_mae_ms": None,
                "boundary_p95_ms": None,
                "phase_boundary_mae_ms": None,
                "phase_boundary_p95_ms": None,
                "phase_boundary_valid_rate": None,
                "phase_boundary_by_name": {},
            },
            "feature_metrics": {},
            "error_budget": {
                "pose_error": None,
                "event_boundary_error": None,
                "smoothing_error": None,
                "smoothing_error_reason": "corrected_keypoints_and_ground_truth_events_required",
                "missing_value_impact": None,
            },
        }
    event_metrics = evaluate_events(predictions, ground_truth_events)
    predictions_by_id = {item["event_id"]: item for item in predictions}
    truth_by_id = {item["event_id"]: item for item in ground_truth_events}
    errors: dict[tuple[str, str], list[float]] = defaultdict(list)
    eligible: dict[tuple[str, str], int] = defaultdict(int)
    budgets: dict[str, dict[str, list[float]]] = defaultdict(
        lambda: defaultdict(list)
    )
    smoothing_reasons: dict[str, list[str]] = defaultdict(list)
    missing: dict[str, list[float]] = defaultdict(list)
    details = []
    for match in event_metrics["matches"]:
        prediction = predictions_by_id[match["prediction_event_id"]]
        truth = truth_by_id[match["ground_truth_event_id"]]
        event_code = prediction["event_code"]
        view_group = str(truth.get("view_group", "unspecified"))
        for feature_name in feature_names_by_event.get(event_code, []):
            eligible[(feature_name, view_group)] += 1
            production = compute_feature(
                model_sequence, _interval(prediction), feature_name
            )
            truth_value = compute_feature(
                corrected_sequence, _interval(truth), feature_name
            )
            truth_pose_prediction_boundary = compute_feature(
                corrected_sequence, _interval(prediction), feature_name
            )
            production_computed_value = _numeric(production.value)
            corrected_computed_value = _numeric(truth_value.value)
            counterfactual_computed_value = _numeric(
                truth_pose_prediction_boundary.value
            )
            # A feature can retain a diagnostic scalar while failing its
            # quality gate (for example, an event summary with too little
            # valid keypoint coverage).  Such a scalar must never contribute
            # to MAE/P95/Bias or make the reported valid rate look complete.
            production_value = (
                production_computed_value if production.valid else None
            )
            corrected_value = (
                corrected_computed_value if truth_value.valid else None
            )
            counterfactual_value = (
                counterfactual_computed_value
                if truth_pose_prediction_boundary.valid
                else None
            )
            smoothing_counterfactual = (
                compute_smoothing_counterfactual(truth_value)
                if truth_value.valid
                else {
                    "value": None,
                    "raw_counterfactual_value": None,
                    "matched_series": [],
                    "reason": f"ground_truth_feature_invalid:{truth_value.reason}",
                }
            )
            raw_truth_value = smoothing_counterfactual[
                "raw_counterfactual_value"
            ]
            error = (
                production_value - corrected_value
                if production_value is not None and corrected_value is not None
                else None
            )
            if error is not None:
                errors[(feature_name, view_group)].append(error)
            pose_effect = (
                production_value - counterfactual_value
                if production_value is not None and counterfactual_value is not None
                else None
            )
            boundary_effect = (
                counterfactual_value - corrected_value
                if counterfactual_value is not None and corrected_value is not None
                else None
            )
            smoothing_effect = smoothing_counterfactual["value"]
            smoothing_reasons[feature_name].append(
                smoothing_counterfactual["reason"]
            )
            for name, value in (
                ("pose_error", pose_effect),
                ("event_boundary_error", boundary_effect),
                ("smoothing_error", smoothing_effect),
            ):
                if value is not None:
                    budgets[feature_name][name].append(value)
            model_valid_fraction = float(
                production.provenance.get("valid_fraction", 1.0 if production.valid else 0.0)
            )
            truth_valid_fraction = float(
                truth_value.provenance.get("valid_fraction", 1.0 if truth_value.valid else 0.0)
            )
            missing_impact = max(0.0, truth_valid_fraction - model_valid_fraction)
            missing[feature_name].append(missing_impact)
            details.append(
                {
                    "feature_name": feature_name,
                    "unit": FEATURE_DEFINITIONS[feature_name]["unit"],
                    "view_group": view_group,
                    "prediction_event_id": prediction["event_id"],
                    "ground_truth_event_id": truth["event_id"],
                    "model_value": production_value,
                    "ground_truth_value": corrected_value,
                    "model_computed_value": production_computed_value,
                    "ground_truth_computed_value": corrected_computed_value,
                    "event_boundary_counterfactual_computed_value": (
                        counterfactual_computed_value
                    ),
                    "model_valid": bool(production.valid),
                    "ground_truth_valid": bool(truth_value.valid),
                    "event_boundary_counterfactual_valid": bool(
                        truth_pose_prediction_boundary.valid
                    ),
                    "model_reason": production.reason,
                    "ground_truth_reason": truth_value.reason,
                    "event_boundary_counterfactual_reason": (
                        truth_pose_prediction_boundary.reason
                    ),
                    "error": error,
                    "pose_counterfactual_error": pose_effect,
                    "event_boundary_counterfactual_error": boundary_effect,
                    "smoothing_counterfactual_error": smoothing_effect,
                    "smoothing_raw_counterfactual_value": raw_truth_value,
                    "smoothing_counterfactual_reason": smoothing_counterfactual[
                        "reason"
                    ],
                    "smoothing_matched_series": smoothing_counterfactual[
                        "matched_series"
                    ],
                    "missing_valid_fraction_impact": round(missing_impact, 8),
                }
            )
    context_features = context_feature_names_by_event or {}
    unknown_context_features = sorted(
        {
            feature_name
            for names in context_features.values()
            for feature_name in names
            if feature_name not in SCORING_CONTEXT_FEATURE_DEFINITIONS
        }
    )
    if unknown_context_features:
        raise ValueError(
            "unsupported scoring-context feature error evaluation: "
            + ", ".join(unknown_context_features)
        )
    target_semantics = _target_semantics_by_event(semantic_ground_truth)
    for match in event_metrics["matches"]:
        prediction = predictions_by_id[match["prediction_event_id"]]
        truth = truth_by_id[match["ground_truth_event_id"]]
        event_code = prediction["event_code"]
        view_group = str(truth.get("view_group", "unspecified"))
        for feature_name in context_features.get(event_code, []):
            if feature_name != TARGET_DIRECTION_ALIGNMENT_FEATURE_NAME:
                raise ValueError(
                    f"unsupported scoring-context feature: {feature_name}"
                )
            eligible[(feature_name, view_group)] += 1
            semantic = target_semantics.get(str(truth["event_id"]))
            target_direction, reference_confidence, target_reason = (
                _target_reference(semantic)
            )
            production_launch = compute_feature(
                model_sequence, _interval(prediction), "launch_direction_deg"
            )
            truth_launch = compute_feature(
                corrected_sequence, _interval(truth), "launch_direction_deg"
            )
            truth_pose_prediction_boundary_launch = compute_feature(
                corrected_sequence, _interval(prediction), "launch_direction_deg"
            )

            def alignment(launch: FeatureResult) -> dict[str, Any]:
                return build_target_direction_alignment_feature(
                    launch_direction_feature=_feature_payload(launch),
                    target_direction_deg=target_direction,
                    reference_confidence=reference_confidence,
                    unavailable_reason=(
                        target_reason
                        if target_reason != "valid"
                        else f"launch_direction_unavailable:{launch.reason}"
                    ),
                )

            production = alignment(production_launch)
            truth_value = alignment(truth_launch)
            truth_pose_prediction_boundary = alignment(
                truth_pose_prediction_boundary_launch
            )
            production_computed_value = _numeric(production.get("value"))
            corrected_computed_value = _numeric(truth_value.get("value"))
            counterfactual_computed_value = _numeric(
                truth_pose_prediction_boundary.get("value")
            )
            production_value = (
                production_computed_value
                if production.get("valid") is True
                else None
            )
            corrected_value = (
                corrected_computed_value
                if truth_value.get("valid") is True
                else None
            )
            counterfactual_value = (
                counterfactual_computed_value
                if truth_pose_prediction_boundary.get("valid") is True
                else None
            )
            error = (
                production_value - corrected_value
                if production_value is not None and corrected_value is not None
                else None
            )
            if error is not None:
                errors[(feature_name, view_group)].append(error)
            pose_effect = (
                production_value - counterfactual_value
                if production_value is not None
                and counterfactual_value is not None
                else None
            )
            boundary_effect = (
                counterfactual_value - corrected_value
                if counterfactual_value is not None and corrected_value is not None
                else None
            )
            smoothing_launch = (
                compute_smoothing_counterfactual(truth_launch)
                if truth_launch.valid
                else {
                    "value": None,
                    "raw_counterfactual_value": None,
                    "matched_series": [],
                    "reason": f"ground_truth_feature_invalid:{truth_launch.reason}",
                }
            )
            raw_launch_value = _numeric(
                smoothing_launch.get("raw_counterfactual_value")
            )
            raw_alignment = build_target_direction_alignment_feature(
                launch_direction_feature=(
                    {
                        **_feature_payload(truth_launch),
                        "value": raw_launch_value,
                        "valid": raw_launch_value is not None,
                    }
                    if raw_launch_value is not None
                    else None
                ),
                target_direction_deg=target_direction,
                reference_confidence=reference_confidence,
                unavailable_reason=(
                    target_reason
                    if target_reason != "valid"
                    else str(smoothing_launch.get("reason"))
                ),
            )
            raw_alignment_value = (
                _numeric(raw_alignment.get("value"))
                if raw_alignment.get("valid") is True
                else None
            )
            smoothing_effect = (
                corrected_value - raw_alignment_value
                if corrected_value is not None and raw_alignment_value is not None
                else None
            )
            smoothing_reason = (
                "computed_from_launch_direction_raw_counterfactual"
                if smoothing_effect is not None
                else str(smoothing_launch.get("reason"))
            )
            smoothing_reasons[feature_name].append(smoothing_reason)
            for component, value in (
                ("pose_error", pose_effect),
                ("event_boundary_error", boundary_effect),
                ("smoothing_error", smoothing_effect),
            ):
                if value is not None:
                    budgets[feature_name][component].append(value)
            if target_direction is None:
                missing_impact = 1.0
            else:
                model_valid_fraction = float(
                    production_launch.provenance.get(
                        "valid_fraction", 1.0 if production_launch.valid else 0.0
                    )
                )
                truth_valid_fraction = float(
                    truth_launch.provenance.get(
                        "valid_fraction", 1.0 if truth_launch.valid else 0.0
                    )
                )
                missing_impact = max(
                    0.0, truth_valid_fraction - model_valid_fraction
                )
            missing[feature_name].append(missing_impact)
            details.append(
                {
                    "feature_name": feature_name,
                    "unit": _feature_definition(feature_name)["unit"],
                    "view_group": view_group,
                    "prediction_event_id": prediction["event_id"],
                    "ground_truth_event_id": truth["event_id"],
                    "model_value": production_value,
                    "ground_truth_value": corrected_value,
                    "model_computed_value": production_computed_value,
                    "ground_truth_computed_value": corrected_computed_value,
                    "event_boundary_counterfactual_computed_value": (
                        counterfactual_computed_value
                    ),
                    "model_valid": production.get("valid") is True,
                    "ground_truth_valid": truth_value.get("valid") is True,
                    "event_boundary_counterfactual_valid": (
                        truth_pose_prediction_boundary.get("valid") is True
                    ),
                    "model_reason": str(production.get("reason")),
                    "ground_truth_reason": str(truth_value.get("reason")),
                    "event_boundary_counterfactual_reason": str(
                        truth_pose_prediction_boundary.get("reason")
                    ),
                    "error": error,
                    "pose_counterfactual_error": pose_effect,
                    "event_boundary_counterfactual_error": boundary_effect,
                    "smoothing_counterfactual_error": smoothing_effect,
                    "smoothing_raw_counterfactual_value": raw_alignment_value,
                    "smoothing_counterfactual_reason": smoothing_reason,
                    "smoothing_matched_series": smoothing_launch.get(
                        "matched_series", []
                    ),
                    "missing_valid_fraction_impact": round(missing_impact, 8),
                    "semantic_ground_truth": {
                        "available": target_direction is not None,
                        "reason": target_reason,
                        "annotation_id": (
                            semantic.get("annotation_id")
                            if isinstance(semantic, dict)
                            else None
                        ),
                        "coordinate_frame": (
                            semantic.get("value", {}).get("coordinate_frame")
                            if isinstance(semantic, dict)
                            and isinstance(semantic.get("value"), dict)
                            else None
                        ),
                        "target_direction_deg": target_direction,
                        "annotation_confidence": reference_confidence,
                    },
                }
            )
    feature_metrics = {}
    feature_names = sorted({name for name, _ in eligible})
    for feature_name in feature_names:
        by_view = {}
        combined_errors = []
        combined_eligible = 0
        for (name, view_group), count in eligible.items():
            if name != feature_name:
                continue
            values = errors[(name, view_group)]
            by_view[view_group] = _metric(values, count)
            combined_errors.extend(values)
            combined_eligible += count
        feature_metrics[feature_name] = {
            "unit": _feature_definition(feature_name)["unit"],
            "overall": _metric(combined_errors, combined_eligible),
            "by_view": by_view,
        }
    error_budget = {}
    for feature_name in feature_names:
        feature_detail_count = sum(
            item["feature_name"] == feature_name for item in details
        )
        error_budget[feature_name] = {
            component: _metric(
                budgets[feature_name][component], feature_detail_count
            )
            for component in (
                "pose_error",
                "event_boundary_error",
                "smoothing_error",
            )
        }
        smoothing_metric = error_budget[feature_name]["smoothing_error"]
        reason_counts = Counter(smoothing_reasons[feature_name])
        unavailable_reasons = {
            reason: count
            for reason, count in sorted(reason_counts.items())
            if not reason.startswith("computed_from_")
        }
        if smoothing_metric["valid_count"] == feature_detail_count:
            smoothing_status = "evaluated"
        elif smoothing_metric["valid_count"] > 0:
            smoothing_status = "partially_evaluated"
        else:
            smoothing_status = "unavailable"
        smoothing_metric.update(
            {
                "status": smoothing_status,
                "unavailable_reason_counts": unavailable_reasons,
            }
        )
        missing_values = missing[feature_name]
        error_budget[feature_name]["missing_value_impact"] = {
            "mean_valid_fraction_loss": (
                round(float(np.mean(missing_values)), 8) if missing_values else None
            ),
            "p95_valid_fraction_loss": (
                round(float(np.percentile(missing_values, 95)), 8)
                if missing_values
                else None
            ),
        }
    eligible_total = sum(
        item["overall"]["eligible_count"] for item in feature_metrics.values()
    )
    valid_total = sum(
        item["overall"]["valid_count"] for item in feature_metrics.values()
    )
    if valid_total == 0:
        evaluation_status = "insufficient_keypoint_ground_truth_coverage"
    elif valid_total < eligible_total:
        evaluation_status = "evaluated_with_incomplete_feature_truth"
    else:
        evaluation_status = "evaluated"
    clear_feature_cache(model_sequence)
    clear_feature_cache(corrected_sequence)
    return {
        "schema_version": "1.0.0",
        "status": evaluation_status,
        "ground_truth_coverage": {
            "eligible_feature_event_pairs": eligible_total,
            "valid_feature_event_pairs": valid_total,
            "valid_rate": round(valid_total / max(eligible_total, 1), 8),
            "semantics": "only explicitly annotated keypoints are ground truth; model values are never reused as truth",
        },
        "event_metrics": event_metrics,
        "feature_metrics": feature_metrics,
        "error_budget": {
            "method": "one_factor_counterfactual_differences_not_additive_shapley_decomposition",
            "features": error_budget,
        },
        "details": details,
    }
