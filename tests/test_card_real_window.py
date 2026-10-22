from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
import unittest

from rallymate_scoring.card_evaluation import evaluate_card_window
from rallymate_scoring.card_rule_catalog import get_card_rule


class RealCardWindowTests(unittest.TestCase):
    def test_public_video_measured_values_and_missing_pose_stay_distinct(self):
        path = Path(__file__).parent / "fixtures/card-real-window.json"
        cases = json.loads(path.read_text(encoding="utf-8"))["cases"]
        for case in cases:
            with self.subTest(indicator=case["indicator_id"]):
                actual = evaluate_card_window(get_card_rule(case["indicator_id"]),
                    [SimpleNamespace(**s) for s in case["samples"]], case["window"], case["timeline"])
                expected = case["expected"]
                self.assertEqual(actual["status"], {"proxy_scored": "scored", "review_required": "unavailable"}.get(
                    expected["status"], expected["status"]))
                self.assertEqual(actual["proxy_grade"], expected["proxy_grade"])
                self.assertEqual(actual["score"], expected["score"])
                self.assertIsNone(actual["original_grade"])
                self.assertFalse(actual["quality"]["visibility_verified"])
                self.assertEqual(actual["quality"]["total_frame_count"], len(case["timeline"]))
                self.assertEqual(actual["quality"]["valid_frame_count"], expected["quality"]["valid_frame_count"])
                for condition, reference in zip(actual["conditions"], expected["conditions"]):
                    self.assertEqual(condition["metric"], reference["metric"])
                    self.assertEqual(condition["passed"], reference["passed"] if actual["status"] == "scored" else None)
                    if reference["value"] is None:
                        self.assertIsNone(condition["value"])
                    else:
                        self.assertAlmostEqual(condition["value"], reference["value"], places=6)
        scored, missing = cases
        self.assertEqual(scored["expected"]["status"], "proxy_scored")
        self.assertEqual(scored["expected"]["proxy_grade"], "A")
        self.assertEqual(missing["expected"]["status"], "unavailable")
        self.assertIsNone(missing["expected"]["score"])


if __name__ == "__main__":
    unittest.main()
