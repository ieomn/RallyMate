"""Application scoring totals and unavailable data stay distinct."""
from copy import deepcopy
import unittest

from rallymate_scoring.card_evaluation import summarize_card_scores
from rallymate_scoring.card_scoring_policy import (
    SCORING_POLICY_VERSION, FORMAL_SCORE_SEMANTICS, GRADE_POINTS,
    apply_scoring_policy, apply_report_scoring_policy,
)


def result(card="FS01-M02", event="event-1", passed=(True, True, True, True)):
    fraction = sum(passed) / len(passed)
    grade = "A" if fraction == 1 else "B" if fraction >= .75 else "C" if fraction >= .5 else "D" if fraction else "E"
    return {"indicator_id": card, "video_id": "video", "event_id": event, "person_track_id": 1,
            "status": "proxy_scored", "proxy_grade": grade, "score": GRADE_POINTS[grade],
            "reason_codes": [], "conditions": [{"value": index + .5, "passed": flag} for index, flag in enumerate(passed)]}


class CardFormalPolicyTests(unittest.TestCase):
    def test_complete_calculations_receive_formal_grades_without_claiming_original_rubric(self):
        for count, grade in ((4, "A"), (3, "B"), (2, "C"), (1, "D"), (0, "E")):
            with self.subTest(grade=grade):
                row = apply_scoring_policy(result(passed=tuple(i < count for i in range(4))))
                self.assertEqual((row["status"], row["grade"], row["proxy_grade"], row["score"]),
                                 ("scored", grade, grade, 100 * count / 4))
                self.assertEqual(row["scoring_policy_version"], SCORING_POLICY_VERSION)
                self.assertEqual(row["semantics"], FORMAL_SCORE_SEMANTICS)
                self.assertIsNone(row["original_grade"])
                self.assertFalse(row["source_grade_supported"])
                self.assertEqual(apply_scoring_policy(deepcopy(row)), row)

    def test_missing_condition_and_invalid_computed_grade_cannot_turn_into_e_or_a(self):
        candidates = []
        for missing in (None, float("nan"), float("inf"), True):
            row = result()
            row["conditions"][0]["value"] = missing
            candidates.append(row)
        row = result()
        row["score"] = 20
        candidates.append(row)
        row = result()
        row["conditions"] = []
        candidates.append(row)
        for row in candidates:
            converted = apply_scoring_policy(row)
            self.assertEqual(converted["status"], "unavailable")
            self.assertTrue(all(converted[key] is None for key in ("grade", "proxy_grade", "score")))
            self.assertTrue(all(c["passed"] is None for c in converted["conditions"]))

    def test_later_source_rejection_clears_previously_computed_formal_grade(self):
        row = apply_scoring_policy(result())
        row.update(status="unavailable", reason_codes=["source_clock_binding_unverified"])
        rejected = apply_scoring_policy(row)
        self.assertIsNone(rejected["grade"])
        self.assertIsNone(rejected["score"])
        self.assertNotIn("condition_completion_fraction", rejected)

    def test_report_uses_equal_card_weight_and_preserves_unavailable_coverage(self):
        a = result(event="a")
        b = result(event="b", passed=(False,) * 4)
        c = result("FS09-M03", event="c", passed=(True, True, True, False))
        report = {"cards": [
            {"indicator_id": "FS01-M02", "event_code": "FS01", "instances": [a, b], "run_status": "proxy_scored"},
            {"indicator_id": "FS09-M03", "event_code": "FS09", "instances": [c], "run_status": "proxy_scored"},
            {"indicator_id": "GS01-M01-01", "event_code": "GS01", "instances": [], "run_status": "review_required"},
        ], "coverage": {}, "observed_type_summary": {}, "input": {"observed_event_codes": ["FS01", "FS09"]}}
        converted = apply_report_scoring_policy(report)
        self.assertEqual(converted["overall_score"], 62.5)
        self.assertEqual(converted["summary"]["channels"]["formal"]["coverage"], 2 / 3)
        self.assertEqual(converted["summary"]["formal_scored_instances"], 3)
        self.assertEqual(converted["summary"]["formal_scored_cards"], 2)
        self.assertEqual(converted["cards"][-1]["run_status"], "unavailable")
        self.assertEqual(converted["observed_type_summary"]["channels"]["formal"]["applicable_cards"], 2)
        self.assertEqual(apply_report_scoring_policy(deepcopy(converted)), converted)

    def test_duplicate_conflicts_and_legacy_channels_do_not_inflate_formal_total(self):
        first = apply_scoring_policy(result())
        conflicting = deepcopy(first)
        conflicting["conditions"][0]["value"] += 1
        valid = apply_scoring_policy(result("FS09-M03", "valid", (True, True, False, False)))
        historical = result("FS01-M05", "legacy")
        summary = summarize_card_scores([first, first, conflicting, valid, historical],
                                        ["FS01-M02", "FS09-M03", "FS01-M05"])
        self.assertEqual(summary["conflicting_event_instances"], 1)
        self.assertEqual(summary["overall_score"], 50)
        self.assertEqual(summary["formal_scored_instances"], 1)
        self.assertEqual(summary["channels"]["visual_proxy"]["score"], 100)

    def test_wrong_policy_or_missing_event_cannot_enter_formal_aggregation(self):
        wrong = apply_scoring_policy(result())
        wrong["scoring_policy_version"] = "unrecognized"
        missing = apply_scoring_policy(result(event=""))
        summary = summarize_card_scores([wrong, missing], ["FS01-M02"])
        self.assertIsNone(summary["overall_score"])
        self.assertEqual(summary["formal_scored_cards"], 0)

    def test_no_observations_produce_an_empty_total_not_zero(self):
        report = apply_report_scoring_policy({"cards": [], "coverage": {}, "input": {}})
        self.assertIsNone(report["overall_score"])
        self.assertIsNone(report["summary"]["channels"]["formal"]["coverage"])
        self.assertEqual(report["summary"]["formal_scored_cards"], 0)


if __name__ == "__main__":
    unittest.main()
