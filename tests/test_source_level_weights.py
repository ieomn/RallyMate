"""Source-table regression for the five requested assessment tiers."""
import json
from pathlib import Path
import unittest

from rallymate_scoring.level_grading import grade_level


ROOT = Path(__file__).resolve().parents[1]
SOURCE = json.loads((ROOT / "tests/fixtures/source-level-weights.json").read_text(encoding="utf-8"))
POLICY = json.loads((ROOT / "src/rallymate_scoring/data/level_grading_v1.json").read_text(encoding="utf-8"))


class SourceLevelWeightsTests(unittest.TestCase):
    def test_all_45_weight_tables_match_the_original_document(self):
        self.assertEqual(len(SOURCE["rows"]), 45)
        self.assertEqual(POLICY["source_document"]["sha256"], SOURCE["sha256"])
        self.assertEqual(POLICY["source_document"]["filename"], SOURCE["source_document"])
        self.assertEqual(POLICY["source_document"]["table_index_base"], 1)
        for row in SOURCE["rows"]:
            with self.subTest(level=row["level"], table=row["table"]):
                level = str(row["level"])
                is_major = row["kind"] == "module"
                field = "module_weights" if is_major else row["kind"]
                self.assertEqual(POLICY["source_document"]["tables"][level][field], row["table"])
                actual = POLICY["levels"][level]["module_weights"] if is_major else POLICY["levels"][level]["technique_weights"][field]
                expected = dict(zip(row["codes"], [v / 100 if is_major else v for v in row["percent"]]))
                self.assertEqual(actual, expected)

    def test_lv1_slices_are_scored_and_braking_is_outside_the_tier(self):
        result = grade_level({"GS01": 100, "GS02": 0, "GS03": 100, "GS04": 50, "GS05": 50, "FS09": 100})
        self.assertEqual(result["domains"]["GS"]["score"], 55)
        self.assertEqual(result["overall_score"], 55)
        self.assertEqual(result["weighted_coverage"], .7)
        self.assertNotIn("FS09", result["domains"]["FS"]["active_weights"])
        self.assertIsNone(result["domains"]["FS"]["score"])
        self.assertEqual(result["policy_version"], "level-grading-policy-v2.0.0")

    def test_lv2_source_zero_weights_do_not_become_scores_or_missing_requirements(self):
        result = grade_level({"SS02": 100, "SS03": 100, "NS03": 100, "DS03": 100, "FS09": 100}, target_level=2)
        self.assertIsNone(result["overall_score"])
        for domain, code in [("SS", "SS02"), ("SS", "SS03"), ("NS", "NS03"), ("DS", "DS03"), ("FS", "FS09")]:
            self.assertNotIn(code, result["domains"][domain]["active_weights"])
            self.assertNotIn(code, result["domains"][domain]["missing_techniques"])

    def test_document_raw_percentages_are_normalized_after_backhand_selection(self):
        # Source tables 32 and 38 sum to 75 after selecting one backhand.
        # Keeping their raw values avoids inventing an unprovided 25 percent.
        for style, backhand in [("one_handed", "GS03"), ("two_handed", "GS02")]:
            result = grade_level({"GS01": 100, backhand: 0, "GS04": 0, "GS05": 0}, target_level=3, backhand_style=style)
            self.assertEqual(result["overall_score"], 40)
            self.assertEqual(result["domains"]["GS"]["active_weights"]["GS01"], .4)
            self.assertAlmostEqual(sum(result["domains"]["GS"]["active_weights"].values()), 1)

    def test_fifth_tier_preserves_real_zero_and_missing_measurements(self):
        complete = grade_level({"SS01": 100, "SS02": 0, "SS03": 0, "SS04": 0, "SS05": 0}, target_level=5)
        partial = grade_level({"SS01": 100, "SS02": None}, target_level=5)
        self.assertEqual(complete["overall_score"], 20)
        self.assertEqual(complete["domains"]["SS"]["coverage"], 1)
        self.assertEqual(partial["overall_score"], 100)
        self.assertEqual(partial["domains"]["SS"]["coverage"], .2)


if __name__ == "__main__":
    unittest.main()
