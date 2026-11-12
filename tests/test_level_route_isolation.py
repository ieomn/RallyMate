"""Resolved techniques cannot bypass level weights via family fallback."""
from copy import deepcopy
import unittest

from rallymate_service.level_assessment import build_level_assessments


def episode(profile, score, *, family="baseline", movement=None):
    metrics = {"coordination": {"value": 1.0, "score": score}}
    if movement is not None:
        metrics["support"] = {"value": 1.0, "score": movement}
    return {"family": family, "motion_pattern": {"profile_id": profile}, "metrics": metrics}


def report(*episodes):
    return {"source_video_sha256": "a" * 64, "source_run_id": "run",
            "dimensions": [{"id": "swing", "metrics": [{"id": "coordination"}]},
                           {"id": "movement", "metrics": [{"id": "support"}]}],
            "episodes": list(episodes)}


class LevelRouteIsolationTests(unittest.TestCase):
    def test_explicitly_excluded_backhand_does_not_reenter_as_family(self):
        for profile, style in (("baseline_two_hand_backhand", "one_handed"),
                               ("baseline_one_hand_backhand", "two_handed")):
            for result in build_level_assessments(report(episode(profile, 80)), backhand_style=style):
                with self.subTest(profile=profile, level=result["target_level"]):
                    self.assertIsNone(result["domains"]["GS"]["score"])
                    self.assertIsNone(result["domains"]["GS"]["family_score"])
                    self.assertIsNone(result["overall_score"])

    def test_return_route_isolation_uses_the_same_rule(self):
        results = build_level_assessments(report(episode("return_two_hand_backhand", 80, family="return")),
                                          backhand_style="one_handed")
        self.assertTrue(all(result["domains"]["RS"]["score"] is None for result in results))

    def test_zero_weight_technique_does_not_reenter_but_higher_level_can_use_it(self):
        levels = build_level_assessments(report(episode("forehand_slice", 85)))
        self.assertEqual(levels[0]["domains"]["GS"]["score"], 85)
        self.assertIsNone(levels[1]["domains"]["GS"]["score"])
        self.assertEqual(levels[-1]["domains"]["GS"]["score"], 85)
        overridden = build_level_assessments(report(episode("baseline_forehand", 85)),
                                             technique_weights={"GS": {"GS01": 0, "GS02": 1}})
        self.assertTrue(all(result["domains"]["GS"]["score"] is None for result in overridden))

    def test_unknown_subtype_keeps_only_its_own_family_evidence(self):
        data = report(episode("baseline_two_hand_backhand", 100), episode("unclassified_baseline", 25))
        for result in build_level_assessments(data, backhand_style="one_handed"):
            self.assertEqual(result["domains"]["GS"]["score"], 25)
            self.assertEqual(result["domains"]["GS"]["family_score"], 25)
            self.assertEqual(result["domains"]["GS"]["score_source"], "observed_family")

    def test_default_route_does_not_average_in_less_frequent_backhand(self):
        data = report(episode("baseline_two_hand_backhand", 60), episode("baseline_two_hand_backhand", 80),
                      episode("baseline_one_hand_backhand", 100))
        result = build_level_assessments(data)[0]
        self.assertEqual(result["backhand_style"], "two_handed")
        self.assertEqual(result["domains"]["GS"]["score"], 70)
        self.assertIsNone(result["domains"]["GS"]["family_score"])

    def test_movement_is_selected_by_id_and_missing_movement_stays_missing(self):
        data = report(episode("baseline_forehand", 80, movement=20))
        result = build_level_assessments(data)[0]
        self.assertEqual(result["domains"]["FS"]["score"], 20)
        swapped = deepcopy(data)
        swapped["dimensions"].reverse()
        self.assertEqual(build_level_assessments(swapped), build_level_assessments(data))
        self.assertIsNone(build_level_assessments(report(episode("baseline_forehand", 80)))[0]["domains"]["FS"]["score"])

    def test_zero_is_valid_evidence_but_nonfinite_values_are_not(self):
        data = report(episode("baseline_forehand", 0))
        self.assertEqual(build_level_assessments(data)[0]["overall_score"], 0)
        data["episodes"][0]["metrics"]["coordination"]["value"] = float("nan")
        self.assertIsNone(build_level_assessments(data)[0]["overall_score"])


if __name__ == "__main__":
    unittest.main()
