"""Observation confidence stays separate from an explicit mathematical grade."""
from copy import deepcopy
import json
from pathlib import Path
import unittest

from rallymate_scoring.scoring import score_indicator


class UnknownConfidenceScoringTests(unittest.TestCase):
    def setUp(self):
        self.feature = {"feature_name": "synthetic_feature", "feature_version": "test-only",
            "value": 4., "unit": "test_unit", "confidence": None, "valid": True,
            "reason": "valid", "source_frames": [1, 2, 3]}
        self.calibration = {"schema_version": "1.2.0", "backend": "threshold_rule",
            "indicator_id": "FS01-M02", "primary_feature": "synthetic_feature",
            "primary_feature_version": "test-only", "unit": "test_unit",
            "direction": "higher_is_better", "thresholds": [1., 2., 3., 4.],
            "threshold_version": "test-only-thresholds-v1", "artifact_scope": "test_only"}

    def evaluate(self, item, calibration):
        return score_indicator(indicator_id="FS01-M02", features=[item],
            calibration=calibration, model_versions={}, evidence=[], allow_test_only=True)

    def test_confidence_does_not_change_explicit_threshold_grade(self):
        # These values exercise real threshold transitions, including a valid
        # zero feature. Confidence neither multiplies nor vetoes the result.
        for value, expected_grade in ((0., "E"), (2., "C"), (4., "A")):
            for confidence in (None, 0., .2, 1., "omitted"):
                with self.subTest(value=value, confidence=confidence):
                    item = {**self.feature, "value": value, "confidence": confidence}
                    if confidence == "omitted":
                        del item["confidence"]
                    result = self.evaluate(item, self.calibration)
                    self.assertEqual(result["status"], "scored")
                    self.assertEqual(result["assessment_status"], "graded")
                    self.assertEqual(result["grade"], expected_grade)
                    self.assertEqual(result["confidence"], None if confidence == "omitted" else confidence)
                    self.assertEqual(result["threshold_version"], self.calibration["threshold_version"])
                    self.assertEqual(result["feature"], item)
                    self.assertEqual(result["grading_basis"], "supplied_mathematical_rule")
                    self.assertFalse(result["accuracy_validated"])
                    json.dumps(result, allow_nan=False)

    def test_missing_rule_keeps_measurement_without_grade_for_known_or_unknown_confidence(self):
        for confidence in (None, .9, "omitted"):
            with self.subTest(confidence=confidence):
                item = {**self.feature, "confidence": confidence}
                if confidence == "omitted":
                    del item["confidence"]
                result = self.evaluate(item, None)
                self.assertEqual(result["status"], "calibration_required")
                self.assertEqual(result["assessment_status"], "measurement_only")
                self.assertIsNone(result["grade"])
                self.assertEqual(result["confidence"], None if confidence == "omitted" else confidence)
                self.assertIsNone(result["threshold_version"])
                self.assertEqual(result["reason_codes"], ["grade_rule_not_defined"])
                self.assertEqual(result["feature"]["items"], [item])
                json.dumps(result, allow_nan=False)

    def test_confidence_cannot_make_an_invalid_feature_scorable(self):
        for confidence in (None, 0., 1.):
            for invalid in ({"valid": False}, {"value": None}, {"value": True}):
                with self.subTest(confidence=confidence, invalid=invalid):
                    item = {**self.feature, "confidence": confidence, **invalid}
                    result = self.evaluate(item, self.calibration)
                    self.assertEqual(result["status"], "unavailable")
                    self.assertIsNone(result["grade"])
                    self.assertIn("required_feature_unavailable", result["reason_codes"])
                    self.assertEqual(result["feature"]["items"], [item])

    def test_unknown_confidence_also_preserves_ordinal_rule_result(self):
        definition = {"backend": "ordinal_regression", "indicator_id": "FS01-M02",
            "model_version": "test-only-ordinal-v1", "feature_order": ["synthetic_feature"],
            "unit_by_feature": {"synthetic_feature": "test_unit"},
            "feature_version_by_feature": {"synthetic_feature": "test-only"},
            "coefficients": [1.], "cutpoints": [-2., -1., 1., 2.]}
        known = self.evaluate({**self.feature, "confidence": .9}, definition)
        for missing_field in (False, True):
            item = deepcopy(self.feature)
            if missing_field:
                del item["confidence"]
            result = self.evaluate(item, definition)
            self.assertEqual(result["status"], "scored")
            self.assertEqual(result["grade"], known["grade"])
            self.assertEqual(result["feature"]["grade_probabilities"], known["feature"]["grade_probabilities"])
            self.assertIsNone(result["confidence"])

    def test_null_confidence_is_declared_by_score_contract(self):
        schema = json.loads((Path(__file__).resolve().parents[1] / "contracts/score-result.schema.json").read_text(encoding="utf-8"))
        self.assertIn("null", schema["properties"]["confidence"]["type"])


if __name__ == "__main__":
    unittest.main()
