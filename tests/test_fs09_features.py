from __future__ import annotations

import json
import unittest

import numpy as np

from rallymate_features.event_features import (
    FEATURE_DEFINITIONS,
    compute_event_features,
)
from rallymate_features.fs09_features import (
    _BASE_EVENT_SERIES_CACHE,
    clear_fs09_feature_cache,
    summarize_fs09_event_feature,
)
from rallymate_features.fs09_features import (
    FS09_EVENT_FEATURE_NAMES,
    FS09_FEATURE_VERSION,
    FS09_SERIES_FEATURE_NAMES,
    braking_ankle_slowdown_to_hip_deceleration_from_series,
    braking_side_from_speed_drops,
    double_support_low_motion_proxy,
    event_edge_change,
    hip_deceleration_to_double_support_proxy_from_series,
    hip_center_velocity_body_s,
)
from rallymate_features.schemas import EventInterval, PoseSequence


JOINTS = (
    "left_shoulder",
    "right_shoulder",
    "left_hip",
    "right_hip",
    "left_knee",
    "right_knee",
    "left_ankle",
    "right_ankle",
)


def _braking_sequence(*, missing_left_ankle_at: int | None = None) -> PoseSequence:
    # Deliberately irregular intervals.  Every motion derivative must use these
    # timestamps rather than infer a fixed FPS from the frame indexes.
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
    points = {name: [] for name in JOINTS}
    confidence = {name: [] for name in JOINTS}
    end_s = timestamps[-1] / 1000.0
    for frame_index, timestamp in enumerate(timestamps):
        t = timestamp / 1000.0
        # Hip proxy continues image-right but its speed falls throughout FS09.
        hip_x = 0.50 + 0.25 * t - 0.10 * t * t
        hip_y = 0.55 + 0.06 * t / end_s
        # Left ankle has the larger early-to-late speed drop.  This is enough
        # for a kinematic-side diagnostic, not a true landing-side label.
        left_ankle_x = 0.35 + 0.30 * min(t, 0.50)
        right_ankle_x = 0.65 + 0.10 * min(t, 0.85)
        bend = 0.01 + 0.055 * t / end_s
        shoulder_axis_tilt = 0.025 * np.sin(11.0 * t) * max(0.0, 1.0 - t / end_s)
        torso_shift = 0.025 * np.sin(9.0 * t) * max(0.0, 1.0 - t / end_s)
        coordinates = {
            "left_hip": (hip_x - 0.05, hip_y),
            "right_hip": (hip_x + 0.05, hip_y),
            "left_ankle": (left_ankle_x, 0.90),
            "right_ankle": (right_ankle_x, 0.90),
            "left_knee": (
                ((hip_x - 0.05) + left_ankle_x) / 2.0 - bend,
                (hip_y + 0.90) / 2.0,
            ),
            "right_knee": (
                ((hip_x + 0.05) + right_ankle_x) / 2.0 + bend,
                (hip_y + 0.90) / 2.0,
            ),
            "left_shoulder": (
                hip_x + torso_shift - 0.10,
                hip_y - 0.25 + shoulder_axis_tilt,
            ),
            "right_shoulder": (
                hip_x + torso_shift + 0.10,
                hip_y - 0.25 - shoulder_axis_tilt,
            ),
        }
        for name in JOINTS:
            if name == "left_ankle" and frame_index == missing_left_ankle_at:
                points[name].append((np.nan, np.nan))
                confidence[name].append(np.nan)
            else:
                points[name].append(coordinates[name])
                confidence[name].append(0.95)
    return PoseSequence(
        timestamp_ms=timestamps,
        source_frames=np.arange(timestamps.size, dtype=np.int64),
        keypoints_xy={name: np.asarray(value) for name, value in points.items()},
        confidence={name: np.asarray(value) for name, value in confidence.items()},
    )


