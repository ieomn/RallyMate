from __future__ import annotations

import json
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class ExtensibleScoringContractTests(unittest.TestCase):
    def test_indicator_feature_contract_is_registry_driven_not_six_id_enum(self) -> None:
        schema = json.loads(
            (ROOT / "contracts" / "indicator-feature.schema.json").read_text(
                encoding="utf-8"
            )
        )
        indicator = schema["properties"]["indicator_id"]
        self.assertNotIn("enum", indicator)
        self.assertIn("pattern", indicator)
        self.assertIn("quality_gate", schema["properties"])

    def test_real_pose_wave_output_declares_all_registry_indicators(self) -> None:
        summary_path = (
            ROOT
            / "reports"
            / "fs09-pose-wave-v2"
            / "850cb0006b406c7176eeda8d711cd065"
            / "scoring-loop-summary.json"
        )
        if not summary_path.exists():
            self.skipTest("real pose-wave smoke artifact has not been generated")
        summary = json.loads(summary_path.read_text(encoding="utf-8"))
        registry = json.loads(
            (ROOT / "metric-feasibility-pose-wave-v2.json").read_text(
                encoding="utf-8"
            )
        )
        expected = {item["indicator_id"] for item in registry["indicators"]}
        self.assertEqual(set(summary["indicator_feature_validity"]), expected)
        self.assertEqual(
            summary["result_state"]["target_indicator_count"], len(expected)
        )
        self.assertFalse(summary["grade_counts"])
        self.assertFalse(
            summary["safety_assertions"]["any_non_null_grade_without_calibration"]
        )


if __name__ == "__main__":
    unittest.main()
