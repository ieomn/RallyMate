"""Reference geometry matching: source isolation, coverage, and known arithmetic."""
from copy import deepcopy
import json
import math
import unittest

import numpy as np

from rallymate_scoring.reference_comparison import compare_action_to_reference, policy_snapshot


METRICS = ("left_knee_flexion_deg", "right_knee_flexion_deg", "left_elbow_flexion_deg",
           "right_elbow_flexion_deg", "left_shoulder_elevation_deg", "right_shoulder_elevation_deg",
           "shoulder_hip_separation_deg", "stance_width_shoulders")


def fixture(sha="a" * 64, *, frame_offset=0, time_offset=0., episode_id="action"):
    times = [round(time_offset + index * 1001 / 30, 3) for index in range(21)]
    frames = [frame_offset + index for index in range(21)]
    def measurements(indices):
        output = {}
        for metric in METRICS:
            value = 1.5 if metric == "stance_width_shoulders" else 30.
            output[metric] = {"unit": "shoulder_width" if metric == "stance_width_shoulders" else "deg",
                             "status": "measured", "coverage": 1., "observation_count": len(indices),
                             "source_frames": [frames[i] for i in indices], "range": 0.,
                             **{field: value for field in ("min", "p10", "median", "p90", "max")}}
        return output
    phases = []
    for number, anchor in enumerate((0, 3, 5, 7, 9, 11, 13, 15, 17, 19), 1):
        start, end = max(0, anchor - 1), min(20, anchor + 1)
        phases.append({"phase_id": f"M{number:02d}", "status": "kinematic_candidate",
                       "start_ms": times[start], "anchor_ms": times[anchor], "end_ms": times[end],
                       "source_start_frame": frames[start], "source_frame": frames[anchor], "source_end_frame": frames[end],
                       "physical_event_verified": False,
                       "window_semantics": "overlapping_kinematic_observation_not_strict_state_transition",
                       "metrics": measurements(range(start, end + 1))})
    return {"version": "tennis-action-assessment-v1.4.0", "source_run_id": "run",
            "source_video_sha256": sha, "source_timestamp_semantics": "original_decoder_pts", "overall_score": 87.3,
            "subject_scope": {"selected_subject_id": "subject-1", "cross_subject_aggregation": False,
                              "subjects": [{"subject_id": "subject-1", "included_in_report": True,
                                            "source_track_ids": [7], "start_ms": times[0], "end_ms": times[-1]}]},
            "episodes": [{"episode_id": episode_id, "family": "baseline", "subject_id": "subject-1",
                          "person_track_id": 7, "source_track_id": 7, "source_selection_epoch": 3,
                          "camera_reference_epoch": 0, "active_arm": "right", "frame_count": len(frames),
                          "hand_roles": {"measurement_hand": "right"},
                          "start_ms": times[0], "peak_ms": times[15], "end_ms": times[-1],
                          "motion_pattern": {"profile_id": "baseline_forehand", "classification_status": "verified",
                                             "classification_provenance": {"method": "agent_visual_review", "reviewer": "source-reviewer"}},
                          "joint_kinematics": {"version": "body-joint-kinematics-v1", "coordinate_system": "body_relative_native_3d",
                                               "measurement_semantics": "monocular_model_estimate", "minimum_joint_coverage": .7,
                                               "metrics": measurements(range(21)), "phases": phases}}]}


def episode(report):
    return report["episodes"][0]


def phase(report, name="M07"):
    return next(row for row in episode(report)["joint_kinematics"]["phases"] if row["phase_id"] == name)


def compare(candidate, reference):
    return compare_action_to_reference(candidate, episode(candidate)["episode_id"], reference, episode(reference)["episode_id"])


def set_value(metric, value):
    metric.update({field: value for field in ("min", "p10", "median", "p90", "max")})


