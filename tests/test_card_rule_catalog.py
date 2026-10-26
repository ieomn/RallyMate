from __future__ import annotations

from collections import Counter
from copy import deepcopy
import importlib.util
import json
from pathlib import Path
import unittest

from rallymate_scoring.card_rule_catalog import (
    GRADE_MAPPING, VERSION, catalogue_coverage, get_card_rule, get_stage_rule, load_card_rules,
)

ROOT = Path(__file__).resolve().parents[1]


class CardRuleCatalogueTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.catalog = load_card_rules()
        cls.reference = json.loads((ROOT / "contracts/scoring-reference-20261004.json").read_text(encoding="utf-8"))

    def test_exact_source_inventory_and_unaltered_text(self):
        originals = {row["indicator_id"]: row for row in self.reference["indicators"]}
        self.assertEqual(set(originals), {row["indicator_id"] for row in self.catalog["cards"]})
        self.assertEqual(Counter(row["event_code"][:2] for row in self.catalog["cards"]), {"GS": 248, "FS": 50})
        for card in self.catalog["cards"]:
            for key, value in originals[card["indicator_id"]]["source_fields"].items():
                with self.subTest(card=card["indicator_id"], field=key):
                    saved = card["source"]["fields"][key]
                    self.assertEqual(value["text"], saved["text"])
                    self.assertTrue(saved["locators"])
                    self.assertTrue(saved["cells"])

    def test_three_conflicts_change_semantics_and_dependencies_not_titles(self):
        gaze, grip = get_card_rule("GS02-M01-01"), get_card_rule("GS02-M01-02")
        self.assertEqual(gaze["effective"]["name_zh"], "盯球观察")
        self.assertIn("观察来球", gaze["effective"]["definition"])
        self.assertIn("双手握拍", gaze["source"]["fields"]["技术定义"]["text"])
        self.assertEqual(gaze["evaluation"]["kind"], "manual_review")
        self.assertIn("拍柄", grip["effective"]["calculation"])
        self.assertNotIn("活动球", grip["effective"]["required_points"])
        self.assertIn("观察", gaze["effective"]["key_items"])
        self.assertNotIn("双手", gaze["effective"]["key_items"])
        self.assertIn("拍柄", grip["effective"]["key_items"])
        self.assertNotIn("视线", grip["effective"]["key_items"])
        recovery = get_card_rule("GS01-M10-04")
        self.assertEqual(recovery["source"]["fields"]["技术定义"]["text"], "GS-02 双手反手")
        self.assertIn("下一次", recovery["effective"]["definition"])
        for card in (gaze, grip, recovery):
            self.assertTrue(card["revision_ids"])
            self.assertIn("observation_points", card["effective"])
            self.assertIn("positive_feedback", card["effective"])
            self.assertIn("工程修订", card["effective"]["source_status"])

    def test_reviewed_compound_behaviors_do_not_score_mismatched_partial_motion(self):
        checks = {
            "GS01-M03-04": "racket_side_loading_review",
            "GS01-M06-03": "pre_acceleration_posture_review",
            "GS01-M04-01": "support_leg_push_review",
            "GS02-M04-01": "support_leg_push_review",
            "GS03-M04-01": "support_leg_push_review",
            "GS02-M08-01": "contact_control_review",
            "GS03-M08-01": "contact_control_review",
        }
        for identifier, family in checks.items():
            with self.subTest(indicator=identifier):
                evaluation = get_card_rule(identifier)["evaluation"]
                self.assertEqual(evaluation["kind"], "manual_review")
                self.assertEqual(evaluation["family"], family)
                self.assertEqual(evaluation["conditions"], [])
        crossing = get_card_rule("FS03-M03")["evaluation"]
        self.assertIn("步幅/髋宽比", crossing["limitation"])
        self.assertEqual(crossing["source_behavior_coverage"], "partial_observations_only")

    def test_maintenance_is_not_movement_and_preparation_is_not_execution(self):
        for identifier in ("GS01-M04-06", "GS01-M05-06", "GS03-M05-05", "GS04-M04-02", "GS04-M05-04", "GS05-M04-02", "GS05-M05-04", "FS05-M03", "FS05-M04"):
            with self.subTest(indicator=identifier):
                self.assertEqual(get_card_rule(identifier)["evaluation"]["kind"], "manual_review")
        maintenance = get_card_rule("GS01-M05-05")["evaluation"]
        self.assertEqual(maintenance["family"], "projected_separation_maintenance")
        by_metric = {c["metric"]: c for c in maintenance["conditions"]}
        self.assertEqual(by_metric["shoulder_hip_separation_deg.range"]["operator"], "lte")
        self.assertEqual(by_metric["shoulder_hip_separation_deg.mean"]["operator"], "gte")
        for identifier in ("FS06-M05", "FS07-M05"):
            conditions = get_card_rule(identifier)["evaluation"]["conditions"]
            self.assertTrue(conditions)
            self.assertFalse(any("speed_drop" in c["metric"] for c in conditions))

    def test_spread_posture_is_not_repeated_opening_and_lowering_is_local(self):
        for identifier in ("GS03-M07-03", "GS03-M09-02", "GS04-M07-04", "GS04-M09-04", "GS05-M07-04", "GS05-M09-04"):
            conditions = get_card_rule(identifier)["evaluation"]["conditions"]
            self.assertIn("wrist_separation_torso.median", {c["metric"] for c in conditions})
            self.assertNotIn("wrist_separation_torso.delta", {c["metric"] for c in conditions})
        for identifier in ("GS03-M06-04", "GS04-M06-04", "GS05-M06-04"):
            conditions = get_card_rule(identifier)["evaluation"]["conditions"]
            self.assertIn("wrist_separation_torso.delta", {c["metric"] for c in conditions})
        for identifier in ("GS01-M01-03", "GS02-M01-04", "GS03-M01-03", "GS04-M01-03", "GS05-M01-03"):
            evaluation = get_card_rule(identifier)["evaluation"]
            self.assertEqual(evaluation["kind"], "manual_review")
            self.assertEqual(evaluation["required_engine"], "temporal_preload_subwindow_required")
            self.assertIs(evaluation["fallback_binding"]["reuse_score"], False)
            self.assertIs(evaluation["fallback_binding"]["requires_confirmed_split_step"], True)

    def test_all_missing_stage_boundaries_revised_without_overwriting_source(self):
        changed = [stage for stage in self.catalog["stages"] if stage["boundary_origin"] == "engineering_revision"]
        self.assertEqual(len(changed), 20)
        codes = {row["stage_code"] for row in changed}
        self.assertEqual(sum(card["stage_code"] in codes for card in self.catalog["cards"]), 97)
        for stage in changed:
            self.assertFalse(stage["source"]["start"])
            self.assertFalse(stage["source"]["end"])
            self.assertTrue(stage["effective"]["start"])
            self.assertTrue(stage["effective"]["end"])
            self.assertIn("never", stage["binding_policy"])
        self.assertIn("双腕", get_stage_rule("GS02-M07")["effective"]["start"])
        self.assertIn("单手", get_stage_rule("GS03-M07")["effective"]["start"])
        self.assertIn("移动", get_stage_rule("GS04-M02")["effective"]["definition"])

    def test_executable_conditions_are_typed_and_have_explicit_joint_dependencies(self):
        count = 0
        for card in self.catalog["cards"]:
            evaluation = card["evaluation"]
            self.assertIs(evaluation["source_grade_supported"], False)
            self.assertEqual(evaluation["grade_mapping"], GRADE_MAPPING)
            self.assertIn(evaluation["kind"], {"visual_proxy", "manual_review"})
            self.assertEqual(len({c["key"] for c in evaluation["conditions"]}), len(evaluation["conditions"]))
            if evaluation["kind"] == "visual_proxy":
                count += 1
                self.assertTrue(evaluation["conditions"])
                self.assertTrue(card["required_joint_names"])
            for condition in evaluation["conditions"]:
                spec = self.catalog["metrics"][condition["metric"]]
                self.assertEqual(condition["unit"], spec["unit"])
                self.assertTrue(condition["required_joint_names"])
                self.assertEqual(condition["measurement_semantics"], spec["definition"])
                self.assertIn(condition["operator"], {"gte", "lte", "eq", "between"})
                if condition["operator"] == "between":
                    self.assertEqual(len(condition["bounds"]), 2)
                    self.assertLessEqual(*condition["bounds"])
                else:
                    self.assertIsInstance(condition["value"], (int, float))
        self.assertGreater(count, 100)
        self.assertLess(count, 298)

    def test_missing_quality_is_not_zero_or_E_and_coverage_is_not_completion(self):
        policy = self.catalog["policy"]
        self.assertTrue(policy["contract_count_is_not_implementation_coverage"])
        self.assertTrue(policy["grade_policy"]["all_conditions_must_be_measured"])
        self.assertTrue(policy["grade_policy"]["unknown_condition_is_not_failed_condition"])
        self.assertIsNone(policy["aggregation"]["no_available_scores"])
        for card in self.catalog["cards"]:
            self.assertEqual(card["quality_policy"]["minimum_valid_fraction"], .7)
            self.assertIn("including_missing", card["quality_policy"]["denominator"])
            self.assertIn("not_E", card["quality_policy"]["failure_output"])
            self.assertEqual(card["validation"]["video_evidence"], [])
            self.assertFalse(card["validation"]["automatic_source_grade_implemented"])
        self.assertEqual(catalogue_coverage()["automatic_source_grade_claims"], 0)

    def test_minimal_geometric_dependencies(self):
        support = self.catalog["metrics"]["hip_inside_ankle_support_fraction.mean"]
        self.assertEqual(set(support["required_joint_names"]), {"left_hip", "right_hip", "left_ankle", "right_ankle"})
        wrist = self.catalog["metrics"]["wrist_separation_torso.median"]
        self.assertNotIn("left_elbow", wrist["required_joint_names"])
        head = self.catalog["metrics"]["head_relative_motion_range_torso"]
        self.assertNotIn("left_ear", head["required_joint_names"])

    def test_temporal_braking_has_joint_condition_and_nonnegative_successor(self):
        braking = get_card_rule("FS09-M03")["evaluation"]
        self.assertEqual(braking["engine_version"], "temporal-card-evidence-v1.0.1")
        self.assertIn("temporal_card_evidence", braking["engine"])
        self.assertIn("joint_braking_fraction", {c["metric"] for c in braking["conditions"]})
        for identifier in ("FS01-M05", "FS09-M05"):
            condition = next(c for c in get_card_rule(identifier)["evaluation"]["conditions"] if c["metric"] == "successor_interval_ms")
            self.assertEqual(condition["operator"], "between")
            self.assertEqual(condition["bounds"], [0, 500])

    def test_catalogue_is_reproducible_and_cannot_be_mutated_by_callers(self):
        spec = importlib.util.spec_from_file_location("card_catalog_builder", ROOT / "scripts/build_card_rule_catalog.py")
        builder = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(builder)
        self.assertEqual(builder.build_catalog(), self.catalog)
        mutated = load_card_rules()
        mutated["cards"][0]["evaluation"]["kind"] = "fake"
        self.assertNotEqual(load_card_rules()["cards"][0]["evaluation"]["kind"], "fake")
        with self.assertRaises(KeyError):
            get_card_rule("GS99-M99-99")
        self.assertEqual(self.catalog["version"], VERSION)


if __name__ == "__main__":
    unittest.main()
