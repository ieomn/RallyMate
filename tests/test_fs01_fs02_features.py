from __future__ import annotations

import json
import math
import unittest

import numpy as np

from rallymate_features.event_features import FEATURE_DEFINITIONS, compute_event_features
from rallymate_features.fs01_fs02_features import (
    ANKLE_FALLBACK_CONFIDENCE_MULTIPLIER,
    FS01_FS02_EVENT_FEATURE_NAMES,
    FS01_FS02_FEATURE_VERSION,
    FS01_FS02_MAX_TEMPORAL_GAP_MS,
    bilateral_foot_rise_duration_from_position_series,
    bilateral_foot_rise_from_position_series,
    bilateral_foot_rise_synchrony_from_velocity_series,
    bilateral_foot_vertical_slowdown_from_position_series,
    drive_measurement_candidate_side,
    drive_side_from_knee_extension_peaks,
    foot_reference_series,
    first_step_phase_kinematics_from_series,
    hip_acceleration_along_launch_direction_from_series,
    hip_center_lateral_variability_from_position_series,
    hip_center_vertical_velocity_from_position_series,
    launch_direction_from_hip_motion,
    launch_foot_event_kinematics_from_series,
    post_slowdown_stance_width_from_position_series,
)
from rallymate_features.kinematics import irregular_derivative
from rallymate_features.schemas import EventInterval, PoseSequence


CORE_JOINTS = (
    "left_shoulder",
    "right_shoulder",
    "left_hip",
    "right_hip",
    "left_knee",
    "right_knee",
    "left_ankle",
    "right_ankle",
)


def _motion_sequence(
    *,
    fine_foot: bool,
    missing_fine_foot_indexes: set[int] | None = None,
    missing_ankle_indexes: set[int] | None = None,
) -> PoseSequence:
    timestamps = np.asarray(
        [
            0,
            40,
            95,
            150,
            215,
            270,
            335,
            390,
            455,
            510,
            575,
            630,
            695,
            750,
            815,
            870,
            935,
            990,
            1055,
            1110,
            1175,
        ],
        dtype=np.int64,
    )
    normalized_time = timestamps / timestamps[-1]
    names = list(CORE_JOINTS)
    if fine_foot:
        names.extend(
            f"{side}_{point}"
            for side in ("left", "right")
            for point in ("heel", "big_toe", "small_toe")
        )
    points: dict[str, list[tuple[float, float]]] = {name: [] for name in names}
    confidence: dict[str, list[float]] = {name: [] for name in names}
    missing = missing_fine_foot_indexes or set()
    missing_ankles = missing_ankle_indexes or set()
    for index, t in enumerate(normalized_time):
        hip_x = 0.50 + 0.16 * t * t + 0.04 * t
        hip_y = 0.56 - 0.05 * np.sin(np.pi * t)
        rise = (
            0.055 * np.sin(np.pi * min(t / 0.55, 1.0))
            if t <= 0.55
            else 0.0
        )
        left_ankle_x = 0.35 + 0.24 * max(0.0, (t - 0.25) / 0.75) ** 1.4
        right_ankle_x = 0.65 + 0.025 * t
        left_ankle_y = 0.90 - rise
        right_ankle_y = 0.90 - (0.95 * rise if t < 0.60 else 0.0)
        left_knee_offset = 0.035 * (1.0 - t)
        right_knee_offset = 0.075 * (1.0 - t)
        coordinates = {
            "left_hip": (hip_x - 0.05, hip_y),
            "right_hip": (hip_x + 0.05, hip_y),
            "left_ankle": (left_ankle_x, left_ankle_y),
            "right_ankle": (right_ankle_x, right_ankle_y),
            "left_knee": (
                ((hip_x - 0.05) + left_ankle_x) / 2.0 - left_knee_offset,
                (hip_y + left_ankle_y) / 2.0,
            ),
            "right_knee": (
                ((hip_x + 0.05) + right_ankle_x) / 2.0 + right_knee_offset,
                (hip_y + right_ankle_y) / 2.0,
            ),
            "left_shoulder": (hip_x - 0.10, hip_y - 0.25),
            "right_shoulder": (hip_x + 0.10, hip_y - 0.25),
        }
        if fine_foot:
            for side, ankle_x, ankle_y in (
                ("left", left_ankle_x, left_ankle_y),
                ("right", right_ankle_x, right_ankle_y),
            ):
                # The three-point centroid equals the ankle coordinate.  This
                # isolates topology/provenance behavior from geometry changes.
                coordinates[f"{side}_heel"] = (ankle_x - 0.01, ankle_y)
                coordinates[f"{side}_big_toe"] = (ankle_x + 0.02, ankle_y)
                coordinates[f"{side}_small_toe"] = (ankle_x - 0.01, ankle_y)
        for name in names:
            if (
                (name not in CORE_JOINTS and index in missing)
                or (name.endswith("_ankle") and index in missing_ankles)
            ):
                points[name].append((np.nan, np.nan))
                confidence[name].append(np.nan)
            else:
                points[name].append(coordinates[name])
                confidence[name].append(0.95)
    return PoseSequence(
        timestamp_ms=timestamps,
        source_frames=np.arange(100, 100 + timestamps.size, dtype=np.int64),
        keypoints_xy={name: np.asarray(values) for name, values in points.items()},
        confidence={name: np.asarray(values) for name, values in confidence.items()},
    )


def _mirrored_sequence(sequence: PoseSequence) -> PoseSequence:
    points: dict[str, np.ndarray] = {}
    confidence: dict[str, np.ndarray] = {}
    for name in sequence.keypoints_xy:
        if name.startswith("left_"):
            source_name = "right_" + name[len("left_") :]
        elif name.startswith("right_"):
            source_name = "left_" + name[len("right_") :]
        else:
            source_name = name
        values = sequence.keypoints_xy[source_name].copy()
        values[:, 0] = 1.0 - values[:, 0]
        points[name] = values
        confidence[name] = sequence.confidence[source_name].copy()
    return PoseSequence(
        timestamp_ms=sequence.timestamp_ms.copy(),
        source_frames=sequence.source_frames.copy(),
        keypoints_xy=points,
        confidence=confidence,
    )


def _with_timestamps(sequence: PoseSequence, timestamps: list[int]) -> PoseSequence:
    return PoseSequence(
        timestamp_ms=np.asarray(timestamps, dtype=np.int64),
        source_frames=sequence.source_frames.copy(),
        keypoints_xy={
            name: values.copy() for name, values in sequence.keypoints_xy.items()
        },
        confidence={
            name: values.copy() for name, values in sequence.confidence.items()
        },
    )


