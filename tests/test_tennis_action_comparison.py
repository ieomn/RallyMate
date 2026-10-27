import unittest
from scripts.compare_tennis_action_replays import compare


class TennisActionComparisonTests(unittest.TestCase):
    def review(self):
        return {"reviewed_intervals": [[0, 2000]], "anchor_uncertainty_ms": 50,
                "positive_actions": [{"manual_action_id": "a", "person_track_id": 1,
                    "anchor_ms": 1000, "technique": "serve", "phase_evidence_ms":
                    {"preparation": 400, "forward_swing": 1000, "follow_through": 1400}}]}

    def prediction(self, **kw):
        return {"episode_id": "auto", "person_track_id": 1, "start_ms": 200,
                "peak_ms": 1000, "end_ms": 1600, "family": "serve", **kw}

    def test_wrong_subject_cannot_match(self):
        result = compare([self.prediction(person_track_id=2)], self.review())
        self.assertEqual(result["localized_count"], 0)
        self.assertEqual(len(result["unmatched_predictions"]), 1)

    def test_duplicate_predictions_do_not_inflate_recall(self):
        result = compare([self.prediction(), self.prediction(episode_id="duplicate")], self.review())
        self.assertEqual(result["localized_count"], 1)
        self.assertEqual(len(result["unmatched_predictions"]), 1)

    def test_localization_does_not_imply_phase_or_anchor_accuracy(self):
        result = compare([self.prediction(start_ms=800, peak_ms=1100, end_ms=1200)], self.review())
        self.assertEqual(result["localized_count"], 1)
        self.assertEqual(result["anchor_within_uncertainty_count"], 0)
        self.assertEqual(result["all_three_phase_observations_covered_count"], 0)

    def test_predictions_outside_reviewed_interval_do_not_affect_false_positive_count(self):
        result = compare([self.prediction(peak_ms=2100)], self.review())
        self.assertEqual(result["prediction_count_in_reviewed_intervals"], 0)


if __name__ == "__main__":
    unittest.main()
