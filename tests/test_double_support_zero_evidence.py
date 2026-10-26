"""A zero ankle-motion proxy binds evaluated samples, not an observed contact run."""
from __future__ import annotations

import unittest
from unittest.mock import patch

import numpy as np

from rallymate_features.coordinates import pose_sequence_from_records
from rallymate_features.event_features import compute_event_features
from rallymate_features.fs09_features import double_support_low_motion_proxy
from rallymate_features.schemas import EventInterval
from rallymate_vision.validation import OutputValidationError, _validate_feature_result
from tests.test_events import _motion_sequence
from tests.test_sam_footwork import sam_motion_fixture


NAME = "double_support_proxy_duration_ms"


def _model_sequence(count=8):
    records, timeline = sam_motion_fixture()
    return pose_sequence_from_records(records[:count], timeline[:count])


def _series(left, right):
    left, right = np.asarray(left, dtype=float), np.asarray(right, dtype=float)
    values = {"left_ankle_speed": left, "right_ankle_speed": right,
              "left_ankle_slowdown": np.zeros_like(left),
              "right_ankle_slowdown": np.zeros_like(right)}
    return values, values


def _feature(sequence, left, right, start=0, end=None):
    end = len(sequence.timestamp_ms) - 1 if end is None else end
    event = EventInterval("zero-support", "FS09", int(sequence.timestamp_ms[start]),
                          int(sequence.timestamp_ms[end]), person_track_id=1)
    with patch("rallymate_features.fs09_features._base_event_series",
               return_value=_series(left, right)):
        return compute_event_features(sequence, event, [NAME])[0]


def _raw_countermoving_ankles(missing_left=()):
    """Actual conversion/derivatives/smoothing; synthetic geometry only."""
    records, timeline = sam_motion_fixture()
    records, timeline = records[:8], timeline[:8]
    positions = {"left_ankle": 150 + np.cumsum(np.arange(8, 0, -1.))*3,
                 "right_ankle": 600 + np.cumsum(np.arange(1., 9.))*3}
    for index, record in enumerate(records):
        for point in record["poses"][0]["keypoints"]:
            if point["name"] in positions:
                point["raw_x_px"] = point["x_px"] = float(positions[point["name"]][index])
                point["raw_y_px"] = point["y_px"] = 800.
                if point["name"] == "left_ankle" and index in missing_left:
                    point["in_frame"] = False
    return pose_sequence_from_records(records, timeline)


