from __future__ import annotations

import unittest
from unittest.mock import patch

import numpy as np

from rallymate_evaluation.feature_errors import (
    compute_smoothing_counterfactual,
    evaluate_feature_errors,
)
from rallymate_features.fs01_fs02_features import (
    bilateral_foot_vertical_slowdown_from_position_series,
    hip_center_lateral_variability_from_position_series,
    first_step_phase_kinematics_from_series,
    launch_foot_event_kinematics_from_series,
    post_slowdown_stance_width_from_position_series,
)
from rallymate_features.schemas import FeatureResult, PoseSequence


def _series(
    values: list[float | None],
    *,
    timestamps_ms: list[int] | None = None,
) -> list[dict[str, float | int | None]]:
    if timestamps_ms is None:
        timestamps_ms = [index * 40 for index in range(len(values))]
    if len(timestamps_ms) != len(values):
        raise ValueError("timestamps_ms must match values")
    return [
        {
            "timestamp_ms": timestamps_ms[index],
            "source_frame": index,
            "value": value,
        }
        for index, value in enumerate(values)
    ]


def _vector_series(
    values: list[list[float | None]],
    *,
    timestamps_ms: list[int],
) -> list[dict[str, object]]:
    if len(timestamps_ms) != len(values):
        raise ValueError("timestamps_ms must match values")
    return [
        {
            "timestamp_ms": timestamps_ms[index],
            "source_frame": index,
            "value": value,
        }
        for index, value in enumerate(values)
    ]


def _result(
    *,
    value: float | int | None,
    aggregation: str,
    raw_value: object,
    smoothed_value: object,
    feature_name: str = "hip_center_speed_drop_body_s",
    provenance: dict[str, object] | None = None,
) -> FeatureResult:
    return FeatureResult(
        feature_name=feature_name,
        feature_version="test-v1",
        value=value,
        unit="unit",
        confidence=1.0,
        valid=value is not None,
        reason="valid" if value is not None else "unavailable",
        source_frames=[0, 3],
        raw_value=raw_value,
        smoothed_value=smoothed_value,
        provenance={"aggregation": aggregation, **(provenance or {})},
    )


