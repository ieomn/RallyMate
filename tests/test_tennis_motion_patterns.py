"""Synthetic counterfactuals, never player identities or desired rankings."""
from copy import deepcopy
import json
from pathlib import Path
import unittest

import numpy as np

from rallymate_service.tennis_motion_patterns import (
    analyze_motion_pattern, PATTERN_METRICS, SOURCE_REQUIREMENTS, TECHNIQUES,
)


def fixture():
    times = np.arange(0., 1601., 20.)
    observations, rows = [], []
    for i, t in enumerate(times):
        center = np.array([.15 * t / 1600, 0.])
        hip = np.deg2rad(30 * np.arctan((t - 600) / 65))
        shoulder = np.deg2rad(40 * np.arctan((t - 760) / 65))
        hand = np.array([.8 * np.arctan((t - 900) / 65), 1.2])
        points = {}
        for name, midpoint, angle, span in (("hip", center, hip, .6), ("shoulder", center + [0., 1.], shoulder, .9)):
            vector = span / 2 * np.array([np.cos(angle), np.sin(angle)])
            points["left_" + name] = midpoint - vector
            points["right_" + name] = midpoint + vector
        points.update(right_wrist=center + hand, left_wrist=center + hand + [.1, .05],
                      right_elbow=center + [.5, .8], left_elbow=center + [.4, .8])
        observations.append({"time": t, "frame": i, "track": 1, "selection_epoch": 0,
                             "camera_reference_epoch": 0, "points": points})
        rows.append({"time": t, "frame": i, "local": {key: np.r_[value - center, 0.] for key, value in points.items()}})
    context = {"coordinate_system": "fixed_camera_image_plane", "camera_reference_verified": True,
               "source_binding_verified": True, "scale": 1., "video_sha256": "a" * 64,
               "track": 1, "selection_epoch": 0, "camera_reference_epoch": 0,
               "forward_direction_2d": [1., 0.], "forward_direction_2d_verified": True,
               "up_direction_2d": [0., 1.], "up_direction_2d_verified": True}
    return rows, observations, context


def calculate(data=None, technique="baseline_forehand", **kwargs):
    rows, observations, context = data or fixture()
    return analyze_motion_pattern(rows, prep_end=25, peak=50, side="right", technique=technique,
                                  classification_verified=True, source_observations=observations,
                                  source_context=context, **kwargs)