class DoubleSupportZeroEvidenceTests(unittest.TestCase):
    def test_helper_distinguishes_zero_evaluation_from_support_interval(self):
        duration, endpoints, mask, diagnostics = double_support_low_motion_proxy(
            np.arange(8) * 40, np.arange(8, 0, -1.), np.arange(1., 9.), np.arange(8))
        self.assertEqual(duration, 0)
        self.assertEqual(endpoints, ())
        self.assertFalse(mask.any())
        self.assertEqual(diagnostics["evaluated_sample_indexes"], list(range(8)))
        self.assertFalse(diagnostics["simultaneous_low_motion_interval_observed"])
        self.assertEqual(diagnostics["contact_status"], "not_observed_from_pose")

    def test_helper_positive_run_retains_actual_endpoints_and_duration(self):
        duration, endpoints, mask, diagnostics = double_support_low_motion_proxy(
            np.array([0, 70, 180, 330, 450, 580]),
            np.array([1.2, 1., .6, .2, .1, .1]),
            np.array([1., .9, .7, .25, .1, .1]), np.arange(6))
        self.assertEqual((duration, endpoints), (370, (3, 5)))
        self.assertEqual(mask.tolist(), [False, False, False, True, True, True])
        self.assertNotIn("evaluated_sample_indexes", diagnostics)

    def test_helper_fewer_than_three_finite_pairs_remains_missing(self):
        for left, right in (([1., 2., np.nan, np.nan], [2., 1., 3., 4.]),
                            ([np.nan]*4, [1.]*4)):
            with self.subTest(left=left):
                duration, endpoints, mask, diagnostics = double_support_low_motion_proxy(
                    np.arange(4)*40, np.array(left), np.array(right), np.arange(4))
                self.assertIsNone(duration)
                self.assertEqual(endpoints, ())
                self.assertFalse(mask.any())
                self.assertNotIn("evaluated_sample_indexes", diagnostics)

    def test_model_zero_passes_strict_validator_with_unknown_confidence_and_identity(self):
        result = _feature(_model_sequence(), np.arange(8, 0, -1.), np.arange(1., 9.))
        self.assertTrue(result.valid)
        self.assertEqual(result.value, 0)
        self.assertEqual(result.source_frames, list(range(8)))
        self.assertIsNone(result.confidence)
        self.assertEqual(result.provenance["person_track_id"], 1)
        self.assertEqual(result.provenance["source_window"]["person_track_id"], 7)
        self.assertEqual(result.provenance["source_frame_semantics"],
                         "evaluated_samples_no_simultaneous_low_motion_run")
        self.assertFalse(result.provenance["simultaneous_low_motion_interval_observed"])
        self.assertFalse(result.provenance["measurement_provenance"]["scoring_allowed"])
        self.assertEqual(result.provenance["measurement_provenance"]["assessment_status"], "measurement_only")
        payload = result.to_dict()
        _validate_feature_result(payload, "repaired")
        payload["source_frames"] = []
        with self.assertRaisesRegex(OutputValidationError, "requires source frames"):
            _validate_feature_result(payload, "unrepaired")

    def test_only_finite_evaluated_samples_in_event_are_bound(self):
        sequence = _model_sequence(12)
        left = np.full(12, np.nan)
        right = np.full(12, np.nan)
        selected = [2, 4, 5, 6, 7, 9]
        left[selected], right[selected] = np.arange(6, 0, -1.), np.arange(1., 7.)
        result = _feature(sequence, left, right, start=2, end=9)
        self.assertTrue(result.valid)
        self.assertEqual(result.value, 0)
        self.assertEqual(result.source_frames, selected)
        self.assertEqual(result.provenance["source_window"]["source_frames"], list(range(2, 10)))
        self.assertEqual(result.provenance["valid_fraction"], .75)
        _validate_feature_result(result.to_dict(), "finite_subset")

    def test_existing_half_window_gate_is_preserved(self):
        for count, expected_valid in ((8, True), (10, False)):
            with self.subTest(count=count):
                left, right = np.full(count, np.nan), np.full(count, np.nan)
                left[:4], right[:4] = [4., 3., 2., 1.], [1., 2., 3., 4.]
                result = _feature(_model_sequence(count), left, right)
                self.assertEqual(result.valid, expected_valid)
                self.assertEqual(result.value, 0)
                self.assertEqual(result.source_frames, [0, 1, 2, 3] if expected_valid else [])
                if not expected_valid:
                    self.assertEqual(result.reason, "valid_fraction_below_quality_gate")
                    self.assertNotIn("source_frame_semantics", result.provenance)

    def test_model_fewer_than_three_pairs_or_all_missing_does_not_become_zero(self):
        for left in ([1., 2.] + [np.nan]*6, [np.nan]*8):
            with self.subTest(left=left):
                result = _feature(_model_sequence(), left, np.arange(8, 0, -1.))
                self.assertFalse(result.valid)
                self.assertIsNone(result.value)
                self.assertEqual(result.source_frames, [])
                self.assertNotIn("source_frame_semantics", result.provenance)
                self.assertIsNone(result.confidence)

    def test_missing_ankle_geometry_remains_unavailable_without_patched_series(self):
        records, timeline = sam_motion_fixture()
        for row in records:
            next(p for p in row["poses"][0]["keypoints"] if p["name"] == "left_ankle")["in_frame"] = False
        sequence = pose_sequence_from_records(records[:8], timeline[:8])
        result = compute_event_features(sequence, EventInterval("missing", "FS09", 0, 280), [NAME])[0]
        self.assertFalse(result.valid)
        self.assertIsNone(result.value)
        self.assertEqual(result.source_frames, [])
        self.assertNotIn("source_frame_semantics", result.provenance)

    def test_interpolated_speed_cannot_bind_a_missing_raw_ankle_frame(self):
        sequence = _raw_countermoving_ankles(missing_left=(3,))
        result = compute_event_features(sequence, EventInterval("partial", "FS09", 0, 280), [NAME])[0]
        self.assertTrue(result.valid)
        self.assertEqual(result.value, 0)
        self.assertEqual(result.source_frames, [0, 1, 2, 4, 5, 6, 7])
        self.assertIsNone(result.raw_value["left_ankle_speed"][3]["value"])
        self.assertIsNotNone(result.smoothed_value["series"]["left_ankle_speed"][3]["value"])
        self.assertFalse(result.provenance["geometry_mask"]["left_ankle"][3])
        self.assertEqual(result.provenance["source_window"]["source_frames"], list(range(8)))
        _validate_feature_result(result.to_dict(), "partial_raw_geometry")

    def test_fewer_than_three_original_geometry_samples_cannot_become_valid_zero(self):
        sequence = _raw_countermoving_ankles(missing_left=(1, 2, 3, 4, 5, 6))
        result = compute_event_features(sequence, EventInterval("few", "FS09", 0, 280), [NAME])[0]
        self.assertFalse(result.valid)
        self.assertIsNone(result.value)
        self.assertEqual(result.source_frames, [])
        self.assertNotIn("source_frame_semantics", result.provenance)

    def test_all_eight_original_geometry_samples_remain_bound(self):
        result = compute_event_features(_raw_countermoving_ankles(),
            EventInterval("complete", "FS09", 0, 280), [NAME])[0]
        self.assertTrue(result.valid)
        self.assertEqual(result.value, 0)
        self.assertEqual(result.source_frames, list(range(8)))
        self.assertIsNone(result.confidence)

    def test_identity_or_selection_epoch_break_cannot_bind_evaluated_window(self):
        for defect in ("identity", "selection_epoch"):
            records, timeline = sam_motion_fixture()
            if defect == "identity":
                timeline[4]["identity_ambiguous"] = True
            else:
                for record, selected in zip(records[4:], timeline[4:]):
                    record["poses"][0]["source_binding"]["selection_epoch"] = 4
                    record["sam3d_requests"][0]["selection_epoch"] = 4
                    selected["selection_epoch"] = 4
            sequence = pose_sequence_from_records(records[:8], timeline[:8])
            with self.subTest(defect=defect):
                result = _feature(sequence, np.arange(8, 0, -1.), np.arange(1., 9.))
                self.assertFalse(result.valid)
                self.assertIsNone(result.value)
                self.assertEqual(result.source_frames, [])
                self.assertEqual(result.reason, "event_crosses_unverified_camera_window")
                self.assertNotIn("source_frame_semantics", result.provenance)

    def test_positive_model_duration_does_not_use_zero_evidence_fallback(self):
        result = _feature(_model_sequence(), np.arange(8, 0, -1.), np.arange(8, 0, -1.))
        self.assertTrue(result.valid)
        self.assertEqual(result.value, 160)
        self.assertEqual(result.source_frames, [4, 7])
        self.assertNotIn("source_frame_semantics", result.provenance)
        self.assertNotIn("simultaneous_low_motion_interval_observed", result.provenance)

    def test_ordinary_confidence_path_keeps_existing_zero_evidence_behavior(self):
        result = _feature(_motion_sequence().slice(slice(0, 8)), np.arange(8, 0, -1.), np.arange(1., 9.))
        self.assertTrue(result.valid)
        self.assertEqual(result.value, 0)
        self.assertEqual(result.source_frames, [])
        self.assertIsNotNone(result.confidence)
        self.assertNotIn("source_frame_semantics", result.provenance)


if __name__ == "__main__":
    unittest.main()