class SmoothingCounterfactualPureTests(unittest.TestCase):
    def test_fs01_m04_family_replays_shared_raw_slowdown_anchor(self) -> None:
        timestamps = [0, 50, 140, 260, 410, 570]
        left_values = [
            [0.30, 0.80], [0.30, 0.75], [0.30, 0.78],
            [0.29, 0.90], [0.28, 0.96], [0.28, 0.96],
        ]
        right_values = [
            [0.70, 0.80], [0.70, 0.76], [0.70, 0.80],
            [0.72, 0.88], [0.73, 0.93], [0.73, 0.93],
        ]
        hip_values = [
            [0.50, 0.55], [0.51, 0.55], [0.52, 0.55],
            [0.54, 0.55], [0.55, 0.55], [0.56, 0.55],
        ]
        timestamp_array = np.asarray(timestamps, dtype=np.int64)
        indexes = np.arange(len(timestamps), dtype=np.int64)
        left_array = np.asarray(left_values, dtype=np.float64)
        right_array = np.asarray(right_values, dtype=np.float64)
        hip_array = np.asarray(hip_values, dtype=np.float64)
        timing_value = bilateral_foot_vertical_slowdown_from_position_series(
            timestamp_array, left_array, right_array, indexes, 0.5
        )[0]
        width_value = post_slowdown_stance_width_from_position_series(
            timestamp_array, left_array, right_array, indexes, 0.5
        )[0]
        variability_value = hip_center_lateral_variability_from_position_series(
            timestamp_array, left_array, right_array, hip_array, indexes, 0.5
        )[0]
        left = _vector_series(left_values, timestamps_ms=timestamps)
        right = _vector_series(right_values, timestamps_ms=timestamps)
        hip = _vector_series(hip_values, timestamps_ms=timestamps)
        common = {
            "body_scale": 0.5,
            "evidence_contract": "fs01_m04_raw_and_prepared_slowdown_series_v1",
        }
        results = [
            _result(
                feature_name="bilateral_foot_vertical_slowdown_time_offset_ms",
                value=timing_value,
                aggregation="absolute_event_local_vertical_slowdown_time_difference",
                raw_value={"left_foot_position": left, "right_foot_position": right},
                smoothed_value={"series": {"left_foot_position": left, "right_foot_position": right}},
                provenance=common,
            ),
            _result(
                feature_name="post_slowdown_stance_width_body",
                value=width_value,
                aggregation="post_bilateral_vertical_slowdown_median",
                raw_value={"left_foot_position": left, "right_foot_position": right},
                smoothed_value={"series": {"left_foot_position": left, "right_foot_position": right}},
                provenance=common,
            ),
            _result(
                feature_name="hip_center_lateral_variability_body",
                value=variability_value,
                aggregation=(
                    "post_bilateral_vertical_slowdown_population_standard_"
                    "deviation_with_boundary_censored_nearest_pre_sample"
                ),
                raw_value={
                    "left_foot_position": left,
                    "right_foot_position": right,
                    "hip_position": hip,
                },
                smoothed_value={
                    "series": {
                        "left_foot_position": left,
                        "right_foot_position": right,
                        "hip_position": hip,
                    }
                },
                provenance=common,
            ),
        ]

        counterfactuals = [compute_smoothing_counterfactual(item) for item in results]

        for expected, counterfactual in zip(
            (timing_value, width_value, variability_value),
            counterfactuals,
        ):
            self.assertIsNotNone(expected)
            self.assertAlmostEqual(counterfactual["raw_counterfactual_value"], expected)
            self.assertAlmostEqual(counterfactual["value"], 0.0)
            self.assertIn("production_fs01", counterfactual["reason"])

    def test_fs01_m04_replay_rejects_missing_anchor_position(self) -> None:
        timestamps = [0, 50, 140, 260]
        left = _vector_series(
            [[0.3, 0.8], [0.3, 0.75], [0.3, 0.9], [0.3, 0.95]],
            timestamps_ms=timestamps,
        )
        right = _vector_series(
            [[0.7, 0.8], [0.7, 0.75], [0.7, 0.9], [0.7, 0.95]],
            timestamps_ms=timestamps,
        )
        result = _result(
            feature_name="post_slowdown_stance_width_body",
            value=0.4,
            aggregation="post_bilateral_vertical_slowdown_median",
            raw_value={"left_foot_position": left},
            smoothed_value={
                "series": {
                    "left_foot_position": left,
                    "right_foot_position": right,
                }
            },
            provenance={"body_scale": 0.5},
        )

        counterfactual = compute_smoothing_counterfactual(result)

        self.assertIsNone(counterfactual["raw_counterfactual_value"])
        self.assertEqual(
            counterfactual["reason"],
            "required_fs01_m04_series_counterfactual_unavailable",
        )

    def test_fs02_m04_family_replays_one_shared_launch_kinematic_contract(self) -> None:
        timestamps = [0, 70, 190, 360, 590]
        hip_values = [[0.0, 0.5], [0.1, 0.5], [0.25, 0.5], [0.45, 0.5], [0.7, 0.5]]
        left_values = [[-0.2, 1.0], [-0.18, 1.0], [-0.15, 1.0], [-0.1, 1.0], [-0.05, 1.0]]
        right_values = [[0.2, 1.0], [0.25, 1.0], [0.4, 1.0], [0.65, 1.0], [0.95, 1.0]]
        timestamp_array = np.asarray(timestamps, dtype=np.int64)
        left_array = np.asarray(left_values, dtype=np.float64)
        right_array = np.asarray(right_values, dtype=np.float64)
        left_velocity = np.gradient(
            left_array, timestamp_array.astype(np.float64) / 1000.0, axis=0
        )
        right_velocity = np.gradient(
            right_array, timestamp_array.astype(np.float64) / 1000.0, axis=0
        )
        components = launch_foot_event_kinematics_from_series(
            timestamp_array,
            np.asarray(hip_values, dtype=np.float64),
            left_array,
            right_array,
            left_velocity,
            right_velocity,
            np.arange(len(timestamps)),
            0.5,
        )
        raw = {
            "hip_position": _vector_series(hip_values, timestamps_ms=timestamps),
            "left_foot_position": _vector_series(left_values, timestamps_ms=timestamps),
            "right_foot_position": _vector_series(right_values, timestamps_ms=timestamps),
            "left_foot_velocity": _vector_series(left_velocity.tolist(), timestamps_ms=timestamps),
            "right_foot_velocity": _vector_series(right_velocity.tolist(), timestamps_ms=timestamps),
        }
        cases = {
            "larger_positive_foot_displacement_along_launch_direction": (
                "launch_side_code",
                components["launch_code"],
            ),
            "selected_launch_foot_peak_speed": (
                "launch_foot_speed_peak_body_s",
                components["speed_peak_body_s"],
            ),
            "launch_minus_other_foot_projected_event_displacement": (
                "launch_foot_relative_displacement_body",
                components["relative_displacement_body"],
            ),
            "longest_half_peak_positive_projected_velocity_run": (
                "launch_foot_motion_duration_ms",
                components["motion_duration_ms"],
            ),
        }
        for aggregation, (feature_name, expected) in cases.items():
            with self.subTest(aggregation=aggregation):
                result = _result(
                    feature_name=feature_name,
                    value=expected,
                    aggregation=aggregation,
                    raw_value=raw,
                    smoothed_value={"series": raw},
                    provenance={"body_scale": 0.5},
                )
                counterfactual = compute_smoothing_counterfactual(result)
                self.assertIsNotNone(expected)
                self.assertAlmostEqual(
                    counterfactual["raw_counterfactual_value"], expected
                )
                self.assertAlmostEqual(counterfactual["value"], 0.0)
                self.assertIn("production_fs02_m04", counterfactual["reason"])

    def test_fs02_m04_replay_rejects_missing_velocity_series(self) -> None:
        timestamps = [0, 50, 140, 260]
        position = _vector_series(
            [[0.0, 0.0], [0.1, 0.0], [0.2, 0.0], [0.3, 0.0]],
            timestamps_ms=timestamps,
        )
        raw = {
            "hip_position": position,
            "left_foot_position": position,
            "right_foot_position": position,
            "left_foot_velocity": position,
        }
        result = _result(
            feature_name="launch_foot_speed_peak_body_s",
            value=1.0,
            aggregation="selected_launch_foot_peak_speed",
            raw_value=raw,
            smoothed_value={"series": raw},
            provenance={"body_scale": 0.5},
        )
        counterfactual = compute_smoothing_counterfactual(result)
        self.assertIsNone(counterfactual["raw_counterfactual_value"])
        self.assertEqual(
            counterfactual["reason"],
            "required_fs02_m04_series_counterfactual_unavailable",
        )

    def test_fs02_m05_family_replays_one_fixed_phase_kinematic_contract(self) -> None:
        timestamps = [0, 70, 190, 360, 590, 760, 940]
        hip_values = [[0.0, 0.5], [0.1, 0.5], [0.25, 0.5], [0.45, 0.5], [0.7, 0.5], [0.85, 0.5], [0.95, 0.5]]
        left_values = [[-0.2, 1.0], [-0.18, 1.0], [-0.15, 1.0], [-0.1, 1.0], [-0.05, 1.0], [0.0, 1.0], [0.04, 1.0]]
        right_values = [[0.2, 1.0], [0.25, 1.0], [0.45, 1.0], [0.75, 1.0], [1.0, 1.0], [1.05, 1.0], [1.06, 1.0]]
        timestamp_array = np.asarray(timestamps, dtype=np.int64)
        hip_array = np.asarray(hip_values, dtype=np.float64)
        left_array = np.asarray(left_values, dtype=np.float64)
        right_array = np.asarray(right_values, dtype=np.float64)
        hip_velocity = np.gradient(
            hip_array, timestamp_array.astype(np.float64) / 1000.0, axis=0
        )
        left_velocity = np.gradient(
            left_array, timestamp_array.astype(np.float64) / 1000.0, axis=0
        )
        right_velocity = np.gradient(
            right_array, timestamp_array.astype(np.float64) / 1000.0, axis=0
        )
        components = first_step_phase_kinematics_from_series(
            timestamp_array,
            hip_array,
            hip_velocity,
            left_array,
            right_array,
            left_velocity,
            right_velocity,
            np.arange(len(timestamps)),
            0.5,
            {"first_step_slowdown_proxy_ms": 360},
        )
        raw = {
            "hip_position": _vector_series(hip_values, timestamps_ms=timestamps),
            "hip_velocity": _vector_series(hip_velocity.tolist(), timestamps_ms=timestamps),
            "left_foot_position": _vector_series(left_values, timestamps_ms=timestamps),
            "right_foot_position": _vector_series(right_values, timestamps_ms=timestamps),
            "left_foot_velocity": _vector_series(left_velocity.tolist(), timestamps_ms=timestamps),
            "right_foot_velocity": _vector_series(right_velocity.tolist(), timestamps_ms=timestamps),
        }
        summary = {
            "post_step_anchor_source": "event_key_phase",
            "event_phase_timestamp_ms": 360,
            "phase_anchor_status": "matched_event_phase_to_source_sample",
        }
        cases = {
            "peak_speed_minus_post_peak_late_median": (
                "launch_foot_speed_drop_body_s",
                components["speed_drop_body_s"],
            ),
            "selected_launch_foot_projected_event_displacement": (
                "first_step_displacement_body",
                components["first_step_displacement_body"],
            ),
            "post_event_first_step_slowdown_phase_mean_velocity_cosine": (
                "post_step_hip_direction_consistency",
                components["post_direction_consistency"],
            ),
            "event_first_step_slowdown_phase_to_alignment_envelope_start_time_delta": (
                "launch_foot_slowdown_to_post_hip_direction_ms",
                components["slowdown_to_post_direction_ms"],
            ),
            "post_event_first_step_slowdown_phase_median": (
                "post_step_stance_width_body",
                components["post_stance_width_body"],
            ),
        }
        for aggregation, (feature_name, expected) in cases.items():
            with self.subTest(aggregation=aggregation):
                self.assertIsNotNone(expected)
                result = _result(
                    feature_name=feature_name,
                    value=expected,
                    aggregation=aggregation,
                    raw_value=raw,
                    smoothed_value={"series": raw, "summary": summary},
                    provenance={"body_scale": 0.5},
                )
                counterfactual = compute_smoothing_counterfactual(result)
                self.assertAlmostEqual(
                    counterfactual["raw_counterfactual_value"], expected
                )
                self.assertAlmostEqual(counterfactual["value"], 0.0)
                self.assertEqual(len(counterfactual["matched_series"]), 6)
                self.assertIn("production_fs02_m05", counterfactual["reason"])

    def test_fs02_m05_post_phase_replay_rejects_missing_phase_evidence(self) -> None:
        timestamps = [0, 50, 140, 260]
        position = _vector_series(
            [[0.0, 0.0], [0.1, 0.0], [0.2, 0.0], [0.3, 0.0]],
            timestamps_ms=timestamps,
        )
        raw = {
            "hip_position": position,
            "hip_velocity": position,
            "left_foot_position": position,
            "right_foot_position": position,
            "left_foot_velocity": position,
            "right_foot_velocity": position,
        }
        result = _result(
            feature_name="post_step_hip_direction_consistency",
            value=0.75,
            aggregation=(
                "post_event_first_step_slowdown_phase_mean_velocity_cosine"
            ),
            raw_value=raw,
            smoothed_value={"series": raw, "summary": {}},
            provenance={"body_scale": 0.5},
        )
        counterfactual = compute_smoothing_counterfactual(result)
        self.assertIsNone(counterfactual["raw_counterfactual_value"])
        self.assertEqual(
            counterfactual["reason"],
            "required_fs02_m05_series_or_phase_counterfactual_unavailable",
        )

    def test_fs01_m03_family_replays_all_four_raw_evidence_contracts(self) -> None:
        timestamps = [0, 50, 140, 260, 410]
        left_position = _vector_series(
            [[0.3, 1.0], [0.3, 0.9], [0.3, 0.7], [0.3, 0.9], [0.3, 1.0]],
            timestamps_ms=timestamps,
        )
        right_position = _vector_series(
            [[0.7, 1.0], [0.7, 0.95], [0.7, 0.8], [0.7, 0.92], [0.7, 1.0]],
            timestamps_ms=timestamps,
        )
        left_velocity = _vector_series(
            [[0.0, -1.0], [0.0, -2.0], [0.0, -0.5], [0.0, 0.5], [0.0, 1.0]],
            timestamps_ms=timestamps,
        )
        right_velocity = _vector_series(
            [[0.0, -0.5], [0.0, -1.0], [0.0, -3.0], [0.0, 0.5], [0.0, 1.0]],
            timestamps_ms=timestamps,
        )
        hip_position = _vector_series(
            [[0.5, 0.60], [0.5, 0.58], [0.5, 0.53], [0.5, 0.49], [0.5, 0.48]],
            timestamps_ms=timestamps,
        )
        common = {
            "body_scale": 0.5,
            "evidence_contract": "fs01_m03_raw_and_prepared_kinematic_series_v1",
        }
        results = [
            _result(
                feature_name="bilateral_foot_rise_min_body",
                value=0.35,
                aggregation="minimum_of_left_and_right_event_local_upward_excursion",
                raw_value={
                    "left_foot_position": left_position,
                    "right_foot_position": right_position,
                },
                smoothed_value={
                    "series": {
                        "left_foot_position": left_position,
                        "right_foot_position": right_position,
                    }
                },
                provenance=common,
            ),
            _result(
                feature_name="bilateral_foot_rise_synchrony_ms",
                value=90,
                aggregation="absolute_peak_upward_velocity_time_difference",
                raw_value={
                    "left_foot_velocity": left_velocity,
                    "right_foot_velocity": right_velocity,
                },
                smoothed_value={
                    "series": {
                        "left_foot_velocity": left_velocity,
                        "right_foot_velocity": right_velocity,
                    }
                },
                provenance=common,
            ),
            _result(
                feature_name="bilateral_foot_rise_proxy_duration_ms",
                value=105,
                aggregation="longest_simultaneous_half_peak_upward_excursion_run",
                raw_value={
                    "left_foot_position": left_position,
                    "right_foot_position": right_position,
                },
                smoothed_value={
                    "series": {
                        "left_foot_position": left_position,
                        "right_foot_position": right_position,
                    }
                },
                provenance=common,
            ),
            _result(
                feature_name="hip_center_vertical_velocity_body_s",
                value=1.0,
                aggregation="peak_positive_image_up_velocity",
                raw_value={"hip_position": hip_position},
                smoothed_value={"series": {"hip_position": hip_position}},
                provenance=common,
            ),
        ]

        counterfactuals = [compute_smoothing_counterfactual(item) for item in results]

        for counterfactual in counterfactuals:
            self.assertAlmostEqual(counterfactual["value"], 0.0)
            self.assertTrue(counterfactual["matched_series"])
            self.assertIn("production_fs01", counterfactual["reason"])
        self.assertAlmostEqual(
            counterfactuals[0]["raw_counterfactual_value"], 0.35
        )
        self.assertEqual(counterfactuals[1]["raw_counterfactual_value"], 90.0)
        self.assertEqual(counterfactuals[2]["raw_counterfactual_value"], 105.0)
        self.assertAlmostEqual(
            counterfactuals[3]["raw_counterfactual_value"],
            1.0,
        )

    def test_fs01_m03_replay_rejects_shifted_prepared_grid(self) -> None:
        timestamps = [0, 50, 140, 260]
        left = _vector_series(
            [[0.3, 1.0], [0.3, 0.9], [0.3, 0.7], [0.3, 0.9]],
            timestamps_ms=timestamps,
        )
        right = _vector_series(
            [[0.7, 1.0], [0.7, 0.95], [0.7, 0.8], [0.7, 0.92]],
            timestamps_ms=timestamps,
        )
        shifted = _vector_series(
            [[0.7, 1.0], [0.7, 0.95], [0.7, 0.8], [0.7, 0.92]],
            timestamps_ms=[0, 50, 141, 260],
        )
        result = _result(
            feature_name="bilateral_foot_rise_min_body",
            value=0.3,
            aggregation="minimum_of_left_and_right_event_local_upward_excursion",
            raw_value={
                "left_foot_position": left,
                "right_foot_position": right,
            },
            smoothed_value={
                "series": {
                    "left_foot_position": left,
                    "right_foot_position": shifted,
                }
            },
            provenance={"body_scale": 0.5},
        )

        counterfactual = compute_smoothing_counterfactual(result)

        self.assertIsNone(counterfactual["raw_counterfactual_value"])
        self.assertEqual(
            counterfactual["reason"],
            "required_fs01_m03_series_counterfactual_unavailable",
        )
    def test_hip_acceleration_projection_replays_raw_direction_and_acceleration(self) -> None:
        timestamps = [0, 50, 150, 300]
        raw_position = _vector_series(
            [[0.0, 0.5], [0.05, 0.5], [0.15, 0.5], [0.30, 0.5]],
            timestamps_ms=timestamps,
        )
        raw_acceleration = _vector_series(
            [[0.5, 0.0], [1.0, 0.0], [2.0, 0.0], [3.0, 0.0]],
            timestamps_ms=timestamps,
        )
        result = _result(
            feature_name="hip_acceleration_along_launch_direction_body_s2",
            value=4.0,
            aggregation="peak_positive_projection_on_event_launch_direction",
            raw_value={
                "hip_position": raw_position,
                "hip_acceleration_xy_body_s2": raw_acceleration,
            },
            smoothed_value={
                "series": {
                    "hip_position": raw_position,
                    "hip_acceleration_xy_body_s2": raw_acceleration,
                    "hip_acceleration_along_launch_direction_body_s2": _series(
                        [1.0, 2.0, 3.0, 4.0], timestamps_ms=timestamps
                    ),
                }
            },
        )

        counterfactual = compute_smoothing_counterfactual(result)

        self.assertAlmostEqual(counterfactual["raw_counterfactual_value"], 3.0)
        self.assertAlmostEqual(counterfactual["value"], 1.0)
        self.assertEqual(
            counterfactual["reason"],
            "computed_from_production_hip_acceleration_projection_pure_function",
        )
        self.assertEqual(len(counterfactual["matched_series"]), 2)

    def test_stability_duration_recomputes_adaptive_envelope_on_raw_series(self) -> None:
        timestamps = [0, 40, 100, 180, 300]
        raw = {
            "speed": _series(
                [4.0, 3.0, 0.5, 0.4, 2.0], timestamps_ms=timestamps
            ),
            "absolute_angular_velocity": _series(
                [20.0, 15.0, 1.0, 1.0, 10.0], timestamps_ms=timestamps
            ),
        }
        result = _result(
            feature_name="stability_duration_ms",
            value=40,
            aggregation="longest_contiguous_stable_run",
            raw_value=raw,
            smoothed_value={"series": raw},
        )

        counterfactual = compute_smoothing_counterfactual(result)

        self.assertEqual(counterfactual["raw_counterfactual_value"], 150.0)
        self.assertEqual(counterfactual["value"], -110.0)
        self.assertEqual(
            counterfactual["reason"],
            "computed_from_production_stability_pure_function",
        )
        self.assertEqual(len(counterfactual["matched_series"]), 2)

    def test_fs09_braking_phase_timing_replays_raw_timestamp_series(self) -> None:
        timestamps = [0, 100, 250, 500, 900]
        raw = {
            "left_ankle_speed": _series(
                [5.0, 4.0, 3.0, 2.0, 1.0], timestamps_ms=timestamps
            ),
            "right_ankle_speed": _series(
                [2.0, 2.0, 2.0, 2.0, 2.0], timestamps_ms=timestamps
            ),
            "hip_speed": _series(
                [5.0, 4.0, 2.0, 1.0, 1.0], timestamps_ms=timestamps
            ),
        }
        result = _result(
            feature_name="braking_ankle_slowdown_to_hip_deceleration_ms",
            value=130,
            aggregation="peak_time_delta",
            raw_value=raw,
            smoothed_value={
                "series": {
                    "left_ankle_speed": raw["left_ankle_speed"],
                    "right_ankle_speed": raw["right_ankle_speed"],
                    "left_ankle_slowdown": _series(
                        [1.0, 2.0, 3.0, 2.0, 1.0], timestamps_ms=timestamps
                    ),
                    "right_ankle_slowdown": _series(
                        [0.0, 0.0, 0.0, 0.0, 0.0], timestamps_ms=timestamps
                    ),
                    "hip_slowdown": _series(
                        [0.0, 1.0, 4.0, 2.0, 0.0], timestamps_ms=timestamps
                    ),
                }
            },
        )

        counterfactual = compute_smoothing_counterfactual(result)

        self.assertEqual(counterfactual["raw_counterfactual_value"], 100.0)
        self.assertEqual(counterfactual["value"], 30.0)
        self.assertEqual(
            counterfactual["reason"],
            "computed_from_production_fs09_braking_phase_timing_pure_function",
        )
        self.assertEqual(len(counterfactual["matched_series"]), 3)

    def test_fs09_support_phase_timing_replays_raw_timestamp_series(self) -> None:
        timestamps = [0, 100, 250, 500, 900]
        raw = {
            "left_ankle_speed": _series(
                [3.0, 2.0, 0.2, 0.1, 0.1], timestamps_ms=timestamps
            ),
            "right_ankle_speed": _series(
                [3.0, 2.0, 0.3, 0.1, 0.1], timestamps_ms=timestamps
            ),
            "hip_speed": _series(
                [5.0, 4.0, 2.0, 1.0, 1.0], timestamps_ms=timestamps
            ),
        }
        result = _result(
            feature_name="hip_deceleration_to_double_support_proxy_ms",
            value=200,
            aggregation="phase_proxy_start_time_delta",
            raw_value=raw,
            smoothed_value={
                "series": {
                    "left_ankle_speed": raw["left_ankle_speed"],
                    "right_ankle_speed": raw["right_ankle_speed"],
                    "hip_slowdown": _series(
                        [0.0, 5.0, 1.0, 0.0, 0.0], timestamps_ms=timestamps
                    ),
                    "simultaneous_low_motion_mask": _series(
                        [0.0, 0.0, 1.0, 1.0, 1.0], timestamps_ms=timestamps
                    ),
                }
            },
        )

        counterfactual = compute_smoothing_counterfactual(result)

        self.assertEqual(counterfactual["raw_counterfactual_value"], 150.0)
        self.assertEqual(counterfactual["value"], 50.0)
        self.assertEqual(
            counterfactual["reason"],
            "computed_from_production_fs09_support_phase_timing_pure_function",
        )
        self.assertEqual(len(counterfactual["matched_series"]), 3)

    def test_fs09_phase_timing_rejects_misaligned_raw_grids(self) -> None:
        timestamps = [0, 100, 250, 500]
        result = _result(
            feature_name="hip_deceleration_to_double_support_proxy_ms",
            value=200,
            aggregation="phase_proxy_start_time_delta",
            raw_value={
                "left_ankle_speed": _series(
                    [3.0, 2.0, 0.2, 0.1], timestamps_ms=timestamps
                ),
                "right_ankle_speed": _series(
                    [3.0, 2.0, 0.3, 0.1], timestamps_ms=[0, 100, 260, 500]
                ),
                "hip_speed": _series(
                    [5.0, 4.0, 2.0, 1.0], timestamps_ms=timestamps
                ),
            },
            smoothed_value={
                "series": {
                    "left_ankle_speed": _series(
                        [3.0, 2.0, 0.2, 0.1], timestamps_ms=timestamps
                    )
                }
            },
        )

        counterfactual = compute_smoothing_counterfactual(result)

        self.assertIsNone(counterfactual["raw_counterfactual_value"])
        self.assertEqual(
            counterfactual["reason"],
            "derived_timing_series_counterfactual_not_reconstructable",
        )

    def test_launch_direction_replays_raw_vector_with_production_function(self) -> None:
        timestamps = [0, 50, 150, 300]
        raw_hip = _vector_series(
            [[0.0, 0.5], [0.05, 0.5], [0.15, 0.5], [0.30, 0.5]],
            timestamps_ms=timestamps,
        )
        smoothed_hip = _vector_series(
            [[0.0, 0.5], [0.0, 0.45], [0.0, 0.35], [0.0, 0.20]],
            timestamps_ms=timestamps,
        )
        result = _result(
            feature_name="launch_direction_deg",
            value=90.0,
            aggregation="event_hip_displacement_velocity_composite_direction",
            raw_value={"hip_position": raw_hip},
            smoothed_value={"series": {"hip_position": smoothed_hip}},
        )

        counterfactual = compute_smoothing_counterfactual(result)

        self.assertAlmostEqual(counterfactual["raw_counterfactual_value"], 0.0)
        self.assertAlmostEqual(counterfactual["value"], 90.0)
        self.assertEqual(
            counterfactual["reason"],
            "computed_from_production_direction_pure_function",
        )
        self.assertEqual(
            counterfactual["matched_series"][0]["transform"],
            "production_launch_direction_from_raw_hip_position_timestamp_ms",
        )

    def test_launch_direction_rejects_malformed_raw_vector_without_zero_fill(self) -> None:
        timestamps = [0, 50, 150, 300]
        result = _result(
            feature_name="launch_direction_deg",
            value=90.0,
            aggregation="event_hip_displacement_velocity_composite_direction",
            raw_value={
                "hip_position": _vector_series(
                    [[0.0, 0.5], [0.05, None], [0.15], [0.30, 0.5]],
                    timestamps_ms=timestamps,
                )
            },
            smoothed_value={
                "series": {
                    "hip_position": _vector_series(
                        [[0.0, 0.5], [0.0, 0.45], [0.0, 0.35], [0.0, 0.20]],
                        timestamps_ms=timestamps,
                    )
                }
            },
        )

        counterfactual = compute_smoothing_counterfactual(result)

        self.assertIsNone(counterfactual["raw_counterfactual_value"])
        self.assertEqual(
            counterfactual["reason"],
            "raw_counterfactual_unavailable_for_aggregation:"
            "event_hip_displacement_velocity_composite_direction",
        )

    def test_exact_named_series_replays_event_aggregation(self) -> None:
        result = _result(
            value=1.5,
            aggregation="event_early_median_minus_late_median",
            raw_value={"hip_speed": _series([4.0, 3.0, 2.0, 1.0])},
            smoothed_value={
                "series": {
                    "hip_speed": _series([3.5, 3.0, 2.0, 1.5]),
                },
                "summary": {"diagnostic": True},
            },
        )
        counterfactual = compute_smoothing_counterfactual(result)
        self.assertEqual(counterfactual["raw_counterfactual_value"], 2.0)
        self.assertEqual(counterfactual["value"], -0.5)
        self.assertEqual(
            counterfactual["reason"], "computed_from_exact_series_match"
        )
        self.assertEqual(
            counterfactual["matched_series"],
            [
                {
                    "raw": "hip_speed",
                    "smoothed": "hip_speed",
                    "transform": "identity",
                }
            ],
        )

    def test_documented_absolute_series_alias_is_matchable(self) -> None:
        result = _result(
            value=-1.5,
            aggregation="event_late_absolute_median_minus_early_absolute_median",
            raw_value={
                "shoulder_hip_angular_velocity": _series(
                    [-8.0, -4.0, 2.0, 4.0]
                )
            },
            smoothed_value={
                "series": {
                    "absolute_angular_velocity": _series(
                        [6.0, 5.0, 4.0, 4.0]
                    )
                }
            },
        )
        counterfactual = compute_smoothing_counterfactual(result)
        self.assertEqual(counterfactual["raw_counterfactual_value"], -3.0)
        self.assertEqual(counterfactual["value"], 1.5)
        self.assertEqual(
            counterfactual["reason"],
            "computed_from_compatible_series_match",
        )
        self.assertEqual(
            counterfactual["matched_series"][0]["transform"],
            "absolute_value",
        )

    def test_supported_multi_series_selection_is_replayed_from_raw(self) -> None:
        result = _result(
            value=2.0,
            aggregation="selected_ankle_early_median_minus_late_median",
            raw_value={
                "left_ankle_speed": _series([5.0, 4.0, 2.0, 1.0]),
                "right_ankle_speed": _series([3.0, 3.0, 2.5, 2.0]),
            },
            smoothed_value={
                "series": {
                    "left_ankle_speed": _series([4.0, 3.5, 2.0, 1.5]),
                    "right_ankle_speed": _series([3.0, 2.8, 2.4, 2.1]),
                }
            },
        )
        counterfactual = compute_smoothing_counterfactual(result)
        self.assertEqual(counterfactual["raw_counterfactual_value"], 3.0)
        self.assertEqual(counterfactual["value"], -1.0)
        self.assertEqual(
            counterfactual["reason"],
            "computed_from_supported_multi_series_match",
        )
        self.assertEqual(len(counterfactual["matched_series"]), 2)

    def test_fs09_v2_side_replays_local_slowdown_fallback(self) -> None:
        timestamps = [0, 31, 75, 122, 181, 247]
        raw = {
            # Both net drops are negative, but left contains an observable
            # local slowdown while right rises monotonically.
            "left_ankle_speed": _series(
                [1.0, 4.0, 3.0, 2.0, 5.0, 6.0],
                timestamps_ms=timestamps,
            ),
            "right_ankle_speed": _series(
                [1.0, 2.0, 3.0, 4.0, 5.0, 6.0],
                timestamps_ms=timestamps,
            ),
        }
        smoothed = {"series": raw}
        side = _result(
            feature_name="braking_side_code",
            value=-1,
            aggregation="positive_net_drop_then_positive_local_slowdown",
            raw_value=raw,
            smoothed_value=smoothed,
        )
        selected_drop = _result(
            feature_name="braking_ankle_speed_drop_body_s",
            value=-3.0,
            aggregation="selected_ankle_early_median_minus_late_median",
            raw_value=raw,
            smoothed_value=smoothed,
        )

        side_counterfactual = compute_smoothing_counterfactual(side)
        drop_counterfactual = compute_smoothing_counterfactual(selected_drop)

        self.assertEqual(side_counterfactual["raw_counterfactual_value"], -1.0)
        self.assertEqual(side_counterfactual["value"], 0.0)
        self.assertEqual(drop_counterfactual["raw_counterfactual_value"], -3.0)
        self.assertEqual(drop_counterfactual["value"], 0.0)
        self.assertEqual(
            side_counterfactual["reason"],
            "computed_from_supported_multi_series_match",
        )

    def test_fs09_v2_indeterminate_code_zero_is_not_missing(self) -> None:
        raw = {
            "left_ankle_speed": _series([1.0, 2.0, 3.0, 4.0, 5.0, 6.0]),
            "right_ankle_speed": _series([1.0, 2.0, 3.0, 4.0, 5.0, 6.0]),
        }
        result = _result(
            feature_name="braking_side_code",
            value=0,
            aggregation="positive_net_drop_then_positive_local_slowdown",
            raw_value=raw,
            smoothed_value={"series": raw},
        )

        counterfactual = compute_smoothing_counterfactual(result)

        self.assertEqual(counterfactual["raw_counterfactual_value"], 0.0)
        self.assertEqual(counterfactual["value"], 0.0)
        self.assertEqual(
            counterfactual["reason"],
            "computed_from_supported_multi_series_match",
        )

    def test_fs02_drive_side_and_selected_knee_replay_raw_flexion(self) -> None:
        timestamps = [0, 40, 100, 160]
        raw = {
            "left_knee_flexion_deg": _series(
                [60.0, 56.0, 50.0, 45.0], timestamps_ms=timestamps
            ),
            "right_knee_flexion_deg": _series(
                [60.0, 58.0, 57.0, 56.0], timestamps_ms=timestamps
            ),
        }
        smoothed = {
            "series": {
                "left_knee_extension_velocity_deg_s": _series(
                    [80.0, 80.0, 75.0, 70.0], timestamps_ms=timestamps
                ),
                "right_knee_extension_velocity_deg_s": _series(
                    [40.0, 35.0, 25.0, 20.0], timestamps_ms=timestamps
                ),
            }
        }
        side = _result(
            feature_name="drive_side_code",
            value=-1,
            aggregation="larger_event_local_knee_extension_velocity",
            raw_value=raw,
            smoothed_value=smoothed,
        )
        selected = _result(
            feature_name="support_knee_extension_velocity_deg_s",
            value=80.0,
            aggregation=(
                "selected_positive_drive_side_or_strongest_observed_"
                "knee_extension_velocity"
            ),
            raw_value=raw,
            smoothed_value=smoothed,
        )

        side_counterfactual = compute_smoothing_counterfactual(side)
        selected_counterfactual = compute_smoothing_counterfactual(selected)

        self.assertEqual(side_counterfactual["raw_counterfactual_value"], -1.0)
        self.assertEqual(side_counterfactual["value"], 0.0)
        self.assertAlmostEqual(
            selected_counterfactual["raw_counterfactual_value"], 100.0
        )
        self.assertAlmostEqual(selected_counterfactual["value"], -20.0)
        self.assertEqual(
            selected_counterfactual["matched_series"][0]["transform"],
            "negative_irregular_derivative_timestamp_ms",
        )

    def test_fs02_drive_to_moving_foot_timing_replays_timestamps(self) -> None:
        timestamps = [0, 40, 100, 160]
        raw = {
            "left_knee_extension_velocity_deg_s": _series(
                [1.0, 3.0, 2.0, 1.0], timestamps_ms=timestamps
            ),
            "right_knee_extension_velocity_deg_s": _series(
                [1.0, 2.0, 1.0, 0.0], timestamps_ms=timestamps
            ),
            "left_foot_upward_velocity_body_s": _series(
                [0.0, 1.0, 2.0, 1.0], timestamps_ms=timestamps
            ),
            "right_foot_upward_velocity_body_s": _series(
                [0.0, 1.0, 4.0, 2.0], timestamps_ms=timestamps
            ),
        }
        result = _result(
            feature_name="support_drive_to_moving_foot_rise_proxy_ms",
            value=80,
            aggregation=(
                "moving_foot_peak_up_velocity_time_minus_positive_drive_or_"
                "strongest_observed_knee_peak_time"
            ),
            raw_value=raw,
            smoothed_value={"series": raw},
        )

        counterfactual = compute_smoothing_counterfactual(result)

        self.assertEqual(counterfactual["raw_counterfactual_value"], 60.0)
        self.assertEqual(counterfactual["value"], 20.0)
        self.assertEqual(len(counterfactual["matched_series"]), 4)
        self.assertEqual(
            counterfactual["reason"],
            "computed_from_supported_multi_series_match",
        )

    def test_unmatched_derived_series_is_null_with_reason(self) -> None:
        result = _result(
            value=80,
            aggregation="phase_proxy_start_time_delta",
            raw_value={"hip_speed": _series([4.0, 3.0, 2.0, 1.0])},
            smoothed_value={
                "series": {
                    "hip_deceleration": _series([0.0, 1.0, 2.0, 1.0]),
                    "simultaneous_low_motion_mask": _series(
                        [0.0, 0.0, 1.0, 1.0]
                    ),
                }
            },
        )
        counterfactual = compute_smoothing_counterfactual(result)
        self.assertIsNone(counterfactual["value"])
        self.assertIsNone(counterfactual["raw_counterfactual_value"])
        self.assertEqual(counterfactual["matched_series"], [])
        self.assertEqual(
            counterfactual["reason"],
            "derived_timing_series_counterfactual_not_reconstructable",
        )


