from __future__ import annotations

import unittest

from rallymate_scoring.calibration import (
    compute_annotator_agreement,
    validate_ordinal_model,
    validate_threshold_calibration,
)
from rallymate_scoring.scoring import score_indicator


def _feature(value: float = 2.5, valid: bool = True) -> dict:
    return {
        "feature_name": "synthetic_feature",
        "feature_version": "test-only",
        "value": value if valid else None,
        "unit": "test_unit",
        "confidence": 0.8,
        "valid": valid,
        "reason": "valid" if valid else "missing",
        "source_frames": [10],
    }


VERSIONS = {
    "pose": "test-pose",
    "primary_player": "test-primary",
    "event": "test-event",
    "feature": "test-feature",
}


def _test_only_evidence() -> dict:
    return {
        "status": "test_only",
        "approved_for_scoring": False,
        "dataset_version": None,
        "report_version": None,
        "report_sha256": None,
        "acceptance_protocol_version": None,
        "evaluated_at": None,
    }


def _production_evidence(status: str) -> dict:
    passed = status == "passed"
    return {
        "status": status,
        "approved_for_scoring": passed,
        "dataset_version": "synthetic-independent-test-v1" if passed else None,
        "report_version": "synthetic-report-v1" if passed else None,
        "report_sha256": "a" * 64 if passed else None,
        "acceptance_protocol_version": "synthetic-protocol-v1" if passed else None,
        "evaluated_at": "2026-08-13T00:00:00Z" if passed else None,
    }


def _threshold_calibration(*, scope: str = "test_only", test_status: str = "test_only") -> dict:
    return {
        "schema_version": "1.2.0",
        "backend": "threshold_rule",
        "threshold_version": "test-only-thresholds-v1",
        "indicator_id": "FS01-M02",
        "primary_feature": "synthetic_feature",
        "primary_feature_version": "test-only",
        "unit": "test_unit",
        "direction": "higher_is_better",
        "thresholds": [1.0, 2.0, 3.0, 4.0],
        "source": "coach_ground_truth_calibration",
        "ground_truth_dataset_version": "synthetic-unit-test-only",
        "artifact_scope": scope,
        "independent_test": (
            _test_only_evidence()
            if scope == "test_only"
            else _production_evidence(test_status)
        ),
        "feedback_by_grade": {"C": "test feedback"},
    }