def _static_offset_sequence(
    *,
    missing_fine_indexes: set[int] | None = None,
    missing_ankle_indexes: set[int] | None = None,
) -> PoseSequence:
    timestamps = np.asarray(
        [0, 40, 95, 150, 215, 290, 375, 470, 575], dtype=np.int64
    )
    fine_names = tuple(
        f"{side}_{point}"
        for side in ("left", "right")
        for point in ("heel", "big_toe", "small_toe")
    )
    names = CORE_JOINTS + fine_names
    points = {name: [] for name in names}
    confidence = {name: [] for name in names}
    missing_fine = missing_fine_indexes or set()
    missing_ankle = missing_ankle_indexes or set()
    base = {
        "left_shoulder": (0.40, 0.30),
        "right_shoulder": (0.60, 0.30),
        "left_hip": (0.45, 0.55),
        "right_hip": (0.55, 0.55),
        "left_knee": (0.43, 0.72),
        "right_knee": (0.57, 0.72),
        "left_ankle": (0.40, 0.90),
        "right_ankle": (0.60, 0.90),
    }
    for index in range(timestamps.size):
        coordinates = dict(base)
        for side, ankle_x in (("left", 0.40), ("right", 0.60)):
            # Fine centroid - ankle is exactly (+0.12, -0.08).
            coordinates[f"{side}_heel"] = (ankle_x + 0.11, 0.82)
            coordinates[f"{side}_big_toe"] = (ankle_x + 0.14, 0.82)
            coordinates[f"{side}_small_toe"] = (ankle_x + 0.11, 0.82)
        for name in names:
            unavailable = (
                name in fine_names and index in missing_fine
            ) or (name.endswith("_ankle") and index in missing_ankle)
            if unavailable:
                points[name].append((np.nan, np.nan))
                confidence[name].append(np.nan)
            else:
                points[name].append(coordinates[name])
                confidence[name].append(0.95)
    return PoseSequence(
        timestamp_ms=timestamps,
        source_frames=np.arange(200, 200 + timestamps.size, dtype=np.int64),
        keypoints_xy={name: np.asarray(values) for name, values in points.items()},
        confidence={name: np.asarray(values) for name, values in confidence.items()},
    )


