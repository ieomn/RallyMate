"""Numerical/contract acceptance, not recognition or coaching accuracy."""
from __future__ import annotations

import copy
import unittest

from rallymate_scoring.scoring import score_indicator
from rallymate_scoring.indicator_assessment import build_indicator_assessment
from rallymate_features.coordinates import pose_sequence_from_records
from rallymate_features.schemas import model_geometry_evidence
from tests.test_sam_footwork import sam_motion_fixture


def rule():
    return {"backend": "threshold_rule", "indicator_id": "FS01-M02", "primary_feature": "value",
            "primary_feature_version": "formula-1", "unit": "deg", "threshold_version": "example-math-1",
            "direction": "higher_is_better", "thresholds": [1, 2, 3, 4]}


def feature(value=3):
    return {"feature_name": "value", "feature_version": "formula-1", "unit": "deg",
            "valid": True, "value": value, "confidence": .8, "source_frames": [0, 3]}


def evaluate(item=None, calibration=None, flags=(), evidence=None):
    return score_indicator(indicator_id="FS01-M02", features=[item or feature()],
        calibration=calibration, model_versions={}, evidence=evidence or [],
        feasibility_level="F2", event_quality_flags=flags)


class DirectScoringRulesTests(unittest.TestCase):
    def test_explicit_rule_boundary_equality_and_both_directions_without_approval(self):
        for direction, expected in (("higher_is_better", "EDCBA"), ("lower_is_better", "ABCDE")):
            definition = {**rule(), "direction": direction}
            for value, grade in zip((.99, 1, 2, 3, 4), expected):
                with self.subTest(direction=direction, value=value):
                    result = evaluate(feature(value), definition)
                    self.assertEqual((result["status"], result["grade"]), ("scored", grade))
                    self.assertNotIn("trusted_promotion_ledger", result["model_versions"])

    def test_metadata_does_not_become_a_computational_veto(self):
        definition = {**rule(), "artifact_scope": "test_only", "independent_test": {
            "status": "pending", "approved_for_scoring": False}}
        self.assertEqual(evaluate(calibration=definition)["grade"], "B")
        for level in ("F0", "F1", "F2", "F3", "F4", None):
            self.assertEqual(score_indicator(indicator_id="FS01-M02", features=[feature()],
                calibration=definition, model_versions={}, evidence=[], feasibility_level=level)["grade"], "B")

    def test_missing_mathematical_rule_keeps_real_measurement_without_grade(self):
        result = evaluate()
        self.assertEqual(result["assessment_status"], "measurement_only")
        self.assertEqual(result["reason_codes"], ["grade_rule_not_defined"])
        self.assertIsNone(result["grade"])
        self.assertTrue(result["quality_gate"]["measurement_allowed"])

    def test_missing_phase_and_ambiguous_identity_still_block(self):
        for flags in (["required_phase_missing:landing_proxy_ms"], ["primary_identity_ambiguous"]):
            result = evaluate(calibration=rule(), flags=flags)
            self.assertEqual(result["status"], "unavailable")
            self.assertIsNone(result["grade"])

    def test_candidate_diagnostics_remain_visible_without_human_validation_gate(self):
        result = evaluate(calibration=rule(), flags=["keypoint_jump_candidates_present", "primary_pose_coverage_low"])
        self.assertEqual(result["status"], "scored")
        self.assertIn("keypoint_jump_candidates_present", result["quality_gate"]["advisory_flags"])

    def test_nonfinite_boolean_and_missing_features_do_not_grade(self):
        for value in (float("nan"), float("inf"), True, None, 10**1000):
            self.assertEqual(evaluate(feature(value), rule())["status"], "unavailable")
        self.assertEqual(evaluate({**feature(), "valid": False}, rule())["status"], "unavailable")

    def test_missing_cutpoints_invalid_units_and_wrong_indicator_are_rejected(self):
        for change in ({"thresholds": [1, 2, 2, 4]}, {"thresholds": [1, 2, float("inf"), 4]},
                       {"thresholds": None}, {"unit": "ms"}, {"primary_feature_version": "different"},
                       {"indicator_id": "FS02-M01"}):
            with self.subTest(change=change), self.assertRaises(ValueError):
                evaluate(calibration={**rule(), **change})

    def test_source_bound_sam_can_evaluate_math_without_inventing_confidence(self):
        records, timeline = sam_motion_fixture()
        proof = model_geometry_evidence(pose_sequence_from_records(records, timeline), range(4))
        item = {**feature(), **proof, "confidence": None}
        result = evaluate(item, rule())
        self.assertEqual(result["grade"], "B")
        self.assertIsNone(result["confidence"])
        self.assertFalse(result["measurement_provenance"]["visibility_verified"])
        self.assertEqual(result["measurement_provenance"]["assessment_status"], "graded")
        broken = copy.deepcopy(item)
        broken["source_window"]["selection_epoch"] += 1
        self.assertEqual(evaluate(broken, rule())["status"], "unavailable")
        self.assertEqual(evaluate(item, rule(), evidence=[{"video_sha256": "b"*64}])["status"], "unavailable")
        empty = copy.deepcopy(item)
        empty["geometry_mask"] = {name: [False] * 4 for name in empty["geometry_mask"]}
        self.assertEqual(evaluate(empty, rule())["status"], "unavailable")
        other = copy.deepcopy(item)
        other["feature_name"] = "another_feature"
        other["source_window"]["selection_epoch"] += 1
        for binding in other["source_window"]["source_bindings"]:
            binding["selection_epoch"] += 1
        mixed = score_indicator(indicator_id="FS01-M02", features=[item, other],
            calibration=rule(), model_versions={}, evidence=[])
        self.assertIn("model_source_identity_mismatch", mixed["reason_codes"])

    def test_ordinal_extremes_do_not_overflow(self):
        definition = {"backend": "ordinal_regression", "indicator_id": "FS01-M02", "model_version": "math-1",
            "feature_order": ["value"], "unit_by_feature": {"value": "deg"},
            "feature_version_by_feature": {"value": "formula-1"}, "coefficients": [1], "cutpoints": [-2,-1,1,2]}
        self.assertEqual(evaluate(feature(1e300), definition)["grade"], "A")
        self.assertEqual(evaluate(feature(-1e300), definition)["grade"], "E")

    def test_proxy_count_recomputes_explicit_rules_without_claiming_complete_source_grade(self):
        trace = {"video_id": "video", "event_id": "event", "person_track_id": 1, "source_frames": [0,3]}
        score = evaluate(calibration=rule(), evidence=[trace])
        record = {**trace, "event_code": "FS01", "indicator_id": "FS01-M02", "features": [feature()],
            "scoring_status": "scored", "grade": "B", "score_result": score, "quality_gate": score["quality_gate"],
            "feasibility_level": "F2"}
        result = build_indicator_assessment({"job_id": "video"}, [record])
        self.assertEqual(result["counts"]["formal_graded"], 0)
        self.assertEqual(result["counts"]["proxy_graded"], 1)
        card = next(r for r in result["indicators"] if r["indicator_id"] == "FS01-M02")
        self.assertEqual(card["grade"]["status"], "proxy_scored")
        record["grade"] = "A"
        self.assertEqual(build_indicator_assessment({"job_id": "video"}, [record])["counts"]["proxy_graded"], 0)
        record["grade"] = "B"
        del record["score_result"]["grade_rule"]
        self.assertEqual(build_indicator_assessment({"job_id": "video"}, [record])["counts"]["proxy_graded"], 0)


if __name__ == "__main__":
    unittest.main()