class ReferenceComparisonTests(unittest.TestCase):
    def setUp(self):
        self.reference = fixture()
        self.candidate = fixture("b" * 64)

    def test_identical_geometry_from_independent_videos_is_a_match_without_mutation(self):
        before = deepcopy((self.candidate, self.reference))
        result = compare(self.candidate, self.reference)
        self.assertEqual(result["reference_similarity_score"], 100)
        self.assertEqual(result["coverage"], 1)
        self.assertTrue(result["is_independent_comparison"])
        self.assertTrue(result["evaluation_eligible"])
        self.assertEqual(result["comparison_scope"], "independent_video_source")
        self.assertEqual(len(result["phases"]), 10)
        self.assertEqual(sum(len(row["metrics"]) for row in result["phases"]), 80)
        self.assertEqual(before, (self.candidate, self.reference))
        self.assertFalse(result["policy"]["coach_calibrated"])
        self.assertIn("not_skill_or_action_quality", result["score_semantics"])
        json.dumps(result, allow_nan=False)

    def test_native_numpy_float64_timeline_matches_json_roundtrip(self):
        for report in (self.candidate, self.reference):
            action = episode(report)
            for field in ("start_ms", "peak_ms", "end_ms"):
                action[field] = np.float64(action[field])
            for row in action["joint_kinematics"]["phases"]:
                for field in ("start_ms", "anchor_ms", "end_ms"):
                    row[field] = np.float64(row[field])
        set_value(phase(self.candidate)["metrics"]["shoulder_hip_separation_deg"], np.float64(80.))
        native = compare(self.candidate, self.reference)
        persisted = compare(json.loads(json.dumps(self.candidate, allow_nan=False)),
                            json.loads(json.dumps(self.reference, allow_nan=False)))
        self.assertEqual(native, persisted)
        self.assertEqual(native["reference_similarity_score"], 92.5)

    def test_native_numeric_support_preserves_json_boolean_and_finite_boundaries(self):
        for value in (True, np.float64(math.nan), np.float64(math.inf), 10 ** 400,
                      np.float32(500.), np.int64(500)):
            candidate = deepcopy(self.candidate)
            episode(candidate)["peak_ms"] = value
            with self.subTest(value_type=type(value).__name__), self.assertRaises(ValueError):
                compare(candidate, self.reference)

    def test_known_trunk_error_has_phase_and_group_weighted_deduction(self):
        set_value(phase(self.candidate)["metrics"]["shoulder_hip_separation_deg"], 80.)
        # 50 deg difference minus 5 deg tolerance spans the 45 deg distance
        # scale: M07 .25 * trunk .30 = 7.5 points out of the full score.
        result = compare(self.candidate, self.reference)
        self.assertEqual(result["reference_similarity_score"], 92.5)
        row = next(row for row in result["phases"] if row["phase_id"] == "M07")
        self.assertEqual(row["score"], 70)
        metric = next(item for item in row["metrics"] if item["metric_id"] == "shoulder_hip_separation_deg")
        self.assertEqual(metric["delta"], 50)
        self.assertEqual(metric["normalized_distance"], 1)
        self.assertEqual(metric["weighted_deduction"], 7.5)

    def test_each_group_has_declared_mass_and_same_group_metrics_have_equal_weights(self):
        policy = policy_snapshot()
        self.assertAlmostEqual(sum(policy["phase_weights"].values()), 1)
        self.assertAlmostEqual(sum(policy["phase_weights"][p] for p in ("M04", "M05", "M06")), .4)
        for group, metrics in policy["metric_groups"].items():
            self.assertAlmostEqual(sum(policy["metric_weights"][m] for m in metrics), policy["group_weights"][group])
            self.assertEqual(len({policy["metric_weights"][m] for m in metrics}), 1)

    def test_tolerance_scale_and_clamp_are_explicit_for_angles_and_stance(self):
        for key, initial, tolerance, scale, deduction in (
            ("right_elbow_flexion_deg", 30., 5., 45., 2.5),
            ("stance_width_shoulders", 1.5, .1, 1., 2.5),
        ):
            for factor, expected in ((0, 100), (.5, 100 - deduction / 2), (1, 100 - deduction), (2, 100 - deduction)):
                with self.subTest(metric=key, factor=factor):
                    candidate = deepcopy(self.candidate)
                    set_value(phase(candidate)["metrics"][key], initial + tolerance + scale * factor)
                    self.assertAlmostEqual(compare(candidate, self.reference)["reference_similarity_score"], expected, places=6)

    def test_difference_is_symmetric_and_never_uses_original_action_scores(self):
        set_value(phase(self.candidate)["metrics"]["right_elbow_flexion_deg"], 55.)
        first = compare(self.candidate, self.reference)
        self.candidate["overall_score"] = 0
        self.reference["overall_score"] = 100
        second = compare(self.reference, self.candidate)
        self.assertEqual(first["reference_similarity_score"], second["reference_similarity_score"])

    def test_missing_measurements_keep_coverage_and_never_become_zero_distance(self):
        for row in episode(self.candidate)["joint_kinematics"]["phases"]:
            for key in ("left_elbow_flexion_deg", "right_elbow_flexion_deg"):
                row["metrics"].pop(key)
        result = compare(self.candidate, self.reference)
        self.assertEqual(result["coverage"], .8)
        self.assertEqual(result["reference_similarity_score"], 100)
        missing = next(item for item in result["phases"][0]["metrics"] if item["metric_id"] == "left_elbow_flexion_deg")
        self.assertEqual(missing["status"], "not_measured")
        self.assertIsNone(missing["delta"])
        self.assertIsNone(missing["weighted_deduction"])
        set_value(phase(self.candidate)["metrics"]["shoulder_hip_separation_deg"], 80.)
        partial = compare(self.candidate, self.reference)
        deductions = [metric["weighted_deduction"] for p in partial["phases"] for metric in p["metrics"]
                      if metric["weighted_deduction"] is not None]
        self.assertAlmostEqual(partial["reference_similarity_score"], 100 - math.fsum(deductions))
        self.assertEqual(partial["reference_similarity_score"], 90.625)
        phase(self.candidate)["metrics"].pop("left_shoulder_elevation_deg")
        self.assertEqual(compare(self.candidate, self.reference)["coverage"], .775)
        self.assertIsNone(compare(self.candidate, self.reference)["reference_similarity_score"])

    def test_missing_core_phase_blocks_even_when_total_coverage_exceeds_eighty_percent(self):
        kin = episode(self.candidate)["joint_kinematics"]
        kin["phases"] = [row for row in kin["phases"] if row["phase_id"] != "M04"]
        result = compare(self.candidate, self.reference)
        self.assertGreater(result["coverage"], .8)
        self.assertIsNone(result["reference_similarity_score"])
        self.assertEqual(result["missing_required_phases"], ["M04"])
        self.assertIn("required_comparison_phase_unavailable", result["reason_codes"])

    def test_unobserved_optional_phase_does_not_block_observed_geometry(self):
        kin = episode(self.candidate)["joint_kinematics"]
        kin["phases"] = [row for row in kin["phases"] if row["phase_id"] != "M03"]
        result = compare(self.candidate, self.reference)
        self.assertEqual(result["coverage"], .97)
        self.assertEqual(result["reference_similarity_score"], 100)
        self.assertIsNone(result["phases"][2]["score"])

    def test_self_and_overlapping_source_are_diagnostics_not_independent_validation(self):
        for candidate in (deepcopy(self.reference), fixture("a" * 64, frame_offset=10, time_offset=1001 / 3, episode_id="other")):
            result = compare(candidate, self.reference)
            self.assertEqual(result["comparison_scope"], "self_reference")
            self.assertEqual(result["reference_similarity_score"], 100)
            self.assertFalse(result["evaluation_eligible"])
            self.assertFalse(result["is_independent_comparison"])
            self.assertIn("self_reference", result["reason_codes"])

    def test_distinct_nonoverlapping_window_in_same_video_is_same_source_holdout(self):
        candidate = fixture("a" * 64, frame_offset=100, time_offset=5000., episode_id="later")
        result = compare(candidate, self.reference)
        self.assertEqual(result["comparison_scope"], "same_source_holdout")
        self.assertFalse(result["evaluation_eligible"])
        self.assertFalse(result["is_independent_comparison"])

    def test_generic_unverified_profiles_and_left_right_mismatch_are_unavailable(self):
        changes = [({"profile_id": "generic", "classification_status": "technique_unverified"}, None),
                   ({"classification_status": "technique_unverified"}, None),
                   ({"profile_id": "baseline_two_hand_backhand"}, None), ({}, "left")]
        for change, arm in changes:
            candidate = deepcopy(self.candidate)
            episode(candidate)["motion_pattern"].update(change)
            if arm:
                episode(candidate)["active_arm"] = arm
                episode(candidate)["hand_roles"]["measurement_hand"] = arm
            with self.subTest(change=change, arm=arm):
                result = compare(candidate, self.reference)
                self.assertEqual(result["status"], "unavailable")
                self.assertIsNone(result["reference_similarity_score"])
                self.assertFalse(result["evaluation_eligible"])

    def test_provenance_and_profile_family_are_required(self):
        for key, value in (("method", "unverified"), ("reviewer", ""), ("method", "source_bound_serve_phases")):
            candidate = deepcopy(self.candidate)
            episode(candidate)["motion_pattern"]["classification_provenance"][key] = value
            with self.subTest(key=key, value=value), self.assertRaises(ValueError):
                compare(candidate, self.reference)
        episode(self.candidate)["family"] = "serve"
        with self.assertRaisesRegex(ValueError, "classification_family_mismatch"):
            compare(self.candidate, self.reference)

    def test_source_identity_subject_track_epoch_and_pts_semantics_fail_closed(self):
        mutations = [lambda r: r.update(source_video_sha256="missing"),
                     lambda r: r.update(source_timestamp_semantics="rounded_playback_clock"),
                     lambda r: episode(r).update(subject_id="other"),
                     lambda r: episode(r).update(source_track_id=8),
                     lambda r: episode(r).update(source_selection_epoch=True),
                     lambda r: episode(r).update(camera_reference_epoch="0"),
                     lambda r: r["subject_scope"].update(cross_subject_aggregation=True),
                     lambda r: r["subject_scope"]["subjects"][0].update(source_track_ids=[8])]
        for mutate in mutations:
            candidate = deepcopy(self.candidate)
            mutate(candidate)
            with self.subTest(mutate=mutate), self.assertRaises(ValueError):
                compare(candidate, self.reference)

    def test_wrong_units_nonfinite_numbers_and_booleans_cannot_enter_distance(self):
        for key, value in (("unit", "rad"), ("median", True), ("median", math.nan),
                           ("median", math.inf), ("coverage", True), ("observation_count", True)):
            candidate = deepcopy(self.candidate)
            phase(candidate)["metrics"]["right_elbow_flexion_deg"][key] = value
            with self.subTest(key=key, value=value), self.assertRaises(ValueError):
                compare(candidate, self.reference)

    def test_duplicate_phase_episode_and_nonincreasing_frame_arrays_are_rejected(self):
        for kind in ("phase", "episode", "frames"):
            candidate = deepcopy(self.candidate)
            if kind == "phase":
                episode(candidate)["joint_kinematics"]["phases"].append(deepcopy(phase(candidate)))
            elif kind == "episode":
                candidate["episodes"].append(deepcopy(episode(candidate)))
            else:
                phase(candidate)["metrics"]["right_elbow_flexion_deg"]["source_frames"].reverse()
            with self.subTest(kind=kind), self.assertRaises(ValueError):
                compare(candidate, self.reference)

    def test_phase_metric_frames_cannot_borrow_from_another_phase_or_unobserved_frame(self):
        metric = phase(self.candidate)["metrics"]["right_elbow_flexion_deg"]
        metric["source_frames"] = [1, 2, 3]
        with self.assertRaisesRegex(ValueError, "metric_frames_outside_phase"):
            compare(self.candidate, self.reference)
        self.candidate = fixture("b" * 64)
        parent = episode(self.candidate)["joint_kinematics"]["metrics"]["right_elbow_flexion_deg"]
        parent["source_frames"].remove(13)
        parent["observation_count"] -= 1
        parent["coverage"] = round(parent["observation_count"] / episode(self.candidate)["frame_count"], 6)
        with self.assertRaisesRegex(ValueError, "metric_frames_outside_episode_measurement"):
            compare(self.candidate, self.reference)

    def test_one_frame_cannot_have_two_clocks_and_different_frames_cannot_share_a_clock(self):
        phase(self.candidate)["end_ms"] += .001
        # The same frame is also an adjacent window endpoint.
        with self.assertRaisesRegex(ValueError, "source_frame_clock_conflict"):
            compare(self.candidate, self.reference)
        self.candidate = fixture("b" * 64)
        only = deepcopy(phase(self.candidate, "M08"))
        only["start_ms"] = only["anchor_ms"]
        episode(self.candidate)["joint_kinematics"]["phases"] = [only]
        with self.assertRaisesRegex(ValueError, "source_clock_non_increasing"):
            compare(self.candidate, self.reference)

    def test_phase_names_are_not_forced_into_a_physical_event_time_order(self):
        first, second = phase(self.candidate, "M04"), phase(self.candidate, "M05")
        first["phase_id"], second["phase_id"] = second["phase_id"], first["phase_id"]
        result = compare(self.candidate, self.reference)
        self.assertEqual(result["reference_similarity_score"], 100)
        self.assertEqual(result["coverage"], 1)

    def test_phase_anchor_cannot_use_unobserved_frame_inside_episode_bounds(self):
        kin = episode(self.candidate)["joint_kinematics"]
        # A sparse source decoder timeline legitimately uses every second frame.
        for metric in kin["metrics"].values():
            metric["source_frames"] = [frame * 2 for frame in metric["source_frames"]]
        for row in kin["phases"]:
            for field in ("source_start_frame", "source_frame", "source_end_frame"):
                row[field] *= 2
            for metric in row["metrics"].values():
                metric["source_frames"] = [frame * 2 for frame in metric["source_frames"]]
        self.assertEqual(compare(self.candidate, self.reference)["reference_similarity_score"], 100)
        phase(self.candidate)["source_frame"] += 1
        with self.assertRaisesRegex(ValueError, "phase_source_binding_invalid"):
            compare(self.candidate, self.reference)

    def test_coordinate_and_physical_event_claims_must_match_declared_measurement(self):
        kin = episode(self.candidate)["joint_kinematics"]
        for key, value in (("coordinate_system", "world_metres"), ("measurement_semantics", "ground_truth"), ("version", "other")):
            candidate = deepcopy(self.candidate)
            episode(candidate)["joint_kinematics"][key] = value
            with self.subTest(key=key), self.assertRaises(ValueError):
                compare(candidate, self.reference)
        phase(self.candidate)["physical_event_verified"] = True
        with self.assertRaisesRegex(ValueError, "phase_semantics_invalid"):
            compare(self.candidate, self.reference)

    def test_report_and_policy_snapshots_are_deterministic_detached_and_auditable(self):
        result = compare(self.candidate, self.reference)
        reordered = dict(reversed(list(self.candidate.items())))
        self.assertEqual(result, compare(reordered, self.reference))
        self.assertEqual(len(result["candidate"]["report_sha256"]), 64)
        self.assertEqual(len(result["policy_sha256"]), 64)
        self.assertEqual(len(result["candidate"]["phases"]), 10)
        self.assertEqual(result["candidate"]["classification_provenance"], episode(self.candidate)["motion_pattern"]["classification_provenance"])
        result["policy"]["phase_weights"]["M07"] = 0
        result["candidate"]["classification_provenance"]["reviewer"] = "other"
        self.assertEqual(compare(self.candidate, self.reference)["policy"]["phase_weights"]["M07"], .25)
        self.assertEqual(episode(self.candidate)["motion_pattern"]["classification_provenance"]["reviewer"], "source-reviewer")

    def test_rule_inferred_classification_can_compare_but_cannot_pass_independent_evaluation(self):
        episode(self.candidate)["motion_pattern"]["classification_provenance"] = {"method": "source_bound_rule_inferred"}
        result = compare(self.candidate, self.reference)
        self.assertEqual(result["status"], "scored")
        self.assertEqual(result["reference_similarity_score"], 100)
        self.assertTrue(result["is_independent_comparison"])
        self.assertFalse(result["evaluation_eligible"])
        self.assertIn("classification_not_independently_reviewed", result["reason_codes"])

    def test_finite_operands_cannot_produce_nonfinite_output_distance(self):
        set_value(phase(self.candidate)["metrics"]["right_elbow_flexion_deg"], 1e308)
        set_value(phase(self.reference)["metrics"]["right_elbow_flexion_deg"], -1e308)
        with self.assertRaisesRegex(ValueError, "metric_distance_nonfinite"):
            compare(self.candidate, self.reference)

    def test_median_quantiles_and_range_must_describe_consistent_observations(self):
        for change in ({"median": 9999.}, {"p10": 31.}, {"p90": 29.}, {"range": 10.}):
            candidate = deepcopy(self.candidate)
            phase(candidate)["metrics"]["shoulder_hip_separation_deg"].update(change)
            with self.subTest(change=change), self.assertRaisesRegex(ValueError, "metric_statistics_inconsistent"):
                compare(candidate, self.reference)
        # Five-decimal producer rounding can shift max-min from range slightly.
        metric = phase(self.candidate)["metrics"]["right_elbow_flexion_deg"]
        metric.update(min=29.99999, max=30.00001, range=.00001)
        self.assertEqual(compare(self.candidate, self.reference)["reference_similarity_score"], 100)

    def test_shrinking_metric_frames_cannot_preserve_full_coverage(self):
        for whole_window in (False, True):
            candidate = deepcopy(self.candidate)
            metric = (episode(candidate)["joint_kinematics"] if whole_window else phase(candidate))["metrics"]["right_elbow_flexion_deg"]
            metric["source_frames"] = metric["source_frames"][:1]
            metric["observation_count"] = 1
            with self.subTest(whole_window=whole_window), self.assertRaisesRegex(ValueError, "metric_coverage_count_mismatch"):
                compare(candidate, self.reference)


if __name__ == "__main__":
    unittest.main()
