from __future__ import annotations

import hashlib
import json
from pathlib import Path
import tempfile
import unittest

import numpy as np

from rallymate_evaluation.kinematic_continuity import (
    JOINTS,
    evaluate_kinematic_continuity,
    evaluate_kinematic_continuity_files,
)
from rallymate_features.kinematics import (
    CONTIGUOUS_DERIVATIVE_VERSION,
    contiguous_irregular_derivative,
    irregular_derivative,
)
from rallymate_features.schemas import PoseSequence
from test_fixed_boundary_pose_ab import _records, _timeline, _write_jsonl


class ContinuousDerivativeCandidateTests(unittest.TestCase):
    def test_missing_ankle_position_cannot_create_speed_at_occlusion_boundaries(self):
        times = np.arange(7) * 40
        positions = np.asarray([0, 0, 0, np.nan, 1, 1, 1], dtype=float)
        baseline = irregular_derivative(times, positions)
        self.assertGreater(baseline[2], 0)
        self.assertGreater(baseline[4], 0)
        expected = np.asarray([0, 0, 0, np.nan, 0, 0, 0], dtype=float)
        np.testing.assert_array_equal(contiguous_irregular_derivative(times, positions), expected)
        # Adding a candidate must not silently change the published primitive.
        np.testing.assert_array_equal(irregular_derivative(times, positions), baseline)

    def test_long_unsampled_interval_cannot_be_used_as_a_velocity_stencil(self):
        times = np.asarray([0, 40, 80, 1000, 1040, 1080])
        positions = np.column_stack(([0, 0, 0, 1, 1, 1], np.zeros(6)))
        baseline = irregular_derivative(times, positions)
        self.assertGreater(baseline[2, 0], 0)
        self.assertGreater(baseline[3, 0], 0)
        np.testing.assert_array_equal(contiguous_irregular_derivative(times, positions), np.zeros((6, 2)))

    def test_adjacent_irregular_observations_keep_exact_legacy_stencil(self):
        rng = np.random.default_rng(19)
        for shape in ((2,), (51, 2), (600, 2, 3)):
            times = np.cumsum(rng.integers(1, 161, size=shape[0]))
            values = rng.normal(size=shape)
            np.testing.assert_array_equal(contiguous_irregular_derivative(times, values), irregular_derivative(times, values))

    def test_breaks_are_column_specific_and_isolated_observations_stay_missing(self):
        times = np.asarray([0, 40, 80, 120, 160])
        values = np.column_stack(([1, np.nan, 2, np.inf, 3], times / 1000 * 2))
        result = contiguous_irregular_derivative(times, values)
        self.assertTrue(np.isnan(result[:, 0]).all())
        np.testing.assert_array_equal(result[:, 1], irregular_derivative(times, values)[:, 1])

    def test_existing_160ms_limit_is_inclusive(self):
        np.testing.assert_array_equal(contiguous_irregular_derivative([0, 160], [0, 0.32]), [2, 2])
        self.assertTrue(np.isnan(contiguous_irregular_derivative([0, 161], [0, 0.322])).all())
        self.assertTrue(CONTIGUOUS_DERIVATIVE_VERSION.endswith("candidate-v1.0.0"))

    def test_empty_single_and_invalid_time_inputs(self):
        for shape in ((0,), (0, 2), (1, 2), (2, 0)):
            result = contiguous_irregular_derivative(np.arange(shape[0]) * 40, np.zeros(shape))
            self.assertEqual(result.shape, shape)
            self.assertTrue(np.isnan(result).all())
        for times in ([0, 0], [40, 0], [0, np.nan], [0, np.inf], [[0, 40]]):
            with self.subTest(times=times), self.assertRaises(ValueError):
                contiguous_irregular_derivative(times, [1, 2])
        for gap in (0, -1, np.nan, True):
            with self.subTest(gap=gap), self.assertRaises(ValueError):
                contiguous_irregular_derivative([0, 40], [1, 2], max_gap_ms=gap)


class FixedEventContinuityDiagnosticTests(unittest.TestCase):
    @staticmethod
    def sequence():
        times = np.asarray([0, 40, 80, 1000, 1040, 1080])
        values = np.column_stack(([0, 0, 0, 1, 1, 1], np.zeros(6)))
        return PoseSequence(times, np.arange(6), {joint: values.copy() for joint in JOINTS},
                            {joint: np.ones(6) for joint in JOINTS})

    @staticmethod
    def event(start=0, end=1080):
        return {"event_id": f"test-{start}", "event_code": "FS09", "person_track_id": 1,
                "start_ms": start, "end_ms": end, "key_phases_ms": {"peak_speed_ms": 80}}

    def test_fixed_boundary_comparison_preserves_event_and_explains_source_support(self):
        event = self.event()
        original = json.dumps(event, sort_keys=True)
        report = evaluate_kinematic_continuity(self.sequence(), [event])
        row = report["events"][0]["comparisons"]["left_ankle/raw_observations"]
        self.assertEqual(row["changed_sample_count"], 2)
        self.assertEqual(row["candidate_peak_image_speed"], 0)
        self.assertGreater(row["baseline_peak_image_speed"], 0)
        self.assertEqual(row["changed_samples"][0]["baseline_stencil_timestamp_ms"], [40, 1000])
        self.assertTrue(row["changed_samples"][0]["baseline_stencil_crosses_long_interval"])
        self.assertFalse(report["candidate_active_in_production"])
        self.assertIsNone(report["accuracy"])
        self.assertIsNone(report["technical_score"])
        self.assertFalse(report["event_boundaries_changed"])
        self.assertEqual(json.dumps(event, sort_keys=True), original)

    def test_no_borrowing_across_event_boundaries(self):
        report = evaluate_kinematic_continuity(self.sequence(), [self.event(0, 80), self.event(1000, 1080)])
        self.assertEqual(report["changed_event_count"], 0)
        self.assertEqual(report["changed_joint_mode_sample_count"], 0)

    def test_no_observed_event_samples_do_not_become_zero_speed(self):
        report = evaluate_kinematic_continuity(self.sequence(), [self.event(400, 600)])
        row = report["events"][0]["comparisons"]["left_knee/raw_observations"]
        self.assertIsNone(row["baseline_peak_image_speed"])
        self.assertIsNone(row["candidate_peak_image_speed"])

    def test_file_entrypoint_binds_sources_and_refuses_overwrite(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            records = _records(1, 0.0)
            for index, record in enumerate(records):
                if 4 <= index <= 7:
                    record["poses"] = []
                elif index > 7:
                    for point in record["poses"][0]["keypoints"]:
                        point["x_normalized"] += 0.1
            _write_jsonl(root/"frames.jsonl", records)
            _write_jsonl(root/"primary-player.jsonl", _timeline(1))
            _write_jsonl(root/"events.jsonl", [self.event(0, 1100)])
            before = {name: (root/name).read_bytes() for name in ("frames.jsonl", "primary-player.jsonl", "events.jsonl")}
            report = evaluate_kinematic_continuity_files(root, root/"candidate.json")
            self.assertGreater(report["changed_joint_mode_sample_count"], 0)
            for name, contents in before.items():
                self.assertEqual((root/name).read_bytes(), contents)
                self.assertEqual(report["sources"][name]["sha256"], hashlib.sha256(contents).hexdigest())
            with self.assertRaises(FileExistsError):
                evaluate_kinematic_continuity_files(root, root/"frames.jsonl")
            with self.assertRaises(FileExistsError):
                evaluate_kinematic_continuity_files(root, root/"candidate.json")


if __name__ == "__main__":
    unittest.main()
