from copy import deepcopy
from types import SimpleNamespace
import unittest

from rallymate_scoring.card_evaluation import evaluate_card_window, summarize_card_scores, GRADE_POINTS
from rallymate_scoring.card_kinematics import measure_card_window


def fixture(count=10):
    points = {"left_shoulder": (-.3, 0), "right_shoulder": (.3, 0),
              "left_hip": (-.25, 1), "right_hip": (.25, 1),
              "left_knee": (-.4, 1.6), "right_knee": (.4, 1.6),
              "left_ankle": (-.5, 2), "right_ankle": (.5, 2),
              "left_elbow": (-.6, .3), "right_elbow": (.6, .3),
              "left_wrist": (-.4, .5), "right_wrist": (.4, .5), "nose": (0, -.3)}
    samples = [SimpleNamespace(time=i*40, frame_index=i, track=1, selection_epoch=0,
                              camera_reference_epoch=0, camera_compensated=True,
                              points=deepcopy(points), point_confidences={k: .9 for k in points},
                              source_binding={"video_sha256": "a"*64},
                              timestamp_source="decoder_pts", normalization_basis="full_torso") for i in range(count)]
    window = {"video_id": "test", "video_sha256": "a"*64, "event_id": "swing-1", "event_code": "GS01", "stage_code": "M03",
              "start_ms": 0, "end_ms": (count-1)*40, "person_track_id": 1, "selection_epoch": 0,
              "camera_reference_epoch": 0, "camera_compensated": True,
              "stage_binding": {"method": "manual_anchor", "version": "test-fixture-only"}}
    timeline = [{"frame_index": s.frame_index, "timestamp_ms": s.time, "pts": s.time,
                 "time_base_num": 1, "time_base_den": 1000} for s in samples]
    rule = {"indicator_id": "GS01-M03-01", "rule_version": "test-fixture-only", "evaluation": {
        "kind": "visual_proxy", "grade_policy": "condition_completion_v1", "grade_mapping": dict(GRADE_POINTS),
        "conditions": [{"key": "support", "metric": "hip_inside_ankle_support_fraction.mean", "operator": "gte",
                        "value": .8, "unit": "fraction", "required": True},
                       {"key": "trunk", "metric": "trunk_tilt_deg.range", "operator": "lte",
                        "value": 15, "unit": "deg", "required": True}]}}
    return rule, samples, window, timeline


