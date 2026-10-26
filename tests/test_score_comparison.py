import copy
import unittest

from rallymate_scoring.card_scoring_policy import SCORING_POLICY_VERSION, FORMAL_SCORE_SEMANTICS
from rallymate_scoring.score_comparison import compare_card_reports


def report(values):
    cards = []
    for identifier, passes in values.items():
        rows = []
        for index, passed in enumerate(passes):
            rows.append({"status": "scored", "event_id": str(index), "video_id": "one", "person_track_id": 1,
                "scoring_policy_version": SCORING_POLICY_VERSION, "semantics": FORMAL_SCORE_SEMANTICS,
                "grade": "A" if passed else "E", "score": 100 if passed else 0, "reason_codes": [],
                "conditions": [{"metric": "example", "unit": "ratio", "operator": "gte", "threshold": .5,
                    "value": .8 if passed else .2, "passed": passed}]})
        cards.append({"indicator_id": identifier, "event_code": identifier.split("-")[0], "instances": rows})
    return {"scoring_policy_version": SCORING_POLICY_VERSION, "cards": cards}


class ScoreComparisonTests(unittest.TestCase):
    def test_unmatched_easy_conditions_do_not_reverse_shared_comparison(self):
        left = report({"GS01-M01": [True, False], "GS01-M02": [False]})
        right = report({"GS01-M01": [False], "FS01-M01": [True], "FS01-M02": [True]})
        result = compare_card_reports(left, right)
        self.assertEqual(result["matched_card_count"], 1)
        self.assertEqual(result["difference_left_minus_right"], 50)
        self.assertEqual(result["right_unmatched_card_ids"], ["FS01-M01", "FS01-M02"])
        self.assertEqual(result["statistical_significance"], "not_established")

    def test_repeated_events_do_not_change_card_weight(self):
        result = compare_card_reports(report({"GS01-M01": [True]*20, "GS01-M02": [False]}),
                                      report({"GS01-M01": [False], "GS01-M02": [False]}))
        self.assertEqual(result["left_matched_score"], 50)

    def test_identity_labels_and_cached_totals_never_affect_scores(self):
        left, right = report({"GS01-M01": [False]}), report({"GS01-M01": [True]})
        expected = compare_card_reports(left, right)
        left.update(athlete_level="professional", overall_score=100, score=100)
        right.update(athlete_level="beginner", overall_score=0)
        self.assertEqual(compare_card_reports(left, right), expected)

    def test_mismatched_thresholds_actions_or_policy_are_not_ranked(self):
        left = report({"GS01-M01": [True]})
        for modify, reason in [
            (lambda right: right["cards"][0]["instances"][0]["conditions"][0].update(threshold=.2), "condition_reference_mismatch"),
            (lambda right: right["cards"][0]["instances"][0].update(measurement_version="different-definition"), "condition_reference_mismatch"),
            (lambda right: right["cards"][0].update(rule_version="different-rule"), "condition_reference_mismatch"),
            (lambda right: right["cards"][0].update(event_code="FS01"), "action_type_mismatch"),
        ]:
            right = copy.deepcopy(left)
            modify(right)
            result = compare_card_reports(left, right)
            self.assertIsNone(result["difference_left_minus_right"])
            self.assertEqual(result["excluded_shared_cards"][0]["reason"], reason)
        right = copy.deepcopy(left)
        right["scoring_policy_version"] = "unknown"
        self.assertIsNone(compare_card_reports(left, right)["left_matched_score"])

    def test_tampered_or_conflicting_rows_and_duplicate_cards_are_excluded(self):
        original = report({"GS01-M01": [True]})
        for variation in ("score", "duplicate_card", "conflict", "same_score_conflicting_measurement"):
            right = copy.deepcopy(original)
            if variation == "score":
                right["cards"][0]["instances"][0]["score"] = 99
            elif variation == "duplicate_card":
                right["cards"].append(copy.deepcopy(right["cards"][0]))
            elif variation == "conflict":
                conflict = copy.deepcopy(report({"GS01-M01": [False]})["cards"][0]["instances"][0])
                right["cards"][0]["instances"].append(conflict)
            else:
                conflict = copy.deepcopy(right["cards"][0]["instances"][0])
                conflict["conditions"][0]["value"] = .9
                right["cards"][0]["instances"].append(conflict)
            self.assertEqual(compare_card_reports(original, right)["status"], "no_comparable_conditions")


if __name__ == "__main__":
    unittest.main()
