import math
import unittest

from rallymate_scoring.action_aggregation import aggregate_action_dimensions, episode_dimension_scores


DEFINITIONS = [("movement", "Movement", [("a", "A", "deg", "A"), ("b", "B", "deg", "B")]),
               ("striking", "Striking", [("c", "C", "ratio", "C")])]


class ActionAggregationTests(unittest.TestCase):
    def test_sparse_episode_has_equal_weight_and_zero_remains_observed(self):
        episodes = [{"metrics": {"a": (1., 0.), "b": (2., 100.)}},
                    {"metrics": {"a": (3., 100.)}}]
        dimensions = aggregate_action_dimensions(episodes, DEFINITIONS)
        self.assertEqual(dimensions[0]["score"], 75)
        self.assertEqual(dimensions[0]["observation_count"], 2)
        a, b = dimensions[0]["metrics"]
        self.assertEqual((a["weight"], b["weight"]), (.75, .25))
        self.assertEqual(a["observation_count"], 2)
        self.assertIsNone(dimensions[1]["score"])

    def test_invalid_values_do_not_score_or_change_denominator(self):
        for pair in [(math.nan, 100), (1, math.inf), (True, 100), (1, True), (None, 100), (1, -1), (1, 101)]:
            with self.subTest(pair=pair):
                actual = aggregate_action_dimensions([{"metrics": {"a": (1, 0), "b": pair}}], DEFINITIONS)
                self.assertEqual(actual[0]["score"], 0)
                self.assertEqual(actual[0]["metrics"][0]["weight"], 1)
                self.assertIsNone(actual[0]["metrics"][1]["score"])

    def test_level_adapter_and_report_share_episode_means(self):
        metrics = {"a": {"value": 10, "score": 40, "confidence": .001},
                   "b": {"value": 20, "score": 80, "confidence": 1}}
        dimensions = aggregate_action_dimensions([{"metrics": metrics}], DEFINITIONS)
        scores = episode_dimension_scores(metrics, {key: [row[0] for row in rows] for key, _, rows in DEFINITIONS})
        self.assertEqual(scores, {"movement": 60, "striking": None})
        self.assertEqual(scores["movement"], dimensions[0]["score"])

    def test_permutation_and_replication_preserve_scores(self):
        episodes = [{"metrics": {"a": (1., 0.), "b": (2., 100.)}}, {"metrics": {"a": (3., 100.)}}]
        original = aggregate_action_dimensions(episodes, DEFINITIONS)
        self.assertEqual(aggregate_action_dimensions(iter(episodes), DEFINITIONS), original)
        self.assertEqual(aggregate_action_dimensions(list(reversed(episodes)), DEFINITIONS), original)
        repeated = aggregate_action_dimensions(episodes * 4, DEFINITIONS)
        self.assertEqual(repeated[0]["score"], original[0]["score"])
        self.assertEqual(repeated[0]["observation_count"], 8)

    def test_duplicate_or_unassigned_metrics_fail_instead_of_double_counting(self):
        with self.assertRaises(ValueError):
            aggregate_action_dimensions([], DEFINITIONS + [("other", "Other", [("a", "A", "deg", "A")])])
        with self.assertRaises(ValueError):
            aggregate_action_dimensions([{"metrics": {"unknown": (1, 100)}}], DEFINITIONS)

    def test_empty_report_has_no_numeric_scores(self):
        result = aggregate_action_dimensions([], DEFINITIONS)
        self.assertTrue(all(d["score"] is None and d["observation_count"] == 0 for d in result))


if __name__ == "__main__":
    unittest.main()
