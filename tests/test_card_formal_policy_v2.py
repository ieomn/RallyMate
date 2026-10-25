"""Fresh completion scores and explicitly preserved historical v1 grades."""
from copy import deepcopy
import unittest

from rallymate_scoring.card_scoring_policy import (
    SCORING_POLICY_VERSION, LEGACY_SCORING_POLICY_VERSION, FORMAL_SCORE_SEMANTICS,
    GRADE_POINTS, apply_scoring_policy, apply_report_scoring_policy,
)
from rallymate_scoring.card_evaluation import summarize_card_scores


def internal(passed, count, card="FS01-M02", event="one"):
    fraction = passed / count if count else 0
    grade = "A" if fraction == 1 else "B" if fraction >= .75 else "C" if fraction >= .5 else "D" if fraction else "E"
    return {"indicator_id": card, "video_id": "v", "event_id": event, "person_track_id": 1,
        "status": "proxy_scored", "proxy_grade": grade, "score": GRADE_POINTS[grade], "reason_codes": [],
        "conditions": [{"value": i+.25, "passed": i < passed} for i in range(count)]}


class FormalCompletionV2Tests(unittest.TestCase):
    def test_all_counts_zero_through_four_and_repeated_finalization(self):
        for count in range(5):
            for passed in range(count+1):
                with self.subTest(count=count, passed=passed):
                    row = apply_scoring_policy(internal(passed, count))
                    self.assertEqual(row["scoring_policy_version"], SCORING_POLICY_VERSION)
                    self.assertEqual(row["score"], 100*passed/count if count else None)
                    self.assertEqual(row["status"], "scored" if count else "unavailable")
                    self.assertEqual(apply_scoring_policy(deepcopy(row)), row)
                    if count:
                        self.assertEqual(row["condition_completion_fraction"], passed/count)
                        self.assertIsNone(row["original_grade"])

    def test_old_v1_is_validated_in_its_original_scale_and_not_relabelled(self):
        for passed, count in ((0, 4), (1, 2), (1, 3), (2, 3), (3, 4)):
            old = internal(passed, count)
            old.update(status="scored", grade=old["proxy_grade"], semantics=FORMAL_SCORE_SEMANTICS,
                       scoring_policy_version=LEGACY_SCORING_POLICY_VERSION)
            result = apply_scoring_policy(deepcopy(old))
            self.assertEqual(result["scoring_policy_version"], LEGACY_SCORING_POLICY_VERSION)
            self.assertEqual(result["score"], old["score"])
            self.assertEqual(summarize_card_scores([result], [result["indicator_id"]])["overall_score"], old["score"])

    def test_relabelled_old_score_cannot_enter_v2(self):
        row = apply_scoring_policy(internal(1, 2))
        row["score"] = 60
        self.assertEqual(apply_scoring_policy(row)["status"], "unavailable")
        self.assertIsNone(row["score"])

    def test_missing_or_nonfinite_is_not_zero_completion(self):
        for value in (None, float("nan"), float("inf"), True, "0"):
            row = internal(0, 3);row["conditions"][0]["value"] = value
            self.assertIsNone(apply_scoring_policy(row)["score"])
        valid = apply_scoring_policy(internal(0, 3))
        self.assertEqual((valid["grade"], valid["score"]), ("E", 0))

    def test_summary_checks_version_arithmetic_and_does_not_pool_mixed_versions(self):
        row = apply_scoring_policy(internal(1, 3))
        self.assertEqual(summarize_card_scores([row], [row["indicator_id"]])["overall_score"], 100/3)
        invalid = deepcopy(row);invalid["score"] = 40
        self.assertIsNone(summarize_card_scores([invalid], [row["indicator_id"]])["overall_score"])
        old = deepcopy(row);old.update(event_id="old", score=40, scoring_policy_version=LEGACY_SCORING_POLICY_VERSION)
        self.assertIsNone(summarize_card_scores([row, old], [row["indicator_id"]])["overall_score"])

    def test_equal_card_weight_and_report_idempotence(self):
        report = {"cards": [
            {"indicator_id": "FS01-M02", "event_code": "FS01", "instances": [internal(0, 2), internal(2, 2, event="two")], "run_status": "proxy_scored"},
            {"indicator_id": "FS09-M03", "event_code": "FS09", "instances": [internal(3, 4, "FS09-M03")], "run_status": "proxy_scored"},
            {"indicator_id": "FS01-M05", "event_code": "FS01", "instances": [], "run_status": "not_observed"}], "coverage": {}}
        actual = apply_report_scoring_policy(report)
        self.assertEqual(actual["overall_score"], 62.5)
        self.assertEqual(actual["summary"]["formal_scored_instances"], 3)
        self.assertEqual(actual["summary"]["channels"]["formal"]["coverage"], 2/3)
        self.assertEqual(apply_report_scoring_policy(deepcopy(actual)), actual)

    def test_report_does_not_relabel_whole_legacy_or_mix_policies(self):
        old = internal(1, 2)
        old.update(status="scored", grade="C", semantics=FORMAL_SCORE_SEMANTICS,
                   scoring_policy_version=LEGACY_SCORING_POLICY_VERSION)
        legacy_report = {"cards": [{"indicator_id": "FS01-M02", "event_code": "FS01",
                         "instances": [old], "run_status": "scored"}], "coverage": {}}
        actual = apply_report_scoring_policy(deepcopy(legacy_report))
        self.assertEqual(actual["scoring_policy_version"], LEGACY_SCORING_POLICY_VERSION)
        self.assertEqual(actual["overall_score"], 60)
        fresh = apply_scoring_policy(internal(1, 2, event="new"))
        for mismatch in ("two_instance_versions", "outer_label_only"):
            report = deepcopy(legacy_report)
            if mismatch == "two_instance_versions":report["cards"][0]["instances"].append(fresh)
            else:report["scoring_policy_version"] = SCORING_POLICY_VERSION
            result = apply_report_scoring_policy(report)
            self.assertIsNone(result["scoring_policy_version"])
            self.assertIsNone(result["overall_score"])
            self.assertTrue(all(i["score"] is None for i in result["cards"][0]["instances"]))
            self.assertEqual(apply_report_scoring_policy(deepcopy(result)), result)

    def test_actual_recorded_native_fs05_three_cards_fresh_computation(self):
        from tests.test_direction_reference_observables import compute, real_args, real_fixture
        from tests.test_micro_adjustment_cycles import compute as cycles, real_args as cycle_args
        direction = compute(real_args(real_fixture()))
        micro = cycles(cycle_args())
        for report, expected, flags in ((direction, 200/3, [False, True, True]), (micro, 50, [False, True])):
            self.assertEqual(report["scoring_policy_version"], SCORING_POLICY_VERSION)
            for card in report["cards"]:
                result = card["instances"][0]
                self.assertEqual(result["source_window_evidence"]["source_frame_bindings"][0]["frame_index"], 112)
                self.assertEqual(result["score"], expected)
                self.assertEqual(result["grade"], "C")
                self.assertEqual([c["passed"] for c in result["conditions"]], flags)
            self.assertEqual(apply_report_scoring_policy(deepcopy(report)), report)

    def test_fresh_missing_target_remains_unavailable(self):
        from tests.test_direction_reference_observables import compute, real_args, real_fixture
        args = real_args(real_fixture())
        for row in args[-1]["windows"]:
            row["direction_reference"].pop("target_point_px")
            row["direction_reference"].pop("target_kind")
        report = compute(args)
        self.assertTrue(all(r["score"] is None for c in report["cards"] for r in c["instances"]))


if __name__ == "__main__":
    unittest.main()