class CalibrationTests(unittest.TestCase):
    def test_missing_calibration_never_generates_grade(self) -> None:
        result = score_indicator(
            indicator_id="FS01-M02",
            features=[_feature()],
            calibration=None,
            model_versions=VERSIONS,
            evidence=[{"source_frame": 10}],
        )
        self.assertEqual(result["status"], "calibration_required")
        self.assertIsNone(result["grade"])
        self.assertIsNone(result["threshold_version"])

    def test_invalid_feature_returns_unavailable_before_calibration(self) -> None:
        result = score_indicator(
            indicator_id="FS01-M02",
            features=[_feature(valid=False)],
            calibration=None,
            model_versions=VERSIONS,
            evidence=[],
            feasibility_level="F4",
        )
        self.assertEqual(result["status"], "unavailable")
        self.assertIsNone(result["grade"])

    def test_explicit_test_only_coach_calibration_can_score_threshold_backend(self) -> None:
        calibration = _threshold_calibration()
        result = score_indicator(
            indicator_id="FS01-M02",
            features=[_feature(2.5)],
            calibration=calibration,
            model_versions=VERSIONS,
            evidence=[],
            allow_test_only=True,
        )
        self.assertEqual(result["status"], "scored")
        self.assertEqual(result["grade"], "C")
        self.assertEqual(result["threshold_version"], "test-only-thresholds-v1")

    def test_test_only_calibration_cannot_score_without_explicit_override(self) -> None:
        result = score_indicator(
            indicator_id="FS01-M02",
            features=[_feature(2.5)],
            calibration=_threshold_calibration(),
            model_versions=VERSIONS,
            evidence=[],
            feasibility_level="F4",
        )
        self.assertEqual(result["status"], "calibration_required")
        self.assertIsNone(result["grade"])
        self.assertIn("test_only_calibration_not_allowed", result["reason_codes"])

    def test_pending_independent_test_cannot_score(self) -> None:
        result = score_indicator(
            indicator_id="FS01-M02",
            features=[_feature(2.5)],
            calibration=_threshold_calibration(
                scope="production", test_status="pending"
            ),
            model_versions=VERSIONS,
            evidence=[],
            feasibility_level="F4",
        )
        self.assertEqual(result["status"], "calibration_required")
        self.assertIsNone(result["grade"])
        self.assertIn("independent_test_not_passed", result["reason_codes"])

    def test_self_declared_production_calibration_cannot_score_without_ledger(self) -> None:
        result = score_indicator(
            indicator_id="FS01-M02",
            features=[_feature(2.5)],
            calibration=_threshold_calibration(
                scope="production", test_status="passed"
            ),
            model_versions=VERSIONS,
            evidence=[],
            feasibility_level="F4",
        )
        self.assertEqual(result["status"], "calibration_required")
        self.assertIsNone(result["grade"])
        self.assertIn(
            "trusted_promotion_ledger_verification_required",
            result["reason_codes"],
        )

    def test_independently_approved_asset_cannot_skip_F2_to_scored(self) -> None:
        result = score_indicator(
            indicator_id="FS01-M02",
            features=[_feature(2.5)],
            calibration=_threshold_calibration(
                scope="production", test_status="passed"
            ),
            model_versions=VERSIONS,
            evidence=[],
            feasibility_level="F2",
        )
        self.assertEqual(result["status"], "calibration_required")
        self.assertIsNone(result["grade"])
        self.assertIn("feasibility_F4_required", result["reason_codes"])

    def test_thresholds_must_be_strictly_increasing(self) -> None:
        calibration = _threshold_calibration()
        calibration["threshold_version"] = "bad"
        calibration["thresholds"] = [1.0, 3.0, 2.0, 4.0]
        with self.assertRaisesRegex(ValueError, "strictly increasing"):
            validate_threshold_calibration(calibration)

    def test_threshold_asset_is_bound_to_feature_version(self) -> None:
        calibration = _threshold_calibration()
        calibration["primary_feature_version"] = "different-feature-contract"
        with self.assertRaisesRegex(ValueError, "primary_feature_version"):
            score_indicator(
                indicator_id="FS01-M02",
                features=[_feature(2.5)],
                calibration=calibration,
                model_versions=VERSIONS,
                evidence=[],
                allow_test_only=True,
            )

    def test_boolean_threshold_is_not_accepted_as_number(self) -> None:
        calibration = _threshold_calibration()
        calibration["thresholds"] = [True, 2.0, 3.0, 4.0]
        with self.assertRaisesRegex(ValueError, "finite numbers"):
            validate_threshold_calibration(calibration)

    def test_passed_independent_test_requires_valid_report_hash(self) -> None:
        calibration = _threshold_calibration(
            scope="production", test_status="passed"
        )
        calibration["independent_test"]["report_sha256"] = "not-a-sha"
        with self.assertRaisesRegex(ValueError, "report_sha256"):
            validate_threshold_calibration(calibration)

    def test_ordinal_backend_outputs_probability_backed_grade(self) -> None:
        calibration = {
            "schema_version": "1.2.0",
            "backend": "ordinal_regression",
            "model_version": "test-only-ordinal-v1",
            "indicator_id": "FS01-M02",
            "feature_order": ["synthetic_feature"],
            "unit_by_feature": {"synthetic_feature": "test_unit"},
            "feature_version_by_feature": {"synthetic_feature": "test-only"},
            "coefficients": [1.0],
            "intercept": 0.0,
            "cutpoints": [-2.0, -1.0, 0.0, 1.0],
            "source": "coach_ground_truth_calibration",
            "ground_truth_dataset_version": "synthetic-unit-test-only",
            "artifact_scope": "test_only",
            "independent_test": _test_only_evidence(),
        }
        result = score_indicator(
            indicator_id="FS01-M02",
            features=[_feature(3.0)],
            calibration=calibration,
            model_versions=VERSIONS,
            evidence=[],
            allow_test_only=True,
        )
        self.assertEqual(result["status"], "scored")
        self.assertIn(result["grade"], {"A", "B", "C", "D", "E"})
        self.assertEqual(
            set(result["feature"]["grade_probabilities"]),
            {"A", "B", "C", "D", "E"},
        )

    def test_ordinal_model_rejects_non_finite_coefficients(self) -> None:
        calibration = {
            "schema_version": "1.2.0",
            "backend": "ordinal_regression",
            "model_version": "test-only-ordinal-v1",
            "indicator_id": "FS01-M02",
            "feature_order": ["synthetic_feature"],
            "unit_by_feature": {"synthetic_feature": "test_unit"},
            "feature_version_by_feature": {"synthetic_feature": "test-only"},
            "coefficients": [float("nan")],
            "intercept": 0.0,
            "cutpoints": [-2.0, -1.0, 0.0, 1.0],
            "source": "coach_ground_truth_calibration",
            "ground_truth_dataset_version": "synthetic-unit-test-only",
            "artifact_scope": "test_only",
            "independent_test": _test_only_evidence(),
        }
        with self.assertRaisesRegex(ValueError, "finite numbers"):
            validate_ordinal_model(calibration)

    def test_ordinal_asset_is_bound_to_feature_unit_and_version(self) -> None:
        calibration = {
            "schema_version": "1.2.0",
            "backend": "ordinal_regression",
            "model_version": "test-only-ordinal-v1",
            "indicator_id": "FS01-M02",
            "feature_order": ["synthetic_feature"],
            "unit_by_feature": {"synthetic_feature": "wrong_unit"},
            "feature_version_by_feature": {"synthetic_feature": "test-only"},
            "coefficients": [1.0],
            "intercept": 0.0,
            "cutpoints": [-2.0, -1.0, 0.0, 1.0],
            "source": "coach_ground_truth_calibration",
            "ground_truth_dataset_version": "synthetic-unit-test-only",
            "artifact_scope": "test_only",
            "independent_test": _test_only_evidence(),
        }
        with self.assertRaisesRegex(ValueError, "unit does not match"):
            score_indicator(
                indicator_id="FS01-M02",
                features=[_feature(2.5)],
                calibration=calibration,
                model_versions=VERSIONS,
                evidence=[],
                allow_test_only=True,
            )
        calibration["unit_by_feature"]["synthetic_feature"] = "test_unit"
        calibration["feature_version_by_feature"]["synthetic_feature"] = "wrong"
        with self.assertRaisesRegex(ValueError, "feature version"):
            score_indicator(
                indicator_id="FS01-M02",
                features=[_feature(2.5)],
                calibration=calibration,
                model_versions=VERSIONS,
                evidence=[],
                allow_test_only=True,
            )

    def test_multi_coach_agreement_supports_grades_and_rankings(self) -> None:
        labels = []
        for annotator in ("coach-1", "coach-2"):
            for event_id, grade, rank in (("event-1", "A", 1), ("event-2", "C", 2)):
                labels.append(
                    {
                        "schema_version": "1.0.0",
                        "annotation_id": f"{annotator}-{event_id}-grade",
                        "video_id": "video",
                        "event_id": event_id,
                        "indicator_id": "FS01-M02",
                        "annotator_id": annotator,
                        "label_type": "grade",
                        "grade": grade,
                    }
                )
                labels.append(
                    {
                        "schema_version": "1.0.0",
                        "annotation_id": f"{annotator}-{event_id}-rank",
                        "video_id": "video",
                        "event_id": event_id,
                        "indicator_id": "FS01-M02",
                        "annotator_id": annotator,
                        "label_type": "ranking",
                        "rank_group_id": "group-1",
                        "rank": rank,
                    }
                )
        result = compute_annotator_agreement(labels)
        self.assertEqual(result["status"], "evaluated")
        self.assertEqual(
            result["grade_agreement"]["mean_quadratic_weighted_kappa"], 1.0
        )
        self.assertEqual(result["ranking_agreement"]["mean_kendall_tau"], 1.0)


if __name__ == "__main__":
    unittest.main()