class FS09PureFunctionTests(unittest.TestCase):
    def test_event_edge_change_is_robust_median_delta(self) -> None:
        values = np.asarray([10.0, 9.0, 8.0, 3.0, 2.0, 1.0])
        value, evidence, diagnostics = event_edge_change(
            values,
            np.arange(values.size),
            early_minus_late=True,
        )
        self.assertEqual(value, 8.0)
        self.assertEqual(len(evidence), 2)
        self.assertEqual(diagnostics["early_median"], 9.5)
        self.assertEqual(diagnostics["late_median"], 1.5)

    def test_braking_side_is_explicit_numeric_proxy(self) -> None:
        self.assertEqual(braking_side_from_speed_drops(0.8, 0.2)[0], -1)
        self.assertEqual(braking_side_from_speed_drops(0.2, 0.8)[0], 1)
        self.assertEqual(braking_side_from_speed_drops(-0.1, -0.2)[0], 0)
        self.assertIsNone(braking_side_from_speed_drops(None, 0.2)[0])

    def test_braking_side_uses_local_slowdown_when_net_drop_is_nonpositive(self) -> None:
        side, reason = braking_side_from_speed_drops(
            -0.4,
            -0.2,
            left_peak_slowdown=0.7,
            right_peak_slowdown=1.4,
        )
        self.assertEqual(side, 1)
        self.assertEqual(
            reason,
            "right_peak_slowdown_fallback_without_positive_net_drop",
        )

        tied_side, tied_reason = braking_side_from_speed_drops(
            -0.1,
            -0.3,
            left_peak_slowdown=1.0,
            right_peak_slowdown=1.0,
        )
        self.assertEqual(tied_side, -1)
        self.assertEqual(
            tied_reason,
            "left_less_negative_net_drop_breaks_equal_peak_tie",
        )

        no_signal_side, no_signal_reason = braking_side_from_speed_drops(
            0.0,
            0.0,
            left_peak_slowdown=None,
            right_peak_slowdown=None,
        )
        self.assertEqual(no_signal_side, 0)
        self.assertEqual(
            no_signal_reason,
            "no_positive_net_drop_or_local_slowdown",
        )

    def test_double_support_proxy_uses_timestamp_duration(self) -> None:
        timestamps = np.asarray([0, 70, 180, 330, 530, 780], dtype=np.int64)
        left = np.asarray([1.2, 1.0, 0.6, 0.2, 0.1, 0.1])
        right = np.asarray([1.0, 0.9, 0.7, 0.25, 0.1, 0.1])
        duration, evidence, mask, diagnostics = double_support_low_motion_proxy(
            timestamps, left, right, np.arange(timestamps.size)
        )
        self.assertEqual(evidence, (3, 5))
        # 780 - 330 + median([70,110,150,200,250]) = 600 ms.
        self.assertEqual(duration, 600)
        self.assertTrue(mask[3:].all())
        self.assertEqual(diagnostics["contact_status"], "not_observed_from_pose")
        self.assertIn("not_scoring_threshold", diagnostics["envelope_status"])

    def test_phase_timing_pure_functions_use_irregular_timestamps(self) -> None:
        timestamps = np.asarray([0, 100, 250, 500, 900], dtype=np.int64)
        indexes = np.arange(timestamps.size, dtype=np.int64)
        braking, evidence, diagnostics = (
            braking_ankle_slowdown_to_hip_deceleration_from_series(
                timestamps,
                np.asarray([5.0, 4.0, 3.0, 2.0, 1.0]),
                np.asarray([2.0, 2.0, 2.0, 2.0, 2.0]),
                np.asarray([0.0, 2.0, 5.0, 1.0, 0.0]),
                np.asarray([0.0, 0.0, 0.0, 0.0, 0.0]),
                np.asarray([0.0, 0.0, 1.0, 6.0, 0.0]),
                indexes,
            )
        )
        self.assertEqual(braking, 250)
        self.assertEqual(evidence, (2, 3))
        self.assertEqual(diagnostics["selected_side"], "left")

        support, evidence, mask, diagnostics = (
            hip_deceleration_to_double_support_proxy_from_series(
                timestamps,
                np.asarray([3.0, 2.0, 0.2, 0.1, 0.1]),
                np.asarray([3.0, 2.0, 0.3, 0.1, 0.1]),
                np.asarray([0.0, 5.0, 1.0, 0.0, 0.0]),
                indexes,
            )
        )
        self.assertEqual(support, 150)
        self.assertEqual(evidence, (1, 2))
        self.assertTrue(mask[2:].all())
        self.assertEqual(diagnostics["contact_status"], "not_observed_from_pose")

    def test_phase_timing_pure_functions_fail_closed_on_misaligned_inputs(self) -> None:
        timestamps = np.asarray([0, 100, 90], dtype=np.int64)
        values = np.asarray([3.0, 2.0, 1.0])
        result, evidence, diagnostics = (
            braking_ankle_slowdown_to_hip_deceleration_from_series(
                timestamps,
                values,
                values,
                values,
                values,
                values,
                np.arange(3, dtype=np.int64),
            )
        )
        self.assertIsNone(result)
        self.assertEqual(evidence, ())
        self.assertEqual(
            diagnostics["input_status"],
            "invalid_or_misaligned_timing_series",
        )

        result, evidence, mask, diagnostics = (
            hip_deceleration_to_double_support_proxy_from_series(
                np.asarray([0, 100, 250], dtype=np.int64),
                values,
                values[:2],
                values,
                np.arange(3, dtype=np.int64),
            )
        )
        self.assertIsNone(result)
        self.assertEqual(evidence, ())
        self.assertFalse(mask.any())
        self.assertEqual(
            diagnostics["input_status"],
            "invalid_or_misaligned_timing_series",
        )

    def test_hip_velocity_respects_irregular_timestamp_spacing(self) -> None:
        sequence = _braking_sequence()
        velocity = hip_center_velocity_body_s(sequence, 0.20)[:, 0]
        finite = velocity[np.isfinite(velocity)]
        # The generated derivative decreases with physical time.  A fixed-frame
        # derivative would instead follow the alternating timestamp gaps.
        self.assertGreater(finite[2], finite[-3])
        self.assertGreater(finite[2] - finite[-3], 0.7)


