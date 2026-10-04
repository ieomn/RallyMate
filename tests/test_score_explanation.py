from __future__ import annotations

import copy
import json
import unittest

from rallymate_service.score_explanation import explain_aggregate_score, explain_indicator_score
from rallymate_service.training_evaluation import build_training_evaluation
from tests import test_training_evaluation as evaluation_fixtures


class ScoreExplanationTests(unittest.TestCase):
    def record(self, event_id="event-1"):
        return evaluation_fixtures.TrainingEvaluationTests._record(event_id)

    def indicator(self, records):
        result = build_training_evaluation(records)
        return next(item for item in result["indicator_evaluations"] if item["indicator_id"] == "FS01-M03")

    def assert_closed(self, explanation):
        self.assertEqual(explanation["status"], "available")
        rows = [row for row in explanation["components"] if row["included"]]
        self.assertAlmostEqual(sum(row["max_points"] for row in rows), 100, places=10)
        self.assertAlmostEqual(sum(row["earned_points"] for row in rows), explanation["raw_score"], places=10)
        self.assertAlmostEqual(100 - sum(row["deduction_points"] for row in rows)
                               + explanation["rounding_adjustment_points"], explanation["score_0_to_100"], places=10)
        for row in rows:
            self.assertAlmostEqual(row["value_percent"] * row["effective_weight"], row["earned_points"], places=10)
            self.assertAlmostEqual(row["max_points"] - row["earned_points"], row["deduction_points"], places=10)
        json.dumps(explanation, allow_nan=False)

    def test_single_observation_excludes_repeatability_without_lost_points(self):
        item = self.indicator([self.record()])
        explanation = item["score_explanation"]
        self.assertEqual(item["score_0_to_100"], 96)
        row = next(row for row in explanation["components"] if row["key"] == "repeatability")
        self.assertFalse(row["included"])
        for key in ("value_percent", "effective_weight", "max_points", "earned_points", "deduction_points"):
            self.assertIsNone(row[key])
        self.assertEqual(explanation["measured_instance_count"], 1)
        self.assertEqual(explanation["counts"]["valid_feature_slots"], 4)
        self.assert_closed(explanation)

    def test_missing_confidence_and_no_evidence_stay_excluded(self):
        record = self.record()
        for feature in record["features"]:
            feature.pop("confidence")
        item = self.indicator([record])
        self.assertEqual(item["score_0_to_100"], 100)
        excluded = {row["key"] for row in item["score_explanation"]["components"] if not row["included"]}
        self.assertEqual(excluded, {"repeatability", "median_feature_confidence"})
        self.assert_closed(item["score_explanation"])
        for records in ([], [{**self.record(), "feature_status": "unavailable"}]):
            result = build_training_evaluation(records)
            self.assertIsNone(result["score_0_to_100"])
            for explanation in [result["score_explanation"], *[item["score_explanation"] for item in result["indicator_evaluations"]]]:
                self.assertEqual(explanation["status"], "unavailable")
                self.assertIsNone(explanation["raw_score"])
                self.assertIsNone(explanation["rounding_adjustment_points"])
                self.assertTrue(all(row["deduction_points"] is None for row in explanation["components"]))

    def test_ties_to_even_is_retained_and_accounted_for(self):
        for left, right, score, adjustment in ((80, 81, 80, -0.5), (81, 82, 82, 0.5)):
            explanation = explain_indicator_score(score=score,
                components={"measured_instance_ratio_percent": left, "required_feature_coverage_percent": right},
                effective_weights={"measured_instance_ratio": 0.5, "required_feature_coverage": 0.5}, repeatability_status="insufficient_samples")
            self.assertEqual(explanation["raw_score"], (left + right) / 2)
            self.assertEqual(explanation["rounding_adjustment_points"], adjustment)
            self.assert_closed(explanation)

    def test_aggregate_excludes_missing_indicators_and_preserves_both_rounding_steps(self):
        records = [self.record("one"), self.record("two")]
        records[1]["features"][0]["value"] *= 3
        result = build_training_evaluation(records)
        explanation = result["score_explanation"]
        self.assertEqual(explanation["included_indicator_ids"], ["FS01-M03"])
        self.assertEqual(len(explanation["excluded_indicator_ids"]), 12)
        self.assertEqual(explanation["included_indicator_count"], 1)
        self.assertEqual(explanation["total_indicator_count"], 13)
        self.assertEqual(sum(explanation["rounding_stages"].values()), explanation["rounding_adjustment_points"])
        self.assert_closed(explanation)

    def test_aggregate_component_weights_reflect_each_indicators_missing_components(self):
        first = self.indicator([self.record()])
        second = self.indicator([self.record("a"), self.record("b")])
        second["indicator_id"] = "second-test-indicator"
        overall = explain_aggregate_score([first, second], 96)  # mean(96,97) rounds to even 96
        self.assertEqual(overall["rounded_indicator_mean"], 96.5)
        repeat = next(row for row in overall["components"] if row["key"] == "repeatability")
        self.assertEqual(repeat["included_indicator_count"], 1)
        self.assertEqual(repeat["max_points"], 12.5)
        self.assertEqual(repeat["deduction_points"], 0)
        self.assertEqual(overall["rounding_stages"]["aggregate_rounding_points"], -0.5)
        self.assert_closed(overall)

    def test_feature_gaps_distinguish_missing_values_from_measurement_gate_exclusion(self):
        missing, blocked = self.record("missing"), self.record("blocked")
        removed = missing["features"].pop()
        blocked["feature_status"] = "unavailable"
        item = self.indicator([self.record("good"), missing, blocked])
        explanation = item["score_explanation"]
        gap = next(row for row in explanation["feature_gaps"] if row["feature_name"] == removed["feature_name"])
        self.assertEqual(gap["required_instance_count"], 3)
        self.assertEqual(gap["missing_instance_count"], 1)
        self.assertEqual(gap["excluded_by_measurement_gate_count"], 1)
        self.assertEqual(gap["available_instance_count"], 1)
        self.assertEqual(gap["event_ids"], ["blocked", "missing"])
        self.assertEqual(sum(row["unavailable_instance_count"] for row in explanation["feature_gaps"]),
                         explanation["counts"]["required_feature_slots"] - explanation["counts"]["valid_feature_slots"])
        self.assert_closed(explanation)

    def test_blocker_counts_overlap_and_replay_examples_are_bounded(self):
        records = [self.record(f"event-{index}") for index in range(5)]
        for record in records:
            record["quality_gate"].update(scoring_allowed=False, scoring_block_flags=[
                "keypoint_jump_candidates_present", "keypoint_jump_candidates_present", "left_right_swap_candidates_present"])
        explanation = self.indicator(records)["score_explanation"]
        self.assertEqual(len(explanation["blocker_reasons"]), 2)
        self.assertEqual(sum(row["affected_instance_count"] for row in explanation["blocker_reasons"]), 10)
        for row in explanation["blocker_reasons"]:
            self.assertEqual(row["affected_instance_count"], 5)
            self.assertEqual(len(row["event_ids"]), 3)
            self.assertEqual(row["event_id_count"], 5)
            self.assertTrue(row["event_ids_truncated"])
        self.assert_closed(explanation)

    def test_duplicate_conflicting_and_wrong_event_records_do_not_inflate_explanations(self):
        first, conflict, wrong = self.record(), self.record(), self.record("wrong")
        conflict["features"][0]["value"] += 1
        wrong["event_code"] = "FS09"
        result = build_training_evaluation([first, copy.deepcopy(first), self.record("good"), conflict, wrong])
        item = next(item for item in result["indicator_evaluations"] if item["indicator_id"] == "FS01-M03")
        self.assertEqual(item["score_explanation"]["total_instance_count"], 1)
        self.assertEqual(item["score_explanation"]["counts"]["required_feature_slots"], 4)
        self.assertEqual(result["input_validation"]["conflicting_record_count"], 3)
        self.assertEqual(result["input_validation"]["event_code_mismatch_record_count"], 1)

    def test_nonfinite_boolean_and_malicious_measurements_never_form_explanation_points(self):
        for value in (float("nan"), float("inf"), True, "<script>alert(1)</script>", 10**500):
            with self.subTest(value_type=type(value).__name__):
                record = self.record()
                for feature in record["features"]:
                    feature["value"] = value
                item = self.indicator([record])
                self.assertIsNone(item["score_0_to_100"])
                self.assertEqual(item["score_explanation"]["status"], "unavailable")
                self.assertEqual(len(item["score_explanation"]["feature_gaps"]), 4)
                serialized = json.dumps(item["score_explanation"], allow_nan=False)
                self.assertNotIn("script", serialized)

    def test_invalid_weights_values_or_mismatched_score_fail_closed_without_recalculation(self):
        for value, weight, score in ((100, -1, 100), (100, 0, 100), (100, True, 100),
                                    (100, float("inf"), 100), (101, 1, 100), (float("nan"), 1, 0),
                                    (True, 1, 1), (100, 1, 99), (100, 1, True), (10**500, 1, 100)):
            result = explain_indicator_score(score=score, components={"measured_instance_ratio_percent": value},
                effective_weights={"measured_instance_ratio": weight}, repeatability_status="insufficient_samples")
            self.assertEqual(result["status"], "unavailable")
            self.assertIsNone(result["score_0_to_100"])
            json.dumps(result, allow_nan=False)

    def test_unknown_blocker_payload_is_not_reflected_as_explanatory_text(self):
        record = self.record()
        record["quality_gate"].update(scoring_allowed=False, scoring_block_flags=[
            "<script>send-secrets()</script>", None, {"unexpected": "value"}, "x" * 1000])
        explanation = self.indicator([record])["score_explanation"]
        self.assertEqual(explanation["blocker_reasons"][0]["key"], "other_evidence_condition")
        rendered = json.dumps(explanation, ensure_ascii=False, allow_nan=False)
        self.assertNotIn("send-secrets", rendered)
        self.assertNotIn("unexpected", rendered)
        self.assert_closed(explanation)


if __name__ == "__main__":
    unittest.main()
