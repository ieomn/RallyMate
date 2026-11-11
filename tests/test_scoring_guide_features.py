from dataclasses import replace
import json
import unittest

import numpy as np

from rallymate_features.event_features import compute_event_features, MIN_VALID_FRACTION
from rallymate_features.fs01_fs02_features import source_stage_measurement, target_direction_alignment
from rallymate_features.fs09_features import braking_phase_candidates_from_series
from rallymate_features.perception_dictionary import normalize_perception_values, resolve_perception_ids, perception_definition
from rallymate_features.schemas import EventInterval
from rallymate_features.validity import meets_keypoint_frame_coverage
from tests.test_source_aligned_features import fixture, event, feature
from rallymate_features.source_alignment import compute_source_aligned_features


class ScoringGuideFeaturesTests(unittest.TestCase):
    def test_dictionary_aliases_do_not_duplicate_ambiguous_scalar(self):
        self.assertEqual(resolve_perception_ids("BALL-M06"), ("BALL-025",))
        self.assertEqual(len(resolve_perception_ids("S013")), 2)
        self.assertEqual(normalize_perception_values({"S013": .4}), {"S013": .4})
        self.assertEqual(normalize_perception_values({"S013": {"base_width": 2, "weight_distribution": .4}})["S013_BASE_WIDTH"], 2)
        self.assertEqual(perception_definition("J179")["semantic_status"], "reserved")
        with self.assertRaises(ValueError):
            normalize_perception_values({"BALL-M01": 3, "BALL-020": 4})

    def test_seventy_percent_is_inclusive_and_does_not_change_point_confidence(self):
        self.assertEqual(MIN_VALID_FRACTION, .7)
        self.assertTrue(meets_keypoint_frame_coverage(7, 10))
        self.assertFalse(meets_keypoint_frame_coverage(6, 10))
        self.assertFalse(meets_keypoint_frame_coverage(0, 0))

    def test_preload_measures_delta_and_does_not_include_later_motion(self):
        sequence, _, _ = fixture()
        indexes = np.arange(len(sequence.timestamp_ms))
        phases = {"observation_start_ms": 40, "preload_ms": 160}
        value = source_stage_measurement("hip_center_y_drop_delta_body", sequence, indexes, .2, phases)
        self.assertAlmostEqual(value.value, .2)
        bent = source_stage_measurement("knee_flexion_increase_deg", sequence, indexes, .2, phases)
        self.assertGreater(bent.value, 0)
        sequence.keypoints_xy["left_hip"][10:, 1] += 20
        after = source_stage_measurement("hip_center_y_drop_delta_body", sequence, indexes, .2, phases)
        self.assertEqual(after.value, value.value)
        self.assertIsNone(source_stage_measurement("hip_center_y_drop_delta_body", sequence, indexes, .2, {}).value)

    def test_forefoot_ratio_is_not_ankle_width_or_later_first_step(self):
        sequence, _, _ = fixture()
        points, confidence = dict(sequence.keypoints_xy), dict(sequence.confidence)
        count = len(sequence.timestamp_ms)
        for side, x in (("left", .1), ("right", .9)):
            for toe in ("big_toe", "small_toe"):
                name = f"{side}_{toe}"
                points[name] = np.tile([x, .95], (count, 1))
                points[name][21:, 0] += 9
                confidence[name] = np.full(count, .95)
        sequence = replace(sequence, keypoints_xy=points, confidence=confidence)
        phases = {"landing_proxy_ms": 480, "initiation_ms": 800}
        result = source_stage_measurement("post_landing_forefoot_width_shoulder_ratio", sequence, np.arange(count), .2, phases)
        self.assertAlmostEqual(result.value, 2.)
        self.assertTrue(max(result.evidence_indexes) <= 20)
        public = compute_event_features(sequence, EventInterval("s", "FS01", 0, 1600, key_phases=phases), ["post_landing_forefoot_width_shoulder_ratio"])[0]
        self.assertTrue(public.valid, public.reason)
        json.dumps(public.to_dict(), allow_nan=False)
        del points["left_small_toe"]
        missing = replace(sequence, keypoints_xy=points)
        self.assertIsNone(source_stage_measurement("post_landing_forefoot_width_shoulder_ratio", missing, np.arange(count), .2, phases).value)

    def test_target_direction_cannot_reward_running_away(self):
        self.assertFalse(target_direction_alignment(0, None)["direction_score_applicable"])
        self.assertEqual(target_direction_alignment(180, (1, 0))["target_direction_alignment_error_deg"], 180)
        self.assertEqual(target_direction_alignment(90, (0, -1))["target_direction_alignment_error_deg"], 0)

    def test_braking_anchor_requires_sustained_local_slowdown(self):
        times = np.arange(5) * 40
        result = braking_phase_candidates_from_series(times, np.array([3, 2, 1, 1, 1]), np.ones(5), np.arange(5))
        self.assertEqual(result["braking_foot_strike_ms"], 40)
        self.assertEqual(result["braking_side"], "left")
        self.assertIsNone(braking_phase_candidates_from_series(times, np.ones(5), np.ones(5), np.arange(5))["braking_foot_strike_ms"])
        self.assertIsNone(braking_phase_candidates_from_series(times * 10, np.array([3, 2, 1, 1, 1]), np.ones(5), np.arange(5))["braking_foot_strike_ms"])

    def test_local_stability_is_independent_of_successor(self):
        sequence, events, _ = fixture()
        stop = events[2]
        a = compute_source_aligned_features(sequence, stop, events=[stop])
        successor = event("early", "FS10", 1040, 1160)
        b = compute_source_aligned_features(sequence, stop, events=[stop, successor])
        name = "stable_control_body_speed_std_body_s"
        self.assertEqual(feature(a, "FS09-M05", name)["value"], feature(b, "FS09-M05", name)["value"])
        self.assertEqual(feature(b, "FS09-M05", name)["window_ms"]["end_ms"], 1200)


if __name__ == "__main__":
    unittest.main()