class FS09EventFeatureIntegrationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.sequence = _braking_sequence()
        self.event = EventInterval("fs09-1", "FS09", 0, 1175)

    def test_all_fs09_features_are_versioned_and_machine_readable(self) -> None:
        names = sorted(FS09_SERIES_FEATURE_NAMES | FS09_EVENT_FEATURE_NAMES)
        results = compute_event_features(self.sequence, self.event, names)
        self.assertEqual(len(results), len(names))
        for result in results:
            self.assertEqual(result.feature_version, FS09_FEATURE_VERSION)
            self.assertTrue(result.valid, result.feature_name)
            self.assertIsNotNone(result.value, result.feature_name)
            self.assertTrue(result.source_frames, result.feature_name)
            self.assertEqual(
                result.provenance["timestamp_source"],
                "timestamp_ms",
            )
            self.assertIn(result.feature_name, FEATURE_DEFINITIONS)
            json.dumps(result.to_dict(), allow_nan=False)

    def test_braking_absorption_and_stability_proxies_have_expected_signs(self) -> None:
        names = [
            "braking_side_code",
            "braking_ankle_speed_drop_body_s",
            "hip_center_speed_drop_body_s",
            "left_knee_flexion_change_deg",
            "right_knee_flexion_change_deg",
            "hip_height_delta_body",
            "torso_lean_variability_deg",
            "double_support_proxy_duration_ms",
            "braking_ankle_slowdown_to_hip_deceleration_ms",
            "hip_deceleration_to_double_support_proxy_ms",
        ]
        by_name = {
            result.feature_name: result
            for result in compute_event_features(self.sequence, self.event, names)
        }
        self.assertEqual(by_name["braking_side_code"].value, -1)
        self.assertGreater(by_name["braking_ankle_speed_drop_body_s"].value, 0.0)
        self.assertGreater(by_name["hip_center_speed_drop_body_s"].value, 0.0)
        self.assertGreater(by_name["left_knee_flexion_change_deg"].value, 0.0)
        self.assertGreater(by_name["right_knee_flexion_change_deg"].value, 0.0)
        self.assertLess(by_name["hip_height_delta_body"].value, 0.0)
        self.assertGreater(by_name["torso_lean_variability_deg"].value, 0.0)
        self.assertGreater(by_name["double_support_proxy_duration_ms"].value, 0)
        self.assertIsInstance(
            by_name["braking_ankle_slowdown_to_hip_deceleration_ms"].value,
            int,
        )
        self.assertIsInstance(
            by_name["hip_deceleration_to_double_support_proxy_ms"].value,
            int,
        )
        side = by_name["braking_side_code"]
        self.assertIn("not_ground_contact", side.reason)
        self.assertEqual(
            side.smoothed_value["summary"]["selected_side"],
            "left",
        )
        self.assertEqual(
            side.provenance["measurement_status"],
            "pose_proxy_not_calibrated_for_grade",
        )
        self.assertIsNone(side.provenance["score_threshold_version"])

        for feature_name in (
            "braking_ankle_slowdown_to_hip_deceleration_ms",
            "hip_deceleration_to_double_support_proxy_ms",
        ):
            feature = by_name[feature_name]
            self.assertEqual(
                feature.provenance["evidence_contract"],
                "fs09_phase_timing_raw_speed_and_prepared_series_v1",
            )
            self.assertIn("hip_slowdown", feature.smoothed_value["series"])
            self.assertNotIn("hip_deceleration", feature.smoothed_value["series"])

    def test_missing_keypoint_is_null_in_evidence_not_numeric_zero(self) -> None:
        sequence = _braking_sequence(missing_left_ankle_at=8)
        result = compute_event_features(
            sequence,
            self.event,
            ["left_ankle_speed_drop_body_s"],
        )[0]
        self.assertTrue(result.valid)
        missing = result.raw_value["left_ankle_speed"]
        missing = [item for item in missing if item["source_frame"] == 8]
        self.assertEqual(len(missing), 1)
        self.assertIsNone(missing[0]["value"])

    def test_no_feature_definition_claims_contact_force_or_grade(self) -> None:
        double_support = FEATURE_DEFINITIONS["double_support_proxy_duration_ms"]
        braking_side = FEATURE_DEFINITIONS["braking_side_code"]
        self.assertIn("not ground contact", double_support["definition"])
        self.assertIn("not measured landing", braking_side["definition"])
        for name in FS09_SERIES_FEATURE_NAMES | FS09_EVENT_FEATURE_NAMES:
            definition = FEATURE_DEFINITIONS[name]
            self.assertNotIn("grade A", definition["definition"])
            self.assertNotIn("ground reaction force measured", definition["definition"])

    def test_repeated_event_summaries_reuse_and_clear_full_sequence_cache(self) -> None:
        sequence = _braking_sequence()
        indexes = np.arange(sequence.timestamp_ms.size, dtype=np.int64)
        clear_fs09_feature_cache(sequence)
        summarize_fs09_event_feature(
            "hip_center_speed_drop_body_s", sequence, indexes, 1.0
        )
        matching = [
            cached
            for cached in _BASE_EVENT_SERIES_CACHE.values()
            if cached[0] is sequence
        ]
        self.assertEqual(len(matching), 1)
        raw_id = id(matching[0][1])
        summarize_fs09_event_feature(
            "left_knee_flexion_change_deg", sequence, indexes, 1.0
        )
        matching = [
            cached
            for cached in _BASE_EVENT_SERIES_CACHE.values()
            if cached[0] is sequence
        ]
        self.assertEqual(id(matching[0][1]), raw_id)
        clear_fs09_feature_cache(sequence)
        self.assertFalse(
            any(cached[0] is sequence for cached in _BASE_EVENT_SERIES_CACHE.values())
        )


if __name__ == "__main__":
    unittest.main()