class FS01FS02PureFunctionTests(unittest.TestCase):
    def test_fs01_m04_pure_functions_share_slowdown_anchor_and_boundary_contract(self) -> None:
        timestamps = np.asarray([0, 50, 140, 260, 410, 570], dtype=np.int64)
        indexes = np.arange(timestamps.size, dtype=np.int64)
        left = np.asarray(
            [[0.30, 0.80], [0.30, 0.75], [0.30, 0.78], [0.29, 0.90], [0.28, 0.96], [0.28, 0.96]]
        )
        right = np.asarray(
            [[0.70, 0.80], [0.70, 0.76], [0.70, 0.80], [0.72, 0.88], [0.73, 0.93], [0.73, 0.93]]
        )
        hip = np.asarray(
            [[0.50, 0.55], [0.51, 0.55], [0.52, 0.55], [0.54, 0.55], [0.55, 0.55], [0.56, 0.55]]
        )

        offset, left_velocity, right_velocity, timing_evidence, timing_diag = (
            bilateral_foot_vertical_slowdown_from_position_series(
                timestamps, left, right, indexes, 0.5
            )
        )
        width, width_series, width_evidence, width_diag = (
            post_slowdown_stance_width_from_position_series(
                timestamps, left, right, indexes, 0.5
            )
        )
        variability, hip_x, hip_evidence, variability_diag, reason = (
            hip_center_lateral_variability_from_position_series(
                timestamps, left, right, hip, indexes, 0.5
            )
        )

        self.assertIsNotNone(offset)
        self.assertEqual(len(timing_evidence), 2)
        self.assertEqual(timing_diag["timing_source"], "timestamp_ms")
        self.assertTrue(np.isfinite(left_velocity).all())
        self.assertTrue(np.isfinite(right_velocity).all())
        self.assertIsNotNone(width)
        self.assertTrue(width_evidence)
        self.assertEqual(
            width_diag["post_slowdown_start_timestamp_ms"],
            timing_diag["post_slowdown_start_timestamp_ms"],
        )
        self.assertAlmostEqual(width, width_series[width_evidence[0]])
        self.assertIsNotNone(variability)
        self.assertEqual(reason, "valid_pose_kinematic_proxy")
        self.assertTrue(hip_evidence)
        self.assertEqual(
            variability_diag["post_slowdown_start_timestamp_ms"],
            timing_diag["post_slowdown_start_timestamp_ms"],
        )
        self.assertEqual(
            variability_diag["variability_statistic"],
            "population_standard_deviation",
        )
        self.assertTrue(np.isfinite(hip_x).all())

    def test_fs01_m04_pure_functions_reject_grid_and_scale_mismatch(self) -> None:
        timestamps = np.asarray([0, 40, 110, 200], dtype=np.int64)
        indexes = np.arange(timestamps.size, dtype=np.int64)
        position = np.zeros((timestamps.size, 2), dtype=np.float64)

        timing = bilateral_foot_vertical_slowdown_from_position_series(
            timestamps, position, position[:-1], indexes, 1.0
        )
        width = post_slowdown_stance_width_from_position_series(
            timestamps, position, position, indexes, 0.0
        )
        variability = hip_center_lateral_variability_from_position_series(
            np.asarray([0, 40, 40, 200], dtype=np.int64),
            position,
            position,
            position,
            indexes,
            1.0,
        )

        self.assertIsNone(timing[0])
        self.assertTrue(np.isnan(timing[1]).all())
        self.assertIsNone(width[0])
        self.assertTrue(np.isnan(width[1]).all())
        self.assertIsNone(variability[0])
        self.assertTrue(np.isnan(variability[1]).all())

    def test_fs01_m03_pure_functions_share_irregular_timestamp_contract(self) -> None:
        timestamps = np.asarray([0, 50, 140, 260, 410], dtype=np.int64)
        indexes = np.arange(timestamps.size, dtype=np.int64)
        left_position = np.asarray(
            [[0.3, 1.0], [0.3, 0.9], [0.3, 0.7], [0.3, 0.9], [0.3, 1.0]]
        )
        right_position = np.asarray(
            [[0.7, 1.0], [0.7, 0.95], [0.7, 0.8], [0.7, 0.92], [0.7, 1.0]]
        )
        left_velocity = irregular_derivative(timestamps, left_position)
        right_velocity = irregular_derivative(timestamps, right_position)
        hip_position = np.asarray(
            [[0.5, 0.60], [0.5, 0.58], [0.5, 0.53], [0.5, 0.49], [0.5, 0.48]]
        )

        rise, left_rise, right_rise, rise_evidence, rise_diag = (
            bilateral_foot_rise_from_position_series(
                timestamps,
                left_position,
                right_position,
                indexes,
                0.5,
            )
        )
        synchrony, left_up, right_up, sync_evidence, sync_diag = (
            bilateral_foot_rise_synchrony_from_velocity_series(
                timestamps,
                left_velocity,
                right_velocity,
                indexes,
                0.5,
            )
        )
        duration, _, _, mask, duration_evidence, duration_diag = (
            bilateral_foot_rise_duration_from_position_series(
                timestamps,
                left_position,
                right_position,
                indexes,
                0.5,
                np.ones(timestamps.size, dtype=bool),
            )
        )
        hip_value, hip_up, hip_evidence, hip_diag = (
            hip_center_vertical_velocity_from_position_series(
                timestamps,
                hip_position,
                indexes,
                0.5,
            )
        )

        self.assertAlmostEqual(rise, 0.35)
        self.assertEqual(rise_evidence, (2, 2))
        self.assertAlmostEqual(left_rise[2], 0.5)
        self.assertAlmostEqual(right_rise[2], 0.35)
        self.assertEqual(rise_diag["validation_status"], "strict_aligned_series")
        self.assertIsNotNone(synchrony)
        self.assertEqual(sync_diag["timing_source"], "timestamp_ms")
        self.assertTrue(np.isfinite(left_up).all())
        self.assertTrue(np.isfinite(right_up).all())
        self.assertTrue(sync_evidence)
        self.assertEqual(duration, 105)
        self.assertEqual(duration_evidence, (2, 2))
        self.assertEqual(mask[2], 1.0)
        self.assertTrue(duration_diag["complete_bilateral_observation"])
        self.assertEqual(
            duration_diag["duration_observation_status"],
            "observed_positive_simultaneous_run",
        )
        expected_hip = -irregular_derivative(timestamps, hip_position)[:, 1] / 0.5
        self.assertAlmostEqual(hip_value, float(np.max(expected_hip)))
        np.testing.assert_allclose(hip_up, expected_hip)
        self.assertTrue(hip_evidence)
        self.assertEqual(hip_diag["timestamp_source"], "timestamp_ms")

    def test_fs01_m03_pure_functions_reject_misaligned_or_invalid_inputs(self) -> None:
        timestamps = np.asarray([0, 40, 110, 200], dtype=np.int64)
        indexes = np.arange(timestamps.size, dtype=np.int64)
        position = np.zeros((timestamps.size, 2), dtype=np.float64)
        velocity = np.zeros((timestamps.size, 2), dtype=np.float64)

        rise = bilateral_foot_rise_from_position_series(
            timestamps,
            position,
            position[:-1],
            indexes,
            1.0,
        )
        synchrony = bilateral_foot_rise_synchrony_from_velocity_series(
            np.asarray([0, 40, 40, 200], dtype=np.int64),
            velocity,
            velocity,
            indexes,
            1.0,
        )
        duration = bilateral_foot_rise_duration_from_position_series(
            timestamps,
            position,
            position,
            indexes,
            1.0,
            np.ones(timestamps.size - 1, dtype=bool),
        )
        hip = hip_center_vertical_velocity_from_position_series(
            timestamps,
            position,
            np.asarray([0, 2, 1], dtype=np.int64),
            1.0,
        )

        self.assertIsNone(rise[0])
        self.assertTrue(np.isnan(rise[1]).all())
        self.assertIsNone(synchrony[0])
        self.assertTrue(np.isnan(synchrony[1]).all())
        self.assertIsNone(duration[0])
        self.assertTrue(np.isnan(duration[3]).all())
        self.assertIsNone(hip[0])
        self.assertTrue(np.isnan(hip[1]).all())

    def test_hip_acceleration_projection_uses_shared_irregular_time_direction(self) -> None:
        timestamps = np.asarray([0, 55, 170, 390, 710], dtype=np.int64)
        seconds = timestamps / 1000.0
        positions = np.column_stack([0.2 + seconds, np.full(seconds.shape, 0.5)])
        acceleration = np.column_stack(
            [np.asarray([0.5, 1.0, 2.0, 3.0, 4.0]), np.zeros(5)]
        )

        value, projection, evidence, diagnostics = (
            hip_acceleration_along_launch_direction_from_series(
                timestamps,
                positions,
                acceleration,
                np.arange(timestamps.size),
            )
        )

        self.assertAlmostEqual(value, 4.0, places=6)
        np.testing.assert_allclose(projection, acceleration[:, 0], atol=1e-12)
        self.assertEqual(evidence[-1], 4)
        self.assertAlmostEqual(diagnostics["launch_direction_deg"], 0.0, places=6)

    def test_hip_acceleration_projection_rejects_misaligned_series(self) -> None:
        value, projection, evidence, diagnostics = (
            hip_acceleration_along_launch_direction_from_series(
                np.asarray([0, 40, 100], dtype=np.int64),
                np.asarray([[0.0, 0.5], [0.1, 0.5], [0.2, 0.5]]),
                np.asarray([[1.0, 0.0], [2.0, 0.0]]),
                np.arange(3),
            )
        )

        self.assertIsNone(value)
        self.assertTrue(np.isnan(projection).all())
        self.assertEqual(evidence, ())
        self.assertEqual(
            diagnostics["reason"],
            "invalid_or_misaligned_hip_kinematic_series",
        )

    def test_launch_direction_uses_irregular_timestamp_ms(self) -> None:
        timestamps = np.asarray([0, 55, 170, 390, 710, 1175], dtype=np.int64)
        seconds = timestamps / 1000.0
        positions = np.column_stack(
            [0.4 + 0.2 * seconds, 0.7 - 0.1 * seconds]
        )
        unit, angle, evidence, diagnostics = launch_direction_from_hip_motion(
            timestamps,
            positions,
            np.arange(timestamps.size),
        )
        self.assertIsNotNone(unit)
        self.assertAlmostEqual(angle, math.degrees(math.atan2(0.1, 0.2)), places=6)
        self.assertEqual(len(evidence), 2)
        self.assertEqual(
            diagnostics["direction_source"],
            "hip_displacement_plus_median_velocity_times_duration",
        )

    def test_fine_foot_is_preferred_and_coco17_fallback_is_explicit(self) -> None:
        fine = foot_reference_series(_motion_sequence(fine_foot=True), "left")
        fallback = foot_reference_series(_motion_sequence(fine_foot=False), "left")
        self.assertEqual(
            fine.mode, "fine_foot_centroid_heel_big_toe_small_toe"
        )
        self.assertEqual(fine.confidence_multiplier, 1.0)
        self.assertEqual(int(fine.fine_mask.sum()), 21)
        self.assertEqual(int(fine.ankle_fallback_mask.sum()), 0)
        self.assertFalse(fine.missing_mask.any())
        self.assertEqual(fallback.mode, "coco17_ankle_fallback")
        self.assertEqual(
            fallback.confidence_multiplier,
            ANKLE_FALLBACK_CONFIDENCE_MULTIPLIER,
        )
        self.assertEqual(int(fallback.fine_mask.sum()), 0)
        self.assertEqual(int(fallback.ankle_fallback_mask.sum()), 21)
        self.assertFalse(fallback.missing_mask.any())
        self.assertEqual(
            fallback.alignment_method, "not_required_ankle_only_topology"
        )

    def test_aligned_sparse_fallback_does_not_create_false_motion(self) -> None:
        sequence = _static_offset_sequence(missing_fine_indexes={4})
        reference = foot_reference_series(sequence, "left")
        self.assertEqual(
            reference.alignment_method,
            "robust_full_sequence_median_fine_centroid_minus_ankle",
        )
        self.assertEqual(reference.alignment_overlap_count, 8)
        np.testing.assert_allclose(
            reference.fine_minus_ankle_offset_xy, (0.12, -0.08), atol=1e-12
        )
        self.assertTrue(reference.ankle_fallback_mask[4])
        np.testing.assert_allclose(
            reference.values,
            np.repeat([[0.52, 0.82]], sequence.timestamp_ms.size, axis=0),
            atol=1e-12,
        )
        velocity = irregular_derivative(
            sequence.timestamp_ms, reference.values
        )
        np.testing.assert_allclose(velocity, 0.0, atol=1e-12)
        event = EventInterval("static", "FS01", 0, 575)
        rise = compute_event_features(
            sequence, event, ["bilateral_foot_rise_min_body"]
        )[0]
        self.assertTrue(rise.valid)
        self.assertAlmostEqual(rise.value, 0.0, places=12)
        alignment = rise.provenance["foot_reference_provenance"]["sides"][
            "left"
        ]["alignment"]
        self.assertEqual(alignment["overlap_frame_count_full_sequence"], 8)
        np.testing.assert_allclose(
            alignment["fine_centroid_minus_ankle_offset_xy"],
            [0.12, -0.08],
            atol=1e-12,
        )

    def test_no_overlap_excludes_unaligned_fallback_from_derivatives(self) -> None:
        sequence = _static_offset_sequence(
            missing_fine_indexes=set(range(4, 9)),
            missing_ankle_indexes=set(range(4)),
        )
        reference = foot_reference_series(sequence, "left")
        self.assertEqual(
            reference.alignment_method,
            "unavailable_no_same_frame_fine_ankle_overlap",
        )
        self.assertEqual(reference.alignment_overlap_count, 0)
        self.assertIsNone(reference.fine_minus_ankle_offset_xy)
        self.assertEqual(int(reference.unaligned_ankle_fallback_mask.sum()), 5)
        self.assertFalse(reference.valid[4:].any())
        self.assertTrue(np.isnan(reference.values[4:]).all())
        event = EventInterval("unaligned", "FS01", 0, 575)
        result = compute_event_features(
            sequence, event, ["bilateral_foot_rise_min_body"]
        )[0]
        self.assertFalse(result.valid)
        side = result.provenance["foot_reference_provenance"]["sides"]["left"]
        self.assertEqual(side["unaligned_ankle_fallback_frame_count"], 5)
        self.assertTrue(
            side["alignment"][
                "unaligned_fallback_unusable_for_derivatives"
            ]
        )
        self.assertAlmostEqual(
            result.provenance["topology_confidence_multiplier"], 4.0 / 9.0
        )

    def test_complete_static_event_reports_observed_zero_rise_duration(self) -> None:
        sequence = _static_offset_sequence()
        event = EventInterval("static-zero", "FS01", 0, 575)
        result = compute_event_features(
            sequence,
            event,
            ["bilateral_foot_rise_proxy_duration_ms"],
        )[0]
        self.assertTrue(result.valid)
        self.assertEqual(result.value, 0)
        self.assertEqual(
            result.reason,
            "valid_observed_zero_simultaneous_bilateral_rise_proxy",
        )
        self.assertTrue(result.source_frames)
        summary = result.smoothed_value["summary"]
        self.assertTrue(summary["complete_bilateral_observation"])
        self.assertEqual(
            summary["duration_observation_status"],
            "observed_zero_no_simultaneous_run",
        )
        self.assertEqual(summary["zero_semantics"], "observed_absence_not_missing")

    def test_incomplete_static_event_keeps_rise_duration_null(self) -> None:
        missing = set(range(4))
        sequence = _static_offset_sequence(
            missing_fine_indexes=missing,
            missing_ankle_indexes=missing,
        )
        event = EventInterval("static-missing", "FS01", 0, 575)
        result = compute_event_features(
            sequence,
            event,
            ["bilateral_foot_rise_proxy_duration_ms"],
        )[0]
        self.assertFalse(result.valid)
        self.assertIsNone(result.value)
        self.assertEqual(result.reason, "bilateral_rise_proxy_not_observable")
        summary = result.smoothed_value["summary"]
        self.assertFalse(summary["complete_bilateral_observation"])
        self.assertEqual(
            summary["duration_observation_status"],
            "unavailable_incomplete_bilateral_observation",
        )
        self.assertIsNone(summary["zero_semantics"])

    def test_observed_zero_requires_timestamp_gap_within_smoothing_contract(self) -> None:
        base = _static_offset_sequence()
        for gap_ms, expected_valid in (
            (FS01_FS02_MAX_TEMPORAL_GAP_MS, True),
            (FS01_FS02_MAX_TEMPORAL_GAP_MS + 1, False),
        ):
            with self.subTest(gap_ms=gap_ms):
                timestamps = [
                    0,
                    40,
                    95,
                    150,
                    150 + gap_ms,
                    190 + gap_ms,
                    245 + gap_ms,
                    320 + gap_ms,
                    415 + gap_ms,
                ]
                sequence = _with_timestamps(base, timestamps)
                result = compute_event_features(
                    sequence,
                    EventInterval("timestamp-gap", "FS01", 0, timestamps[-1]),
                    ["bilateral_foot_rise_proxy_duration_ms"],
                )[0]
                summary = result.smoothed_value["summary"]
                self.assertEqual(summary["max_observed_gap_ms"], gap_ms)
                self.assertEqual(
                    summary["allowed_max_gap_ms"],
                    FS01_FS02_MAX_TEMPORAL_GAP_MS,
                )
                self.assertEqual(result.valid, expected_valid)
                if expected_valid:
                    self.assertEqual(result.value, 0)
                    self.assertTrue(summary["temporal_coverage_complete"])
                else:
                    self.assertIsNone(result.value)
                    self.assertFalse(summary["temporal_coverage_complete"])
                    self.assertEqual(
                        result.reason,
                        "bilateral_rise_proxy_temporal_gap_exceeds_smoothing_contract",
                    )

    def test_single_boundary_frame_uses_only_nearest_valid_in_event_sample(self) -> None:
        sequence = _motion_sequence(fine_foot=True)
        event = EventInterval("short-tail", "FS01", 0, 575)
        result = compute_event_features(
            sequence,
            event,
            ["hip_center_lateral_variability_body"],
        )[0]
        self.assertTrue(result.valid)
        self.assertIsNotNone(result.value)
        self.assertGreater(result.value, 0.0)
        self.assertEqual(result.reason, "valid_pose_kinematic_proxy")
        self.assertEqual(result.source_frames, [109, 110])
        summary = result.smoothed_value["summary"]
        self.assertEqual(summary["valid_post_samples"], 1)
        self.assertEqual(summary["minimum_post_samples"], 2)
        self.assertEqual(summary["measurement_sample_count"], 2)
        self.assertEqual(summary["post_slowdown_start_timestamp_ms"], 575)
        self.assertEqual(
            summary["post_slowdown_window_status"],
            "boundary_censored_two_sample_window",
        )
        self.assertEqual(
            summary["boundary_censored_pre_sample_timestamp_ms"], 510
        )
        self.assertEqual(summary["boundary_censored_pre_sample_gap_ms"], 65)
        self.assertEqual(summary["boundary_censored_max_gap_ms"], 160)
        timestamps_by_frame = dict(
            zip(sequence.source_frames.tolist(), sequence.timestamp_ms.tolist())
        )
        self.assertTrue(
            all(
                event.start_ms <= timestamps_by_frame[frame] <= event.end_ms
                for frame in result.source_frames
            )
        )

    def test_boundary_censored_window_does_not_bridge_timestamp_gap(self) -> None:
        sequence = _motion_sequence(fine_foot=True)
        timestamps = sequence.timestamp_ms.copy()
        timestamps[-1] = int(timestamps[-2] + FS01_FS02_MAX_TEMPORAL_GAP_MS + 1)
        sequence = _with_timestamps(sequence, timestamps.tolist())
        event = EventInterval("short-tail-gap", "FS01", 0, int(timestamps[-1]))
        result = compute_event_features(
            sequence,
            event,
            ["hip_center_lateral_variability_body"],
        )[0]
        summary = result.smoothed_value["summary"]
        if summary["valid_post_samples"] == 1:
            self.assertFalse(result.valid)
            self.assertIsNone(result.value)
            self.assertEqual(result.reason, "post_slowdown_window_insufficient")
            self.assertEqual(
                summary["post_slowdown_window_status"],
                "insufficient_post_slowdown_window",
            )
            self.assertIsNone(
                summary["boundary_censored_pre_sample_timestamp_ms"]
            )
        else:
            # If the changed timestamps move the slowdown anchor earlier, the
            # ordinary fully observed path remains the only valid alternative.
            self.assertGreaterEqual(summary["valid_post_samples"], 2)
            self.assertEqual(
                summary["post_slowdown_window_status"],
                "fully_observed_post_slowdown_window",
            )

    def test_fs02_m04_launch_kinematics_share_one_irregular_time_contract(self) -> None:
        timestamps = np.asarray([0, 70, 190, 360, 590], dtype=np.int64)
        hip = np.asarray(
            [[0.0, 0.5], [0.1, 0.5], [0.25, 0.5], [0.45, 0.5], [0.7, 0.5]],
            dtype=np.float64,
        )
        left = np.asarray(
            [[-0.2, 1.0], [-0.18, 1.0], [-0.15, 1.0], [-0.1, 1.0], [-0.05, 1.0]],
            dtype=np.float64,
        )
        right = np.asarray(
            [[0.2, 1.0], [0.25, 1.0], [0.4, 1.0], [0.65, 1.0], [0.95, 1.0]],
            dtype=np.float64,
        )
        left_velocity = irregular_derivative(timestamps, left)
        right_velocity = irregular_derivative(timestamps, right)
        result = launch_foot_event_kinematics_from_series(
            timestamps,
            hip,
            left,
            right,
            left_velocity,
            right_velocity,
            np.arange(timestamps.size),
            0.5,
        )

        self.assertEqual(result["launch_code"], 1)
        self.assertEqual(result["selected_side"], "right")
        self.assertGreater(result["speed_peak_body_s"], 0.0)
        self.assertGreater(result["relative_displacement_body"], 0.0)
        self.assertGreater(result["motion_duration_ms"], 0)
        self.assertTrue(result["launch_side_evidence"])
        self.assertTrue(result["motion_duration_evidence"])
        self.assertEqual(
            result["diagnostics"]["timestamp_source"], "timestamp_ms"
        )
        self.assertEqual(
            result["diagnostics"]["envelope_status"],
            "event_local_half_peak_motion_not_airborne",
        )

    def test_fs02_m04_launch_kinematics_fail_closed_on_grid_or_scale(self) -> None:
        timestamps = np.asarray([0, 50, 140, 260], dtype=np.int64)
        positions = np.asarray(
            [[0.0, 0.0], [0.1, 0.0], [0.2, 0.0], [0.3, 0.0]],
            dtype=np.float64,
        )
        velocity = irregular_derivative(timestamps, positions)
        invalid_grid = launch_foot_event_kinematics_from_series(
            timestamps,
            positions[:-1],
            positions,
            positions,
            velocity,
            velocity,
            np.arange(timestamps.size),
            1.0,
        )
        invalid_scale = launch_foot_event_kinematics_from_series(
            timestamps,
            positions,
            positions,
            positions,
            velocity,
            velocity,
            np.arange(timestamps.size),
            0.0,
        )
        for result in (invalid_grid, invalid_scale):
            self.assertIsNone(result["launch_code"])
            self.assertIsNone(result["speed_peak_body_s"])
            self.assertIsNone(result["relative_displacement_body"])
            self.assertIsNone(result["motion_duration_ms"])
            self.assertEqual(
                result["diagnostics"]["reason"],
                "invalid_or_misaligned_launch_foot_kinematic_series",
            )

    def test_fs02_m05_first_step_family_uses_fixed_phase_and_irregular_time(self) -> None:
        timestamps = np.asarray([0, 70, 190, 360, 590, 760, 940], dtype=np.int64)
        hip = np.asarray(
            [[0.0, 0.5], [0.1, 0.5], [0.25, 0.5], [0.45, 0.5], [0.7, 0.5], [0.85, 0.5], [0.95, 0.5]],
            dtype=np.float64,
        )
        left = np.asarray(
            [[-0.2, 1.0], [-0.18, 1.0], [-0.15, 1.0], [-0.1, 1.0], [-0.05, 1.0], [0.0, 1.0], [0.04, 1.0]],
            dtype=np.float64,
        )
        right = np.asarray(
            [[0.2, 1.0], [0.25, 1.0], [0.45, 1.0], [0.75, 1.0], [1.0, 1.0], [1.05, 1.0], [1.06, 1.0]],
            dtype=np.float64,
        )
        hip_velocity = irregular_derivative(timestamps, hip)
        left_velocity = irregular_derivative(timestamps, left)
        right_velocity = irregular_derivative(timestamps, right)
        result = first_step_phase_kinematics_from_series(
            timestamps,
            hip,
            hip_velocity,
            left,
            right,
            left_velocity,
            right_velocity,
            np.arange(timestamps.size),
            0.5,
            {"first_step_slowdown_proxy_ms": 360},
        )

        self.assertEqual(result["selected_side"], "right")
        self.assertGreater(result["speed_drop_body_s"], 0.0)
        self.assertGreater(result["first_step_displacement_body"], 0.0)
        self.assertGreater(result["post_direction_consistency"], 0.9)
        self.assertGreaterEqual(result["slowdown_to_post_direction_ms"], 0)
        self.assertGreater(result["post_stance_width_body"], 0.0)
        self.assertEqual(result["post_step_anchor_index"], 3)
        self.assertEqual(
            result["post_step_anchor_diagnostics"]["matched_source_timestamp_ms"],
            360,
        )
        self.assertNotEqual(result["speed_slowdown_index"], 3)
        self.assertEqual(
            result["diagnostics"]["proxy_status"],
            "first_step_2d_kinematics_not_landing_contact_or_load",
        )

    def test_fs02_m05_first_step_family_fails_closed_on_grid_or_phase(self) -> None:
        timestamps = np.asarray([0, 50, 140, 260], dtype=np.int64)
        positions = np.asarray(
            [[0.0, 0.0], [0.1, 0.0], [0.2, 0.0], [0.3, 0.0]],
            dtype=np.float64,
        )
        velocity = irregular_derivative(timestamps, positions)
        invalid_grid = first_step_phase_kinematics_from_series(
            timestamps,
            positions,
            velocity[:-1],
            positions,
            positions,
            velocity,
            velocity,
            np.arange(timestamps.size),
            1.0,
            {"first_step_slowdown_proxy_ms": 140},
        )
        invalid_phase = first_step_phase_kinematics_from_series(
            timestamps,
            positions,
            velocity,
            positions,
            positions + np.asarray([0.5, 0.0]),
            velocity,
            velocity,
            np.arange(timestamps.size),
            1.0,
            {"first_step_slowdown_proxy_ms": 5000},
        )
        self.assertIsNone(invalid_grid["speed_drop_body_s"])
        self.assertIsNone(invalid_grid["post_direction_consistency"])
        self.assertEqual(
            invalid_grid["diagnostics"]["reason"],
            "invalid_or_misaligned_first_step_kinematic_series",
        )
        self.assertIsNone(invalid_phase["post_step_anchor_index"])
        self.assertIsNone(invalid_phase["post_direction_consistency"])
        self.assertIsNone(invalid_phase["post_stance_width_body"])
        self.assertEqual(
            invalid_phase["post_step_anchor_diagnostics"]["phase_anchor_status"],
            "event_phase_outside_temporal_alignment_contract",
        )