class TennisMotionPatternTests(unittest.TestCase):
    def test_unknown_technique_gets_only_generic_continuity(self):
        rows, observations, context = fixture()
        result = analyze_motion_pattern(rows, prep_end=25, peak=50, side="right", technique="baseline_forehand",
                                        source_observations=observations, source_context=context)
        self.assertEqual(result["profile_id"], "generic")
        self.assertEqual(set(result["metrics"]), {"pattern_phase_continuity"})
        self.assertIn("technique_classification", [r["id"] for r in result["requirements"]])

    def test_specific_profiles_have_explicit_source_requirements(self):
        self.assertEqual(len(TECHNIQUES), 11)
        for technique in TECHNIQUES:
            result = calculate(technique=technique)
            self.assertEqual(result["profile_id"], technique)
            self.assertFalse(result["coverage"]["full_source_technique_covered"])
            self.assertGreater(result["coverage"]["applicable_requirements"], result["coverage"]["measured_requirements"])
            if technique.startswith("return_"):
                missing = next(r for r in result["requirements"] if r["id"] == "return_compactness")
                self.assertEqual(missing["status"], "not_measured")

    def test_measured_metrics_have_dimension_and_numerical_contribution(self):
        definitions = {m[0] for metrics in PATTERN_METRICS.values() for m in metrics}
        for technique in TECHNIQUES:
            for key, (value, score) in calculate(technique=technique)["metrics"].items():
                self.assertIn(key, definitions)
                self.assertTrue(np.isfinite(value))
                self.assertTrue(0 <= score <= 100)

    def test_source_locators_exist_in_immutable_contract(self):
        contract = json.loads((Path(__file__).parents[1] / "contracts/scoring-reference-20261004.json").read_text(encoding="utf-8"))
        raw = json.dumps(contract, ensure_ascii=False)
        for _, _, references in SOURCE_REQUIREMENTS.values():
            for ref in references:
                document, paragraph = ref.split(":")
                self.assertIn(f"{document}:word/document.xml:{paragraph}", raw)

    def test_public_output_does_not_leak_document_numbers(self):
        raw = json.dumps(calculate(), ensure_ascii=False)
        self.assertNotIn("VIS-GS", raw)
        self.assertNotIn("P0097", raw)

    def test_camera_source_requires_both_binding_and_reference_verification(self):
        for key in ("camera_reference_verified", "source_binding_verified"):
            data = fixture()
            data[2][key] = False
            result = calculate(data)
            self.assertEqual(set(result["metrics"]), {"pattern_phase_continuity"})
            self.assertIn("fixed_camera_source_reference_unverified", [r["reason"] for r in result["requirements"]])

    def test_native_body_translation_cannot_substitute_camera_motion(self):
        rows, _, context = fixture()
        for row in rows:
            row["native_camera_translation"] = [row["time"], 0., 0.]
        result = calculate((rows, [], context))
        self.assertNotIn("pattern_center_transfer", result["metrics"])

    def test_wrong_subject_epoch_frame_and_time_are_rejected(self):
        for key, value in (("track", 2), ("selection_epoch", 1), ("camera_reference_epoch", 1), ("frame", 100), ("time", 3000.)):
            data = fixture()
            data[1][30][key] = value
            self.assertEqual(set(calculate(data)["metrics"]), {"pattern_phase_continuity"})

    def test_source_video_mismatch_is_rejected(self):
        data = fixture()
        data[1][30]["video_sha256"] = "b" * 64
        self.assertNotIn("pattern_projected_sequence", calculate(data)["metrics"])

    def test_temporal_gap_and_nonmonotonic_time_are_not_interpolated(self):
        for changed in (10000., 400.):
            data = fixture()
            data[0][30]["time"] = changed
            self.assertEqual(calculate(data)["metrics"], {})

    def test_forward_direction_must_be_independently_verified(self):
        data = fixture()
        del data[2]["forward_direction_2d_verified"]
        result = calculate(data)
        self.assertNotIn("pattern_center_transfer", result["metrics"])
        self.assertNotIn("pattern_forward_swing", result["metrics"])
        self.assertIn("pattern_projected_sequence", result["metrics"])

    def test_clipped_preparation_does_not_become_complete_progress_matching(self):
        self.assertIn("pattern_preparation_coupling", calculate()["metrics"])
        data = fixture()
        data[2]["preparation_start_clipped"] = True
        result = calculate(data)
        self.assertNotIn("pattern_preparation_coupling", result["metrics"])
        requirement = next(r for r in result["requirements"] if r["id"] == "preparation_coupling")
        self.assertEqual(requirement["reason"], "preparation_start_clipped_by_source_boundary")

    def test_independent_hip_shoulder_sequence_changes_score(self):
        good = calculate()
        data = fixture()
        for observation in data[1]:
            t, points = observation["time"], observation["points"]
            center = (points["left_hip"] + points["right_hip"]) / 2
            angle = np.deg2rad(30 * np.arctan((t - 920) / 65))
            vector = .3 * np.array([np.cos(angle), np.sin(angle)])
            points.update(left_hip=center - vector, right_hip=center + vector)
        reversed_order = calculate(data)
        self.assertEqual(good["metrics"]["pattern_projected_sequence"][1], 100.)
        self.assertLess(reversed_order["metrics"]["pattern_projected_sequence"][1], 80.)
        evidence = good["metric_evidence"]["pattern_projected_sequence"]
        self.assertLess(evidence["hip_peak_ms"], evidence["shoulder_peak_ms"])

    def test_static_axes_do_not_fabricate_peak_sequence(self):
        data = fixture()
        for observation in data[1]:
            points = observation["points"]
            center = (points["left_hip"] + points["right_hip"]) / 2
            points.update(left_hip=center - [.3, 0.], right_hip=center + [.3, 0.])
        self.assertNotIn("pattern_projected_sequence", calculate(data)["metrics"])

    def test_simultaneous_peaks_do_not_claim_a_resolved_order(self):
        data = fixture()
        for observation in data[1]:
            t, points = observation["time"], observation["points"]
            center = (points["left_hip"] + points["right_hip"]) / 2
            angle = np.deg2rad(30 * np.arctan((t - 760) / 65))
            vector = .3 * np.array([np.cos(angle), np.sin(angle)])
            points.update(left_hip=center - vector, right_hip=center + vector)
        self.assertNotIn("pattern_projected_sequence", calculate(data)["metrics"])

    def test_forward_and_backward_center_motion_have_distinct_scores(self):
        data = fixture()
        good = calculate(data)
        for observation in data[1]:
            delta = np.array([-.3 * observation["time"] / 1600, 0.])
            observation["points"] = {k: v + delta for k, v in observation["points"].items()}
        self.assertEqual(good["metrics"]["pattern_center_transfer"][1], 100.)
        self.assertEqual(calculate(data)["metrics"]["pattern_center_transfer"][1], 0.)

    def test_two_hand_coupling_uses_both_actual_trajectories(self):
        good = calculate(technique="baseline_two_hand_backhand")
        data = fixture()
        for observation in data[1]:
            points = observation["points"]
            center = (points["left_hip"] + points["right_hip"]) / 2
            points["left_wrist"] = center - (points["right_wrist"] - center)
        bad = calculate(data, technique="baseline_two_hand_backhand")
        self.assertAlmostEqual(good["metrics"]["pattern_two_hand_coupling"][1], 100.)
        self.assertLessEqual(bad["metrics"]["pattern_two_hand_coupling"][1], 50.000001)
        self.assertNotIn("pattern_two_hand_coupling", calculate(technique="baseline_one_hand_backhand")["metrics"])

    def test_missing_second_wrist_is_missing_not_zero(self):
        data = fixture()
        del data[1][30]["points"]["left_wrist"]
        self.assertNotIn("pattern_two_hand_coupling", calculate(data, technique="baseline_two_hand_backhand")["metrics"])

    def test_slice_requires_forward_and_downward_not_one_compensating_the_other(self):
        def sliced(dx, dy):
            data = fixture()
            for observation in data[1]:
                points = observation["points"]
                center = (points["left_hip"] + points["right_hip"]) / 2
                points["right_wrist"] = center + np.array([dx, dy]) * observation["time"] / 1000
            return data
        self.assertEqual(calculate(sliced(2., -1.), technique="backhand_slice")["metrics"]["pattern_slice_path"][1], 100.)
        self.assertEqual(calculate(sliced(2., 1.), technique="backhand_slice")["metrics"]["pattern_slice_path"][1], 0.)
        self.assertAlmostEqual(calculate(sliced(1., -2.), technique="backhand_slice")["metrics"]["pattern_slice_path"][1], 50.)
        self.assertEqual(calculate(sliced(1., -2.), technique="forehand_slice")["metrics"]["pattern_slice_path"][1], 100.)

    def test_serve_upward_path_requires_direction_and_uses_path_not_height(self):
        data = fixture()
        for observation in data[1]:
            points = observation["points"]
            center = (points["left_hip"] + points["right_hip"]) / 2
            points["right_wrist"] = center + [1., observation["time"] / 1000]
        self.assertEqual(calculate(data, technique="serve")["metrics"]["pattern_serve_upward_path"][1], 100.)
        for observation in data[1]:
            observation["points"]["right_wrist"][1] *= -1
        self.assertEqual(calculate(data, technique="serve")["metrics"]["pattern_serve_upward_path"][1], 0.)
        data[2]["up_direction_2d_verified"] = False
        self.assertNotIn("pattern_serve_upward_path", calculate(data, technique="serve")["metrics"])

    def test_serve_upper_arm_forearm_order_uses_independent_angles(self):
        def arm_data(upper_peak, elbow_peak):
            data = fixture()
            for observation in data[1]:
                t, points = observation["time"], observation["points"]
                upper = .8 * np.arctan((t - upper_peak) / 60)
                elbow = .8 * np.arctan((t - elbow_peak) / 60)
                points["right_elbow"] = points["right_shoulder"] + .6 * np.array([np.cos(upper), np.sin(upper)])
                points["right_wrist"] = points["right_elbow"] + .6 * np.array([np.cos(upper + elbow), np.sin(upper + elbow)])
            return data
        good = calculate(arm_data(1120, 1380), technique="serve")
        reversed_order = calculate(arm_data(1380, 1120), technique="serve")
        self.assertEqual(good["metrics"]["pattern_serve_arm_sequence"][1], 100.)
        self.assertLess(reversed_order["metrics"]["pattern_serve_arm_sequence"][1], 70.)

    def test_late_acceleration_is_not_penalized_as_bad_deceleration(self):
        for accelerate in (False, True):
            rows, obs, context = fixture()
            for row in rows:
                t = row["time"] / 1000
                row["local"]["right_wrist"] = np.array([t + (t * t if accelerate else 0), 1., 0.])
            result = calculate((rows, obs, context))
            self.assertEqual(result["metrics"]["pattern_phase_continuity"], (1., 100.))
            self.assertFalse(result["metric_evidence"]["pattern_phase_continuity"]["deceleration_required"])

    def test_observed_phase_pause_reduces_continuity(self):
        rows, obs, context = fixture()
        for row in rows:
            t = row["time"] / 1000
            x = t if t < .6 else .6 if t <= .8 else t - .2
            row["local"]["right_wrist"] = np.array([x, 1., 0.])
        self.assertLess(calculate((rows, obs, context))["metrics"]["pattern_phase_continuity"][1], 90.)

    def test_one_hand_recovery_requires_independent_complete_boundary(self):
        missing = calculate(technique="baseline_one_hand_backhand")
        self.assertNotIn("pattern_one_hand_reversal", missing["metrics"])
        data = fixture()
        data[2]["recovery_end_verified"] = True
        result = calculate(data, technique="baseline_one_hand_backhand")
        self.assertEqual(result["metrics"]["pattern_one_hand_reversal"][1], 0.)
        for observation in data[1]:
            t = observation["time"]
            if t >= 1000:
                points = observation["points"]
                center = (points["left_hip"] + points["right_hip"]) / 2
                points["right_wrist"] = center + [1 - abs(t - 1300) / 300, 1.2]
        self.assertEqual(calculate(data, technique="baseline_one_hand_backhand")["metrics"]["pattern_one_hand_reversal"][1], 100.)

    def test_pixel_translation_rotation_scale_and_mirror_do_not_change_results(self):
        original = calculate(technique="baseline_two_hand_backhand")
        for matrix in (np.array([[0., -1.], [1., 0.]]), np.array([[-1., 0.], [0., 1.]])):
            rows, obs, context = fixture()
            for observation in obs:
                observation["points"] = {k: 23 * (matrix @ v) + [40., -17.] for k, v in observation["points"].items()}
            context["scale"] *= 23
            for name in ("forward_direction_2d", "up_direction_2d"):
                context[name] = matrix @ context[name]
            transformed = calculate((rows, obs, context), technique="baseline_two_hand_backhand")
            self.assertEqual(set(original["metrics"]), set(transformed["metrics"]))
            for key in original["metrics"]:
                np.testing.assert_allclose(original["metrics"][key], transformed["metrics"][key], atol=1e-8)

    def test_no_mutation_and_no_player_labels_as_inputs(self):
        rows, observations, context = fixture()
        saved = deepcopy((rows, observations, context))
        a = calculate((rows, observations, context))
        context["player_identity"] = "professional"
        b = calculate((rows, observations, context))
        self.assertEqual(a["metrics"], b["metrics"])
        for old, current in zip(saved[1], observations):
            for name in old["points"]:
                np.testing.assert_array_equal(old["points"][name], current["points"][name])


if __name__ == "__main__":
    unittest.main()
