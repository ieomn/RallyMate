from __future__ import annotations

import unittest
from pathlib import Path

from rallymate_scoring.feasibility import (
    TARGET_INDICATORS,
    assess_feasibility_promotion,
    load_feasibility_registry,
)


ROOT = Path(__file__).resolve().parents[1]


class FeasibilityRegistryTests(unittest.TestCase):
    def test_six_target_indicators_have_f0_to_f4_contract_without_thresholds(self) -> None:
        registry = load_feasibility_registry(ROOT / "metric-feasibility.json")
        self.assertEqual(
            {item["indicator_id"] for item in registry["indicators"]},
            TARGET_INDICATORS,
        )
        self.assertTrue(
            all(item["feasibility_level"] == "F2" for item in registry["indicators"])
        )
        self.assertTrue(
            all(
                all(value is None for value in item["acceptance_metrics"].values())
                for item in registry["indicators"]
            )
        )
        self.assertTrue(
            all(item["ground_truth_requirements"] for item in registry["indicators"])
        )

    def test_F2_cannot_skip_directly_to_F4(self) -> None:
        indicator = load_feasibility_registry(
            ROOT / "metric-feasibility.json"
        )["indicators"][0]
        result = assess_feasibility_promotion(
            indicator,
            target_level="F4",
            evidence={"evidence_version": "synthetic-test"},
        )
        self.assertFalse(result["allowed"])
        self.assertEqual(
            result["blockers"], ["promotion_must_advance_exactly_one_level"]
        )

    def test_F3_requires_truth_calibration_and_review_evidence(self) -> None:
        indicator = load_feasibility_registry(
            ROOT / "metric-feasibility.json"
        )["indicators"][0]
        incomplete = assess_feasibility_promotion(
            indicator,
            target_level="F3",
            evidence={"evidence_version": "synthetic-test"},
        )
        self.assertFalse(incomplete["allowed"])
        complete_evidence = {"evidence_version": "synthetic-test"}
        for field, status in (
            ("event_evaluation", "evaluated"),
            ("feature_evaluation", "evaluated"),
            ("grade_separation", "passed"),
            ("coach_agreement", "evaluated"),
            ("internal_calibration", "validated"),
        ):
            complete_evidence[field] = {
                "status": status,
                "report_version": f"synthetic-{field}-v1",
            }
        complete_evidence["acceptance_review"] = {
            "decision": "passed",
            "protocol_version": "synthetic-F3-protocol-v1",
            "reviewer_id": "synthetic-reviewer",
        }
        complete = assess_feasibility_promotion(
            indicator,
            target_level="F3",
            evidence=complete_evidence,
        )
        self.assertTrue(complete["allowed"], complete["blockers"])

    def test_F4_requires_traceable_independent_test_approval(self) -> None:
        indicator = {
            **load_feasibility_registry(ROOT / "metric-feasibility.json")[
                "indicators"
            ][0],
            "feasibility_level": "F3",
        }
        evidence = {
            "evidence_version": "synthetic-test",
            "calibration_version": "synthetic-calibration-v1",
            "test_split_manifest_version": "synthetic-split-v1",
            "independent_test": {
                "status": "passed",
                "approved_for_scoring": True,
                "dataset_version": "synthetic-independent-v1",
                "report_version": "synthetic-report-v1",
                "report_sha256": "a" * 64,
                "acceptance_protocol_version": "synthetic-protocol-v1",
                "evaluated_at": "2026-08-13T00:00:00Z",
            },
        }
        result = assess_feasibility_promotion(
            indicator, target_level="F4", evidence=evidence
        )
        self.assertTrue(result["allowed"], result["blockers"])


if __name__ == "__main__":
    unittest.main()
