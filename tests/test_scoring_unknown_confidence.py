"""Unknown observation confidence must never become an original A--E grade."""
from copy import deepcopy
import json
from pathlib import Path
import unittest

from rallymate_scoring.scoring import score_indicator


class UnknownConfidenceScoringTests(unittest.TestCase):
    def test_unknown_confidence_blocks_original_grade_with_or_without_calibration(self):
        feature = {"feature_name": "synthetic_feature", "feature_version": "test-only",
            "value": 4., "unit": "test_unit", "confidence": None, "valid": True,
            "reason": "valid", "source_frames": [1, 2, 3]}
        calibration = {"schema_version": "1.2.0", "backend": "threshold_rule",
            "indicator_id": "FS01-M02", "primary_feature": "synthetic_feature",
            "primary_feature_version": "test-only", "unit": "test_unit",
            "direction": "higher_is_better", "thresholds": [1., 2., 3., 4.],
            "threshold_version": "test-only-thresholds-v1", "artifact_scope": "test_only"}
        for supplied_calibration in (None, calibration):
            for missing_field in (False, True):
                with self.subTest(calibration=supplied_calibration, missing_field=missing_field):
                    item = deepcopy(feature)
                    if missing_field:
                        del item["confidence"]
                    result = score_indicator(indicator_id="FS01-M02", features=[item],
                        calibration=supplied_calibration, model_versions={}, evidence=[], allow_test_only=True)
                    self.assertEqual(result["status"], "unavailable")
                    self.assertIsNone(result["grade"])
                    self.assertIsNone(result["confidence"])
                    self.assertIsNone(result["threshold_version"])
                    self.assertIn("feature_confidence_unavailable", result["reason_codes"])
                    self.assertEqual(result["feature"]["items"], [item])
                    json.dumps(result, allow_nan=False)

    def test_null_confidence_is_declared_by_score_contract(self):
        schema = json.loads((Path(__file__).resolve().parents[1] / "contracts/score-result.schema.json").read_text(encoding="utf-8"))
        self.assertIn("null", schema["properties"]["confidence"]["type"])


if __name__ == "__main__":
    unittest.main()