class SmoothingBudgetContractTests(unittest.TestCase):
    @staticmethod
    def _sequence() -> PoseSequence:
        return PoseSequence(
            timestamp_ms=np.asarray([0, 40], dtype=np.int64),
            source_frames=np.asarray([0, 1], dtype=np.int64),
            keypoints_xy={},
            confidence={},
        )

    @staticmethod
    def _events() -> tuple[list[dict[str, object]], list[dict[str, object]]]:
        prediction = {
            "event_id": "prediction",
            "video_id": "video",
            "event_code": "FS09",
            "person_track_id": 1,
            "start_ms": 0,
            "end_ms": 40,
        }
        truth = {
            **prediction,
            "event_id": "truth",
            "view_group": "side",
        }
        return [prediction], [truth]

    def test_evaluated_budget_exposes_match_provenance(self) -> None:
        feature = _result(
            value=1.5,
            aggregation="event_early_median_minus_late_median",
            raw_value={"hip_speed": _series([4.0, 3.0, 2.0, 1.0])},
            smoothed_value={
                "series": {
                    "hip_speed": _series([3.5, 3.0, 2.0, 1.5]),
                }
            },
        )
        predictions, truth = self._events()
        with patch(
            "rallymate_evaluation.feature_errors.compute_feature",
            return_value=feature,
        ):
            report = evaluate_feature_errors(
                self._sequence(),
                self._sequence(),
                predictions,
                truth,
                {"FS09": [feature.feature_name]},
            )
        budget = report["error_budget"]["features"][feature.feature_name][
            "smoothing_error"
        ]
        self.assertEqual(budget["status"], "evaluated")
        self.assertEqual(budget["valid_count"], 1)
        self.assertEqual(budget["mae"], 0.5)
        self.assertEqual(
            report["details"][0]["smoothing_counterfactual_reason"],
            "computed_from_exact_series_match",
        )
        self.assertEqual(len(report["details"][0]["smoothing_matched_series"]), 1)

    def test_unavailable_budget_is_null_and_counts_reason(self) -> None:
        feature = _result(
            feature_name="hip_deceleration_to_double_support_proxy_ms",
            value=80,
            aggregation="phase_proxy_start_time_delta",
            raw_value={"hip_speed": _series([4.0, 3.0, 2.0, 1.0])},
            smoothed_value={
                "series": {
                    "hip_deceleration": _series([0.0, 1.0, 2.0, 1.0]),
                    "simultaneous_low_motion_mask": _series(
                        [0.0, 0.0, 1.0, 1.0]
                    ),
                }
            },
        )
        predictions, truth = self._events()
        with patch(
            "rallymate_evaluation.feature_errors.compute_feature",
            return_value=feature,
        ):
            report = evaluate_feature_errors(
                self._sequence(),
                self._sequence(),
                predictions,
                truth,
                {"FS09": [feature.feature_name]},
            )
        budget = report["error_budget"]["features"][feature.feature_name][
            "smoothing_error"
        ]
        self.assertEqual(budget["status"], "unavailable")
        self.assertIsNone(budget["mae"])
        self.assertEqual(budget["valid_count"], 0)
        self.assertEqual(
            budget["unavailable_reason_counts"],
            {"derived_timing_series_counterfactual_not_reconstructable": 1},
        )
        self.assertIsNone(
            report["details"][0]["smoothing_counterfactual_error"]
        )


if __name__ == "__main__":
    unittest.main()