class CardEvaluationTests(unittest.TestCase):
    def test_real_formula_known_geometry_and_no_original_grade(self):
        rule, samples, window, timeline = fixture()
        result = evaluate_card_window(rule, samples, window, timeline)
        self.assertEqual(result["proxy_grade"], "A")
        self.assertEqual([c["value"] for c in result["conditions"]], [1, 0])
        self.assertIsNone(result["original_grade"])
        self.assertFalse(result["quality"]["visibility_verified"])

    def test_missing_decoder_samples_stay_in_denominator(self):
        rule, samples, window, timeline = fixture()
        result = evaluate_card_window(rule, samples[:6], window, timeline)
        self.assertEqual(result["quality"]["total_frame_count"], 10)
        self.assertEqual(result["quality"]["valid_frame_count"], 6)
        self.assertEqual(result["status"], "unavailable")
        self.assertIsNone(result["score"])
        boundary = evaluate_card_window(rule, samples[:7], window, timeline)
        self.assertEqual(boundary["status"], "scored")

    def test_joint_union_cannot_be_replaced_with_individual_coverage(self):
        rule, samples, window, timeline = fixture()
        for sample in samples[:3]:
            del sample.points["left_ankle"]
        for sample in samples[-3:]:
            del sample.points["left_shoulder"]
        result = evaluate_card_window(rule, samples, window, timeline)
        self.assertTrue(all(c["value"] is None for c in result["conditions"]))
        self.assertEqual(result["quality"]["valid_frame_count"], 4)
        self.assertEqual(result["status"], "unavailable")

    def test_partial_window_preserves_static_metrics_but_never_fabricates_delta(self):
        _, samples, window, timeline = fixture()
        del samples[5].points["right_wrist"]
        result = measure_card_window(samples, window, timeline)["measurements"]["wrist_separation_torso"]
        self.assertEqual(result["statistics"]["median"], .8)
        self.assertNotIn("delta", result["statistics"])

    def test_frame_time_identity_camera_and_decode_source_mismatches_reject(self):
        for field, value in (("track", 2), ("selection_epoch", 1), ("camera_reference_epoch", 1),
                             ("camera_compensated", False), ("time", 41), ("frame_index", 0),
                             ("timestamp_source", "fps_fallback")):
            rule, samples, window, timeline = fixture()
            setattr(samples[1], field, value)
            result = evaluate_card_window(rule, samples, window, timeline)
            self.assertEqual(result["status"], "unavailable", field)

    def test_three_phase_or_wrong_technique_does_not_supply_source_stage(self):
        for change in ({"stage_binding": {"method": "three_phase_swing", "version": "1"}},
                       {"stage_code": "M08"}, {"event_code": "GS02"}):
            rule, samples, window, timeline = fixture()
            result = evaluate_card_window(rule, samples, {**window, **change}, timeline)
            self.assertEqual(result["reason_codes"], ["source_stage_binding_missing_or_mismatched"])

    def test_unknown_extractor_invalid_math_and_wrong_units_never_score(self):
        for change in ({"metric": "unregistered.mean"}, {"unit": "px"}, {"value": True},
                       {"value": float("nan")}, {"operator": "between", "bounds": [2, 1]}):
            rule, samples, window, timeline = fixture()
            rule["evaluation"]["conditions"][0].update(change)
            self.assertEqual(evaluate_card_window(rule, samples, window, timeline)["status"], "unavailable")

    def test_sparse_timeline_or_different_video_cannot_shrink_denominator(self):
        rule, samples, window, timeline = fixture()
        for sample in samples:
            sample.frame_index *= 2
        for row in timeline:
            row["frame_index"] *= 2
        self.assertEqual(evaluate_card_window(rule, samples, window, timeline)["status"], "unavailable")
        rule, samples, window, timeline = fixture()
        samples[5].source_binding["video_sha256"] = "b"*64
        self.assertIn("video_identity_mismatch", evaluate_card_window(rule, samples, window, timeline)["reason_codes"])

    def test_pts_not_rounded_playback_clock_controls_duration_and_velocity(self):
        rule, samples, window, timeline = fixture(3)
        for i, row in enumerate(timeline):
            row.update(pts=i*398, time_base_den=10000)
        self.assertIn("window_too_short", evaluate_card_window(rule, samples, window, timeline)["reason_codes"])
        rule, samples, window, timeline = fixture()
        timeline[5]["pts"] += 2
        self.assertIn("decoder_video_clock_origin_changed", evaluate_card_window(rule, samples, window, timeline)["reason_codes"])
        rule, samples, window, timeline = fixture()
        del timeline[0]["pts"]
        self.assertIn("decoder_pts_missing_or_nonmonotonic", evaluate_card_window(rule, samples, window, timeline)["reason_codes"])

    def test_compound_static_conditions_use_identical_common_frames(self):
        rule, samples, window, timeline = fixture()
        for sample in samples[:3]:
            del sample.points["left_shoulder"]
            sample.points["left_ankle"] = (1.5, 2)
            sample.points["right_ankle"] = (2.5, 2)
        result = evaluate_card_window(rule, samples, window, timeline)
        self.assertEqual(result["proxy_grade"], "A")
        frames = [c["evidence"]["valid_source_frames"] for c in result["conditions"]]
        self.assertEqual(frames, [list(range(3, 10)), list(range(3, 10))])

    def test_fixed_reference_hip_descent_is_independent_of_moving_ankles(self):
        _, samples, window, timeline = fixture()
        for i, sample in enumerate(samples):
            sample.reference_points = {name: (x*100, y*100) for name, (x, y) in sample.points.items()}
            for side in ("left", "right"):
                x, y = sample.reference_points[f"{side}_ankle"]
                sample.reference_points[f"{side}_ankle"] = (x, y-i*2)
        measurement = measure_card_window(samples, window, timeline)["measurements"]
        self.assertEqual(measurement["hip_downward_displacement_torso"]["statistics"]["value"], 0)
        self.assertEqual(measurement["hip_displacement_torso"]["statistics"]["value"], 0)
        self.assertEqual(measurement["ankle_order_exchange_count"]["reason_codes"], ["complete_step_cycle_not_bound"])
        window["camera_compensated"] = False
        for sample in samples:
            sample.camera_compensated = False
        measurement = measure_card_window(samples, window, timeline)["measurements"]
        self.assertEqual(measurement["hip_downward_displacement_torso"]["reason_codes"], ["fixed_camera_reference_required"])

    def test_continuous_ankle_order_exchange_and_zero_path_are_distinct(self):
        _, samples, window, timeline = fixture()
        window["step_cycle_binding"] = {"method": "manual_anchor", "version": "fixture-only",
                                         "depart_ms": 0, "widest_ms": 160, "return_ms": 360}
        for i, sample in enumerate(samples):
            sample.reference_points = {name: (x*100, y*100) for name, (x, y) in sample.points.items()}
            sample.reference_points["left_ankle"] = (-50+i*12, 200)
            sample.reference_points["right_ankle"] = (50-i*12, 200)
        value = measure_card_window(samples, window, timeline)["measurements"]["ankle_order_exchange_count"]
        self.assertEqual(value["statistics"]["value"], 1)
        for sample in samples:
            sample.reference_points["left_ankle"] = (-50, 200)
            sample.reference_points["right_ankle"] = (50, 200)
        value = measure_card_window(samples, window, timeline)["measurements"]["ankle_stability_ratio"]
        self.assertEqual(value["reason_codes"], ["ankle_path_too_small"])
        window["step_cycle_binding"]["depart_ms"] = 40
        value = measure_card_window(samples, window, timeline)["measurements"]["ankle_order_exchange_count"]
        self.assertEqual(value["reason_codes"], ["complete_step_cycle_not_bound"])

    def test_no_confidence_is_not_verified_geometry_without_bound_model_source(self):
        rule, samples, window, timeline = fixture()
        for sample in samples:
            sample.point_confidences = {}
        self.assertEqual(evaluate_card_window(rule, samples, window, timeline)["status"], "unavailable")
        for sample in samples:
            sample.observation_semantics = "monocular_model_estimate"
        result = evaluate_card_window(rule, samples, window, timeline)
        self.assertIn("model_source_binding_unverified", result["reason_codes"])

    def test_unobservable_physics_is_unavailable_without_zero(self):
        rule, samples, window, timeline = fixture()
        rule["evaluation"]["kind"] = "manual_review"
        result = evaluate_card_window(rule, samples, window, timeline)
        self.assertEqual(result["status"], "unavailable")
        self.assertIsNone(result["score"])

    def test_grade_boundary_math_and_missing_are_distinct(self):
        rule, samples, window, timeline = fixture()
        rule["evaluation"]["conditions"][0]["value"] = 1.1
        self.assertEqual(evaluate_card_window(rule, samples, window, timeline)["proxy_grade"], "C")
        rule["evaluation"]["conditions"][1]["value"] = -1
        self.assertEqual(evaluate_card_window(rule, samples, window, timeline)["proxy_grade"], "E")
        samples[0].time = -1
        self.assertIsNone(evaluate_card_window(rule, samples, window, timeline)["score"])