class FS01FS02FeatureIntegrationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.event = EventInterval("fs02-synthetic", "FS02", 0, 1175)

    def test_nonpositive_bilateral_extension_is_measurable_negative_evidence(self) -> None:
        self.assertEqual(
            drive_measurement_candidate_side(-1.75, -0.75),
            ("right", "no_positive_extension_right_is_less_negative"),
        )
        self.assertEqual(
            drive_measurement_candidate_side(-0.25, -1.0),
            ("left", "no_positive_extension_left_is_less_negative"),
        )
        self.assertEqual(
            drive_measurement_candidate_side(2.0, -1.0),
            ("left", "left_has_larger_positive_signal"),
        )
        self.assertEqual(
            drive_measurement_candidate_side(-1.0, -1.0),
            (None, "no_positive_bilateral_signal"),
        )
        self.assertEqual(
            drive_measurement_candidate_side(None, -1.0),
            (None, "bilateral_values_unavailable"),
        )
        self.assertEqual(
            drive_side_from_knee_extension_peaks(2.0, -1.0),
            (-1, "left", "left_has_larger_positive_signal"),
        )
        self.assertEqual(
            drive_side_from_knee_extension_peaks(-1.0, -0.5),
            (0, None, "no_positive_bilateral_signal"),
        )

    def test_fs02_m04_features_publish_shared_replayable_launch_evidence(self) -> None:
        names = [
            "launch_side_code",
            "launch_foot_speed_peak_body_s",
            "launch_foot_relative_displacement_body",
            "launch_foot_motion_duration_ms",
        ]
        results = compute_event_features(
            _motion_sequence(fine_foot=True), self.event, names
        )
        required_series = {
            "hip_position",
            "left_foot_position",
            "right_foot_position",
            "left_foot_velocity",
            "right_foot_velocity",
        }
        self.assertEqual([result.feature_name for result in results], names)
        for result in results:
            self.assertTrue(result.valid, (result.feature_name, result.reason))
            self.assertEqual(
                result.provenance["evidence_contract"],
                "fs02_m04_raw_and_prepared_launch_kinematics_v1",
            )
            self.assertTrue(required_series.issubset(result.raw_value))
            self.assertTrue(
                required_series.issubset(result.smoothed_value["series"])
            )
            for name in required_series:
                self.assertEqual(
                    [item["timestamp_ms"] for item in result.raw_value[name]],
                    [
                        item["timestamp_ms"]
                        for item in result.smoothed_value["series"][name]
                    ],
                )

    def test_fs02_m05_features_publish_shared_phase_replay_evidence(self) -> None:
        names = [
            "launch_foot_speed_drop_body_s",
            "first_step_displacement_body",
            "post_step_hip_direction_consistency",
            "launch_foot_slowdown_to_post_hip_direction_ms",
            "post_step_stance_width_body",
        ]
        event = EventInterval(
            "fs02-m05-evidence",
            "FS02",
            0,
            1175,
            key_phases={"first_step_slowdown_proxy_ms": 630},
        )
        required_series = {
            "hip_position",
            "hip_velocity",
            "left_foot_position",
            "right_foot_position",
            "left_foot_velocity",
            "right_foot_velocity",
        }
        results = compute_event_features(
            _motion_sequence(fine_foot=True), event, names
        )
        self.assertEqual([result.feature_name for result in results], names)
        for result in results:
            self.assertTrue(result.valid, (result.feature_name, result.reason))
            self.assertEqual(
                result.provenance["evidence_contract"],
                "fs02_m05_raw_and_prepared_first_step_phase_kinematics_v1",
            )
            self.assertEqual(set(result.raw_value), required_series)
            self.assertEqual(
                set(result.smoothed_value["series"]), required_series
            )
            self.assertEqual(
                result.smoothed_value["summary"]["post_step_anchor_source"],
                "event_key_phase",
            )
            self.assertEqual(
                result.smoothed_value["summary"]["event_phase_timestamp_ms"],
                630,
            )

    def test_all_next_wave_features_are_versioned_valid_and_json_safe(self) -> None:
        results = compute_event_features(
            _motion_sequence(fine_foot=True),
            self.event,
            sorted(FS01_FS02_EVENT_FEATURE_NAMES),
        )
        self.assertEqual(len(results), 21)
        for result in results:
            self.assertEqual(result.feature_version, FS01_FS02_FEATURE_VERSION)
            self.assertTrue(result.valid, (result.feature_name, result.reason))
            self.assertIsNotNone(result.value, result.feature_name)
            self.assertTrue(result.source_frames, result.feature_name)
            self.assertEqual(result.provenance["timestamp_source"], "timestamp_ms")
            self.assertEqual(
                result.provenance["measurement_status"],
                "pose_kinematic_proxy_not_contact_force_or_calibrated_grade",
            )
            self.assertIsNone(result.provenance["score_threshold_version"])
            self.assertIn(result.feature_name, FEATURE_DEFINITIONS)
            json.dumps(result.to_dict(), allow_nan=False)

    def test_fs01_m03_features_publish_replayable_raw_and_prepared_evidence(self) -> None:
        names = [
            "bilateral_foot_rise_min_body",
            "bilateral_foot_rise_synchrony_ms",
            "bilateral_foot_rise_proxy_duration_ms",
            "hip_center_vertical_velocity_body_s",
        ]
        by_name = {
            result.feature_name: result
            for result in compute_event_features(
                _motion_sequence(fine_foot=True), self.event, names
            )
        }
        for result in by_name.values():
            self.assertEqual(
                result.provenance["evidence_contract"],
                "fs01_m03_raw_and_prepared_kinematic_series_v1",
            )
            self.assertTrue(result.valid, (result.feature_name, result.reason))
        for name in (
            "bilateral_foot_rise_min_body",
            "bilateral_foot_rise_proxy_duration_ms",
        ):
            result = by_name[name]
            self.assertEqual(
                set(result.raw_value),
                {"left_foot_position", "right_foot_position"},
            )
            self.assertTrue(
                {"left_foot_position", "right_foot_position"}.issubset(
                    result.smoothed_value["series"]
                )
            )
        synchrony = by_name["bilateral_foot_rise_synchrony_ms"]
        self.assertEqual(
            set(synchrony.raw_value),
            {"left_foot_velocity", "right_foot_velocity"},
        )
        self.assertTrue(
            {"left_foot_velocity", "right_foot_velocity"}.issubset(
                synchrony.smoothed_value["series"]
            )
        )
        hip = by_name["hip_center_vertical_velocity_body_s"]
        self.assertEqual(set(hip.raw_value), {"hip_position"})
        self.assertIn("hip_position", hip.smoothed_value["series"])

    def test_fs01_m04_features_publish_replayable_slowdown_evidence(self) -> None:
        names = [
            "bilateral_foot_vertical_slowdown_time_offset_ms",
            "post_slowdown_stance_width_body",
            "hip_center_lateral_variability_body",
        ]
        by_name = {
            result.feature_name: result
            for result in compute_event_features(
                _motion_sequence(fine_foot=True), self.event, names
            )
        }
        for result in by_name.values():
            self.assertTrue(result.valid, (result.feature_name, result.reason))
            self.assertEqual(
                result.provenance["evidence_contract"],
                "fs01_m04_raw_and_prepared_slowdown_series_v1",
            )
            self.assertTrue(
                {"left_foot_position", "right_foot_position"}.issubset(
                    result.raw_value
                )
            )
            self.assertTrue(
                {"left_foot_position", "right_foot_position"}.issubset(
                    result.smoothed_value["series"]
                )
            )
        variability = by_name["hip_center_lateral_variability_body"]
        self.assertIn("hip_position", variability.raw_value)
        self.assertIn("hip_position", variability.smoothed_value["series"])

    def test_synthetic_motion_exposes_expected_proxy_structure(self) -> None:
        names = [
            "bilateral_foot_rise_min_body",
            "bilateral_foot_rise_proxy_duration_ms",
            "hip_center_vertical_velocity_body_s",
            "drive_side_code",
            "support_knee_extension_velocity_deg_s",
            "hip_acceleration_along_launch_direction_body_s2",
            "launch_side_code",
            "launch_foot_relative_displacement_body",
            "launch_foot_speed_drop_body_s",
            "first_step_displacement_body",
            "post_step_hip_direction_consistency",
            "post_step_stance_width_body",
        ]
        by_name = {
            result.feature_name: result
            for result in compute_event_features(
                _motion_sequence(fine_foot=True), self.event, names
            )
        }
        self.assertGreater(by_name["bilateral_foot_rise_min_body"].value, 0.0)
        self.assertGreater(
            by_name["bilateral_foot_rise_proxy_duration_ms"].value, 0
        )
        self.assertGreater(by_name["hip_center_vertical_velocity_body_s"].value, 0.0)
        self.assertEqual(by_name["drive_side_code"].value, 1)
        self.assertGreater(by_name["support_knee_extension_velocity_deg_s"].value, 0.0)
        self.assertGreater(
            by_name["hip_acceleration_along_launch_direction_body_s2"].value,
            0.0,
        )
        hip_acceleration = by_name[
            "hip_acceleration_along_launch_direction_body_s2"
        ]
        self.assertEqual(
            set(hip_acceleration.raw_value),
            {"hip_position", "hip_acceleration_xy_body_s2"},
        )
        self.assertTrue(
            {
                "hip_position",
                "hip_acceleration_xy_body_s2",
                "hip_acceleration_along_launch_direction_body_s2",
            }.issubset(hip_acceleration.smoothed_value["series"])
        )
        self.assertEqual(
            hip_acceleration.smoothed_value["summary"]["evidence_contract"],
            "raw_and_smoothed_hip_position_acceleration_series_v1",
        )
        self.assertEqual(by_name["launch_side_code"].value, -1)
        self.assertGreater(by_name["launch_foot_relative_displacement_body"].value, 0.0)
        self.assertGreater(by_name["launch_foot_speed_drop_body_s"].value, 0.0)
        self.assertGreater(by_name["first_step_displacement_body"].value, 0.0)
        self.assertGreater(by_name["post_step_hip_direction_consistency"].value, 0.0)
        self.assertGreater(by_name["post_step_stance_width_body"].value, 0.0)

    def test_post_step_features_use_versioned_event_phase_not_late_global_peak(self) -> None:
        sequence = _motion_sequence(fine_foot=True)
        event = EventInterval(
            "fs02-phase-anchored",
            "FS02",
            0,
            1175,
            key_phases={"first_step_slowdown_proxy_ms": 630},
        )
        names = [
            "post_step_hip_direction_consistency",
            "launch_foot_slowdown_to_post_hip_direction_ms",
            "post_step_stance_width_body",
        ]
        results = compute_event_features(sequence, event, names)
        for result in results:
            self.assertTrue(result.valid, (result.feature_name, result.reason))
            diagnostics = result.smoothed_value["summary"]
            self.assertEqual(
                diagnostics["post_step_anchor_source"], "event_key_phase"
            )
            self.assertEqual(diagnostics["event_phase_timestamp_ms"], 630)
            self.assertEqual(diagnostics["matched_source_timestamp_ms"], 630)
            self.assertEqual(diagnostics["phase_alignment_error_ms"], 0)
            self.assertEqual(
                diagnostics["phase_anchor_status"],
                "matched_event_phase_to_source_sample",
            )
            # This fixture's unconstrained global speed peak is the last frame;
            # using it would leave no post-step evidence.  The explicit event
            # phase must therefore remain the authoritative anchor.
            self.assertEqual(
                diagnostics["computed_signal_slowdown_timestamp_ms"], 1175
            )
        stance = next(
            result
            for result in results
            if result.feature_name == "post_step_stance_width_body"
        )
        self.assertEqual(
            stance.smoothed_value["summary"]["post_step_start_timestamp_ms"],
            630,
        )

    def test_declared_unalignable_post_step_phase_fails_closed(self) -> None:
        sequence = _motion_sequence(fine_foot=True)
        event = EventInterval(
            "fs02-invalid-phase",
            "FS02",
            0,
            1175,
            key_phases={"first_step_slowdown_proxy_ms": 5000},
        )
        result = compute_event_features(
            sequence, event, ["post_step_stance_width_body"]
        )[0]
        self.assertFalse(result.valid)
        self.assertIsNone(result.value)
        diagnostics = result.smoothed_value["summary"]
        self.assertEqual(
            diagnostics["phase_anchor_status"],
            "event_phase_outside_temporal_alignment_contract",
        )
        self.assertEqual(diagnostics["post_step_anchor_source"], "event_key_phase")

    def test_mirror_preserves_scalars_and_transforms_direction_and_side(self) -> None:
        sequence = _motion_sequence(fine_foot=True)
        mirrored = _mirrored_sequence(sequence)
        names = [
            "bilateral_foot_rise_min_body",
            "hip_acceleration_along_launch_direction_body_s2",
            "launch_direction_deg",
            "launch_side_code",
            "first_step_displacement_body",
            "post_step_stance_width_body",
        ]
        original = {
            result.feature_name: result.value
            for result in compute_event_features(sequence, self.event, names)
        }
        reflected = {
            result.feature_name: result.value
            for result in compute_event_features(mirrored, self.event, names)
        }
        for name in (
            "bilateral_foot_rise_min_body",
            "hip_acceleration_along_launch_direction_body_s2",
            "first_step_displacement_body",
            "post_step_stance_width_body",
        ):
            self.assertAlmostEqual(original[name], reflected[name], places=6, msg=name)
        expected_angle = ((180.0 - original["launch_direction_deg"] + 180.0) % 360.0) - 180.0
        self.assertAlmostEqual(reflected["launch_direction_deg"], expected_angle, places=6)
        self.assertEqual(reflected["launch_side_code"], -original["launch_side_code"])

    def test_ankle_fallback_keeps_measurement_but_lowers_confidence(self) -> None:
        name = "bilateral_foot_rise_min_body"
        fine = compute_event_features(
            _motion_sequence(fine_foot=True), self.event, [name]
        )[0]
        fallback = compute_event_features(
            _motion_sequence(fine_foot=False), self.event, [name]
        )[0]
        self.assertTrue(fine.valid)
        self.assertTrue(fallback.valid)
        self.assertAlmostEqual(fine.value, fallback.value, places=6)
        self.assertGreater(fine.confidence, fallback.confidence)
        self.assertAlmostEqual(
            fallback.provenance["topology_confidence_multiplier"],
            ANKLE_FALLBACK_CONFIDENCE_MULTIPLIER,
        )
        self.assertEqual(
            fallback.smoothed_value["summary"]["fallback_status"],
            "ankle_fallback_only_lower_confidence_not_contact",
        )
        provenance = fallback.provenance["foot_reference_provenance"]
        self.assertEqual(
            provenance["sides"]["left"]["ankle_fallback_frame_count"],
            21,
        )
        self.assertEqual(
            provenance["sides"]["right"]["ankle_fallback_ratio_of_valid"],
            1.0,
        )

    def test_sparse_fine_foot_falls_back_per_frame_and_reports_ratio(self) -> None:
        sequence = _motion_sequence(
            fine_foot=True,
            missing_fine_foot_indexes={8},
        )
        result = compute_event_features(
            sequence,
            self.event,
            ["bilateral_foot_rise_min_body"],
        )[0]
        self.assertTrue(result.valid)
        left = result.raw_value["left_foot_position"]
        fallback = [item for item in left if item["source_frame"] == 108]
        self.assertEqual(fallback[0]["value"], [0.3722624, 0.85592021])
        provenance = result.provenance["foot_reference_provenance"]
        for side in ("left", "right"):
            side_provenance = provenance["sides"][side]
            self.assertEqual(side_provenance["fine_foot_frame_count"], 20)
            self.assertEqual(side_provenance["ankle_fallback_frame_count"], 1)
            self.assertEqual(side_provenance["missing_frame_count"], 0)
            self.assertAlmostEqual(
                side_provenance["ankle_fallback_ratio_of_valid"], 1.0 / 21.0
            )
        self.assertAlmostEqual(
            result.provenance["topology_confidence_multiplier"],
            (20.0 + ANKLE_FALLBACK_CONFIDENCE_MULTIPLIER) / 21.0,
        )
        self.assertEqual(
            result.smoothed_value["summary"]["fallback_status"],
            "mixed_per_frame_ankle_fallback_lower_confidence_not_contact",
        )
        later_event = EventInterval("fs02-later", "FS02", 510, 1175)
        later = compute_event_features(
            sequence,
            later_event,
            ["bilateral_foot_rise_min_body"],
        )[0]
        self.assertEqual(
            later.provenance["topology_confidence_multiplier"], 1.0
        )
        self.assertEqual(
            later.provenance["foot_reference_provenance"]["sides"]["left"][
                "ankle_fallback_frame_count"
            ],
            0,
        )

    def test_excessive_missing_fine_foot_is_unavailable_not_numeric_zero(self) -> None:
        sequence = _motion_sequence(
            fine_foot=True,
            missing_fine_foot_indexes=set(range(12)),
            missing_ankle_indexes=set(range(12)),
        )
        result = compute_event_features(
            sequence,
            self.event,
            ["bilateral_foot_rise_min_body"],
        )[0]
        self.assertFalse(result.valid)
        self.assertEqual(result.reason, "valid_fraction_below_quality_gate")
        missing_values = [
            item["value"]
            for item in result.raw_value["left_foot_position"][:12]
        ]
        self.assertTrue(all(value == [None, None] for value in missing_values))

    def test_definitions_never_claim_contact_force_or_grade_observation(self) -> None:
        for name in FS01_FS02_EVENT_FEATURE_NAMES:
            definition = FEATURE_DEFINITIONS[name]
            self.assertEqual(
                definition["measurement_status"],
                "pose_kinematic_proxy_not_contact_force_or_calibrated_grade",
            )
            text = definition["definition"].lower()
            self.assertNotIn("measured force", text)
            self.assertNotIn("confirmed contact", text)
            self.assertNotIn("grade a", text)


if __name__ == "__main__":
    unittest.main()
