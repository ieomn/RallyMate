"""Synthetic counterfactuals, never player identities or desired rankings."""
from copy import deepcopy
import json
from pathlib import Path
import unittest

import numpy as np

from rallymate_service.tennis_motion_patterns import (
    analyze_motion_pattern, PATTERN_METRICS, SOURCE_REQUIREMENTS, TECHNIQUES, _observed_peak, _sequence,
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
    def test_one_decoder_interval_remains_unresolved_after_float_division(self):
        for fps in (30, 60):
            times = np.arange(1., 11.) * 1000 / fps
            peaks = [times[2], times[3], times[6]]
            self.assertGreater(peaks[1] - peaks[0], np.median(np.diff(times)))
            with self.subTest(fps=fps):
                self.assertIsNone(_sequence(peaks, times))
                self.assertIsNone(_sequence(peaks[::-1], times))

    def test_vfr_one_median_interval_is_not_a_resolved_order(self):
        times = np.array([0., 20., 40., 70., 100., 120., 150.])
        # The numerical tolerance does not turn one-frame uncertainty into
        # evidence merely because subtraction adds a tiny rounding residual.
        self.assertIsNone(_sequence([20., 45. + 1e-12, 100.], times))

    def test_resolved_multi_frame_order_and_reverse_score_are_unchanged(self):
        times = np.arange(1., 11.) * 1000 / 30
        self.assertEqual(_sequence([times[1], times[3], times[6]], times), (0., 100.))
        reverse = _sequence([times[6], times[3], times[1]], times)
        tolerance = float(np.median(np.diff(times)))
        expected = ((times[6] - times[3] - tolerance) + (times[3] - times[1] - tolerance)) / (times[-1] - times[0])
        self.assertEqual(reverse, (expected, 100 * (1 - expected)))

    def test_unknown_technique_gets_only_generic_continuity(self):
        rows, observations, context = fixture()
        result = analyze_motion_pattern(rows, prep_end=25, peak=50, side="right", technique="baseline_forehand",
                                        source_observations=observations, source_context=context)
        self.assertEqual(result["profile_id"], "generic")
        self.assertEqual(set(result["metrics"]), {"pattern_phase_continuity"})
        self.assertIn("technique_classification", [r["id"] for r in result["requirements"]])

    def test_reviewed_unknown_type_remains_generic_instead_of_inheriting_a_dataset_label(self):
        result = calculate(technique='unknown')
        self.assertEqual(result['profile_id'], 'generic')
        self.assertEqual(result['classification_status'], 'technique_unverified')
        self.assertEqual(set(result['metrics']), {'pattern_phase_continuity'})

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

    def test_foreshortening_outside_measured_phase_and_fixed_support_does_not_erase_sequence(self):
        expected = calculate()
        for index in (4, 70):
            data = fixture()
            points = data[1][index]['points']
            for joint in ('hip', 'shoulder'):
                center = (points['left_' + joint] + points['right_' + joint]) / 2
                points['left_' + joint], points['right_' + joint] = center - [.01, 0], center + [.01, 0]
            result = calculate(data)
            self.assertEqual(result['metrics']['pattern_projected_sequence'], expected['metrics']['pattern_projected_sequence'])
            if index == 70:
                self.assertEqual(result['metrics']['pattern_preparation_coupling'], expected['metrics']['pattern_preparation_coupling'])
            else:
                self.assertNotIn('pattern_preparation_coupling', result['metrics'])

    def test_foreshortening_in_derivative_support_remains_missing(self):
        data = fixture()
        points = data[1][52]['points']
        center = (points['left_hip'] + points['right_hip']) / 2
        points['left_hip'], points['right_hip'] = center - [.01, 0], center + [.01, 0]
        result = calculate(data)
        self.assertNotIn('pattern_projected_sequence', result['metrics'])
        self.assertEqual(result['metric_evidence']['pattern_projected_sequence']['unobservable_axes'], ['hip'])

    def test_phase_boundary_peak_uses_fixed_neighbor_support_without_searching_recovery(self):
        times = np.arange(9.) * 20
        speed = np.array([1., 2., 4., 7., 10., 8., 4., 11., 3.])
        value, detail = _observed_peak(speed, times, (1, 5))
        self.assertEqual(value, 80.)
        self.assertEqual(detail['search_range_ms'], [20., 80.])
        # Later recovery motion is not selected as this phase's peak.
        self.assertNotEqual(value, times[7])
        for truncated in (speed[:5], np.arange(9.)):
            value, detail = _observed_peak(truncated, times[:len(truncated)], (1, 5))
            self.assertIsNone(value)
            self.assertIn(detail['reason'], ('peak_two_sided_support_missing', 'peak_continues_outside_phase_search'))

    def test_common_camera_plane_rotation_cannot_fabricate_independent_order(self):
        data = fixture()
        for observation in data[1]:
            t, points = observation['time'], observation['points']
            center = (points['left_hip'] + points['right_hip']) / 2
            angle = .7 * np.arctan((t - 760) / 65)
            for joint, offset, width in (('hip', np.array([0., 0.]), .3), ('shoulder', np.array([0., 1.]), .45)):
                axis = width * np.array([np.cos(angle), np.sin(angle)])
                points['left_' + joint], points['right_' + joint] = center + offset - axis, center + offset + axis
        result = calculate(data)
        self.assertNotIn('pattern_projected_sequence', result['metrics'])
        self.assertEqual(next(r['reason'] for r in result['requirements'] if r['id'] == 'projected_sequence'), 'motion_peak_order_unresolved')

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

    def slice_direction_fixture(self, forward):
        data=fixture(); data[2]['forward_direction_2d']=forward
        for observation in data[1]:
            points=observation['points']; center=(points['left_hip']+points['right_hip'])/2
            points['right_wrist']=center+np.array([0.,-1.])*observation['time']/1000
        return data

    def test_collinear_slice_directions_are_missing_not_two_copies_of_one_axis(self):
        for technique in ('forehand_slice','backhand_slice','return_forehand_slice','return_backhand_slice'):
            for forward in ([0.,-1.],[0.,1.],[.02,-1.],[.02,1.]):
                result=calculate(self.slice_direction_fixture(forward), technique=technique)
                self.assertNotIn('pattern_slice_path',result['metrics'])
                requirement=next(r for r in result['requirements'] if r['id']=='slice_path')
                self.assertEqual(requirement['status'],'not_measured')
                self.assertEqual(requirement['reason'],'slice_joint_directions_ill_conditioned')
                detail=result['metric_evidence']['pattern_slice_path']
                self.assertEqual(detail['downward_fraction'],1.)
                self.assertIn(detail['forward_fraction'],(0.,1.))
                self.assertEqual(detail['maximum_direction_basis_condition_number'],4.)
                json.dumps(result,allow_nan=False)

    def test_fixed_condition_four_boundary_changes_observability_not_the_grade_curve(self):
        for condition in (1.,2.,4.,4.001,8.):
            cosine=(condition*condition-1)/(condition*condition+1)
            direction=[np.sqrt(1-cosine*cosine),-cosine]
            result=calculate(self.slice_direction_fixture(direction), technique='forehand_slice')
            detail=result['metric_evidence']['pattern_slice_path']
            self.assertAlmostEqual(detail['direction_basis_condition_number'],condition,places=10)
            if condition<=4 and condition>1:
                self.assertEqual(result['metrics']['pattern_slice_path'],(1.,100.))
            elif condition>4:
                self.assertNotIn('pattern_slice_path',result['metrics'])
        # Existing well-separated forward/down formula is unchanged.
        result=calculate(self.slice_direction_fixture([1.,-1.]), technique='forehand_slice')
        self.assertEqual(result['metrics']['pattern_slice_path'],(1.,100.))

    def test_slice_direction_observability_is_rotation_and_mirror_invariant(self):
        for forward in ([1.,-1.],[.02,-1.],[0.,-1.]):
            original=calculate(self.slice_direction_fixture(forward),technique='forehand_slice')
            for matrix in (np.array([[0.,-1.],[1.,0.]]),np.array([[-1.,0.],[0.,1.]])):
                data=self.slice_direction_fixture(forward)
                for observation in data[1]:
                    observation['points']={k:23*(matrix@v)+[40.,-17.] for k,v in observation['points'].items()}
                data[2]['scale']*=23
                for field in ('forward_direction_2d','up_direction_2d'):
                    data[2][field]=matrix@data[2][field]
                result=calculate(data,technique='forehand_slice')
                self.assertEqual(original['metrics'].get('pattern_slice_path'),result['metrics'].get('pattern_slice_path'))
                self.assertEqual(next(r for r in original['requirements'] if r['id']=='slice_path')['status'],
                                 next(r for r in result['requirements'] if r['id']=='slice_path')['status'])

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
