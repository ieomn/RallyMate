from __future__ import annotations

import copy
import json
import unittest

from rallymate_service.relative_score import build_relative_reference
from rallymate_service.process_evaluation import build_process_evaluation
from tests.test_process_evaluation import episode, summary


class RelativeReferenceTests(unittest.TestCase):
    def test_no_measurements_remain_null_and_old_neutral_prior_is_history_only(self):
        for value in (None, [], {}, [{"id": "swing", "criteria": [], "status": "unavailable"}]):
            result = build_relative_reference(value)
            self.assertIsNone(result["overall_score"])
            self.assertEqual(result["observed_dimension_count"], 0)
            self.assertTrue(all(row["basis"] == "unavailable" and row["score"] is None for row in result["dimensions"]))
            self.assertEqual(result["legacy_relative_reference"]["overall_score"], 50)
            self.assertFalse(result["legacy_relative_reference"]["contributes_to_current_score"])
            self.assertNotIn("neutral_score", result)
            json.dumps(result, allow_nan=False)

    def test_curve_direction_changes_legacy_diagnostic_but_never_current_score(self):
        high = build_process_evaluation(summary(episode(increase=.8, decrease=.7)))["relative_reference"]
        low = build_process_evaluation(summary(episode(increase=.2, decrease=.3)))["relative_reference"]
        self.assertEqual(high["version"], "relative-reference-v3.0.0")
        self.assertEqual((high["overall_score"], low["overall_score"]), (None, None))
        self.assertEqual(high["legacy_diagnostic"]["overall_score"], 52.5)
        self.assertEqual(low["legacy_diagnostic"]["overall_score"], 47.5)
        self.assertEqual([row["score"] for row in high["legacy_diagnostic"]["dimensions"]], [50, 50, 57.5, 50, 55])
        self.assertEqual(high["observed_dimension_count"], 0)
        self.assertFalse(high["legacy_diagnostic"]["is_technical_quality"])
        self.assertFalse(high["legacy_diagnostic"]["contributes_to_current_score"])

    def test_excluded_curve_samples_change_only_historical_prior(self):
        one = build_process_evaluation(summary(episode(increase=.8, decrease=.7)))["relative_reference"]
        many = build_process_evaluation(summary(*(episode(f"motion-{i}", 100 + i * 1000, .8, .7) for i in range(3))))["relative_reference"]
        self.assertEqual(many["overall_score"], one["overall_score"])
        self.assertIsNone(many["overall_score"])
        self.assertEqual(many["legacy_diagnostic"]["overall_score"], 55)
        self.assertGreater(many["legacy_diagnostic"]["overall_score"], one["legacy_diagnostic"]["overall_score"])
        self.assertEqual([row["score"] for row in many["legacy_diagnostic"]["dimensions"]], [50, 50, 65, 50, 60])

    def test_sam_without_confidence_uses_same_formula_and_preserves_model_origin(self):
        item = episode()
        item["process_motion"]["measurement_provenance"] = {
            "pose_source": "sam3d", "observation_semantics": "monocular_model_estimate",
            "assessment_status": "measurement_only", "scoring_allowed": False,
            "confidence_available": False, "visibility_verified": False, "model_estimate_sample_count": 20}
        result = build_process_evaluation(summary(item))
        self.assertIsNone(result["overall_score"])
        self.assertIsNone(result["relative_reference"]["overall_score"])
        self.assertEqual(result["relative_reference"]["legacy_diagnostic"]["overall_score"], 52.5)
        swing = result["relative_reference"]["legacy_diagnostic"]["dimensions"][2]
        self.assertEqual(swing["rules"][0]["model_estimate_sample_count"], 1)

    def test_repeated_same_action_does_not_increase_weight_and_conflicts_drop(self):
        dimensions = build_process_evaluation(summary(episode()))["dimensions"]
        expected = build_relative_reference(dimensions)
        swing = dimensions[2]
        acceleration = next(row for row in swing["criteria"] if row["id"] == "acceleration_trend")
        swing["criteria"].append(copy.deepcopy(acceleration))
        self.assertEqual(build_relative_reference(dimensions), expected)
        swing["criteria"][-1]["value"] = 20
        result = build_relative_reference(dimensions)
        self.assertIsNone(result["dimensions"][2]["score"])
        self.assertEqual(result["legacy_diagnostic"]["dimensions"][2]["rules"][0]["excluded_conflicting_episode_count"], 1)

    def test_nonfinite_out_of_range_or_unbound_values_never_make_nan(self):
        for defect in ("nan", "inf", "bool", "huge", "range", "source", "window", "sample"):
            dimensions = build_process_evaluation(summary(episode()))["dimensions"]
            row = next(row for row in dimensions[2]["criteria"] if row["id"] == "acceleration_trend")
            if defect in ("nan", "inf", "bool", "huge", "range"):
                row["value"] = {"nan": float("nan"), "inf": float("inf"), "bool": True, "huge": 10**1000, "range": 101}[defect]
            elif defect == "source":
                row["source"] = "invented"
            elif defect == "window":
                row["end_ms"] = row["start_ms"]
            else:
                row["sample_count"] = 2
            result = build_relative_reference(dimensions)
            self.assertIsNone(result["dimensions"][2]["score"], defect)
            self.assertEqual(result["legacy_diagnostic"]["dimensions"][2]["score"], 50, defect)
            json.dumps(result, allow_nan=False)

    def test_angles_have_explicit_independent_target_direction(self):
        def value(angle):
            return build_relative_reference([{"id": "movement", "status": "partial", "criteria": [{
                "id": "movement_target_alignment", "status": "measured", "value": angle, "unit": "deg",
                "source": "indicator_features.FS02-M02.scoring_context.accepted_operator_reference",
                "start_ms": 0, "end_ms": 100, "episode_id": "step-1"}]}])
        self.assertEqual(value(0)["overall_score"], 100)
        self.assertEqual(value(90)["overall_score"], 50)
        self.assertEqual(value(180)["overall_score"], 0)
        self.assertEqual(value(0)["legacy_relative_reference"]["overall_score"], 52.5)
        self.assertEqual(value(180)["legacy_relative_reference"]["overall_score"], 47.5)
        self.assertEqual(value(0)["dimensions"][0]["gap"]["type"], "none")
        self.assertEqual(value(0)["dimensions"][0]["scoring_audit"]["scored_episode_count"], 1)

    def test_full_measurements_are_distinct_from_current_scoring_and_missing_rules(self):
        result = build_process_evaluation(summary(episode()))["relative_reference"]
        by_id = {row["id"]: row for row in result["dimensions"]}
        self.assertEqual(by_id["movement"]["gap"]["type"], "independent_reference_missing")
        for identifier in ("preparation", "contact"):
            self.assertEqual(by_id[identifier]["gap"]["type"], "rule_not_implemented")
        for identifier, rule in (("swing", "acceleration_trend"), ("follow_through", "deceleration_trend")):
            row = by_id[identifier]
            self.assertEqual(row["gap"]["type"], "selection_biased_proxy_excluded")
            self.assertGreater(row["scoring_audit"]["measured_criterion_count"], 0)
            self.assertEqual(row["scoring_audit"]["scored_episode_count"], 0)
            self.assertEqual(row["scoring_audit"]["excluded_legacy_rule_ids"], [rule])
            self.assertEqual(row["scoring_audit"]["count_scope"], "validated_returned_criteria")

    def test_supplied_v1_scores_are_ignored_and_recomputed_from_measurements(self):
        rows = build_process_evaluation(summary(episode()))["dimensions"]
        before = copy.deepcopy(rows)
        for row in rows:
            row["score"] = 100
            row["legacy_diagnostic"] = {"score": 1}
            row["scoring_audit"] = {"scored_episode_count": 999}
            for criterion in row["criteria"]:
                criterion["score"] = 100
                criterion["legacy_diagnostic"] = {"score": 1}
        result = build_relative_reference(rows)
        self.assertEqual(result, build_relative_reference(before))
        self.assertFalse(result["recalculation"]["cached_scores_used"])

    def test_independent_targets_deduplicate_and_reject_conflicting_episode_values(self):
        criterion = {"id": "movement_target_alignment", "status": "measured", "value": 0, "unit": "deg",
            "source": "indicator_features.FS02-M02.scoring_context.accepted_operator_reference",
            "start_ms": 0, "end_ms": 100, "episode_id": "step-1"}
        rows = [{"id": "movement", "status": "partial", "criteria": [criterion, copy.deepcopy(criterion)]}]
        result = build_relative_reference(rows)
        self.assertEqual(result["overall_score"], 100)
        self.assertEqual(result["dimensions"][0]["rules"][0]["sample_count"], 1)
        rows[0]["criteria"][1]["value"] = 180
        result = build_relative_reference(rows)
        self.assertIsNone(result["overall_score"])
        self.assertEqual(result["dimensions"][0]["rules"][0]["excluded_conflicting_episode_count"], 1)
        self.assertEqual(result["dimensions"][0]["gap"]["type"], "source_conflict")
        self.assertEqual(result["dimensions"][0]["scoring_audit"]["excluded_conflicting_episode_count"], 1)
        rows[0]["criteria"].append({**criterion, "episode_id": "step-2", "start_ms": 200, "end_ms": 300})
        result = build_relative_reference(rows)
        self.assertEqual(result["overall_score"], 100)
        self.assertEqual(result["dimensions"][0]["gap"]["type"], "none")
        self.assertEqual(result["dimensions"][0]["scoring_audit"]["excluded_conflicting_episode_count"], 1)

    def test_invalid_current_rule_inputs_cannot_restore_a_score_or_break_audit(self):
        criterion = {"id": "movement_target_alignment", "status": "measured", "value": 0, "unit": "deg",
            "source": "indicator_features.FS02-M02.scoring_context.accepted_operator_reference",
            "start_ms": 0, "end_ms": 100, "episode_id": "step-1"}
        for key, value in (("value", float("nan")), ("value", True), ("value", 10**1000),
                           ("value", 181), ("source", "self_inferred_direction"), ("unit", "rad"),
                           ("end_ms", 0), ("episode_id", ""), ("id", [])):
            with self.subTest(key=key, value=str(value)[:20]):
                row = {**criterion, key: value}
                result = build_relative_reference([{"id": "movement", "status": "partial", "criteria": [row]}])
                self.assertIsNone(result["overall_score"])
                self.assertEqual(result["dimensions"][0]["scoring_audit"]["scored_episode_count"], 0)
                json.dumps(result, allow_nan=False)

    def test_unscored_observations_do_not_acquire_arbitrary_good_bad_direction(self):
        dimensions = build_process_evaluation(summary(episode()))["dimensions"]
        expected = build_relative_reference(dimensions)
        for dimension in dimensions:
            for row in dimension["criteria"]:
                if row["id"] not in ("acceleration_trend", "deceleration_trend"):
                    row["value"] = 999
                row["score"] = 100  # Forged cached score never participates.
        self.assertEqual(build_relative_reference(dimensions), expected)

    def test_identical_movement_quality_has_no_clip_length_bonus_or_penalty(self):
        def run(n, angle):
            return build_relative_reference([{"id": "movement", "status": "partial", "criteria": [
                {"id": "movement_target_alignment", "status": "measured", "value": angle, "unit": "deg",
                 "source": "indicator_features.FS02-M02.scoring_context.accepted_operator_reference",
                 "start_ms": i * 200, "end_ms": i * 200 + 100, "episode_id": f"step-{i}"}
                for i in range(n)]}])
        for angle, expected in ((0, 100), (36, 80), (90, 50), (180, 0)):
            for count in (1, 3, 10, 100):
                result = run(count, angle)
                self.assertEqual(result["overall_score"], expected)
                self.assertEqual(result["observed_dimension_count"], 1)
                self.assertEqual([d["score"] for d in result["dimensions"]], [expected, None, None, None, None])
                self.assertEqual(result["dimensions"][0]["rules"][0]["measurement_weight"], 1)
        self.assertNotEqual(run(1, 0)["legacy_relative_reference"]["overall_score"],
                            run(10, 0)["legacy_relative_reference"]["overall_score"])

    def test_zero_is_observed_and_missing_is_excluded_from_aggregation(self):
        rows = [{"id": "movement", "status": "partial", "criteria": [
            {"id": "movement_target_alignment", "status": "measured", "value": angle, "unit": "deg",
             "source": "indicator_features.FS02-M02.scoring_context.accepted_operator_reference",
             "start_ms": i * 200, "end_ms": i * 200 + 100, "episode_id": f"step-{i}"}
            for i, angle in enumerate((0, 180, None))]}]
        before = copy.deepcopy(rows)
        result = build_relative_reference(rows)
        self.assertEqual(rows, before)
        self.assertEqual(result["overall_score"], 50)
        self.assertEqual(result["dimensions"][0]["rules"][0]["sample_count"], 2)
        self.assertEqual(result["observed_dimension_count"], 1)
        self.assertTrue(all(d["score"] is None for d in result["dimensions"][1:]))


if __name__ == "__main__":
    unittest.main()