class CardSummaryTests(unittest.TestCase):
    def row(self, card, event, grade):
        return {"indicator_id": card, "event_id": event, "video_id": "v", "person_track_id": 1,
                "status": "proxy_scored", "proxy_grade": grade, "score": GRADE_POINTS[grade]}

    def test_card_weight_is_not_inflated_by_repetition_and_missing_is_not_zero(self):
        rows = [self.row("a", str(i), "A") for i in range(10)] + [self.row("b", "b", "E")]
        report = summarize_card_scores(rows, ["a", "b", "c"])
        proxy = report["channels"]["visual_proxy"]
        self.assertEqual(proxy["score"], 60)
        self.assertEqual(proxy["coverage"], 2/3)
        self.assertIsNone(report["channels"]["original"]["score"])
        self.assertIsNone(report["overall_score"])

    def test_duplicate_event_is_idempotent_but_conflicting_claims_are_excluded(self):
        row = self.row("a", "e", "A")
        self.assertEqual(summarize_card_scores([row, row], ["a"])["channels"]["visual_proxy"]["scored_event_instances"], 1)
        report = summarize_card_scores([row, self.row("a", "e", "E")], ["a"])
        self.assertEqual(report["conflicting_event_instances"], 1)
        self.assertIsNone(report["channels"]["visual_proxy"]["score"])

    def test_empty_is_null_and_out_of_scope_results_are_rejected(self):
        self.assertIsNone(summarize_card_scores([], [])["channels"]["visual_proxy"]["coverage"])
        with self.assertRaises(ValueError):
            summarize_card_scores([self.row("a", "e", "A")], ["b"])


if __name__ == "__main__":
    unittest.main()
