from __future__ import annotations

import copy
import json
import tempfile
import unittest
from pathlib import Path

from rallymate_scoring.multivideo_readiness import (
    build_multivideo_scoring_readiness_decomposition,
    load_latest_multivideo_scoring_readiness_decomposition,
    validate_multivideo_scoring_readiness_decomposition,
)


ROOT = Path(__file__).resolve().parents[1]
COVERAGE_LATEST = (
    ROOT / "reports" / "multivideo-indicator-calculation-coverage" / "latest.json"
)
LATEST = (
    ROOT
    / "reports"
    / "multivideo-scoring-readiness"
    / "latest.json"
)


class MultivideoScoringReadinessTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.audit = load_latest_multivideo_scoring_readiness_decomposition(LATEST)

    def test_real_decomposition_is_exhaustive_and_preserves_score_status(self) -> None:
        counts = self.audit["counts"]
        self.assertEqual(2366, counts["indicator_event_instances"])
        self.assertEqual(2297, counts["raw_measurement_vector_complete"])
        self.assertEqual(2291, counts["operational_feature_measured"])
        self.assertEqual(834, counts["ready_for_calibration_application"])
        self.assertEqual(
            {
                "measurement_hard_fail": 18,
                "measurement_vector_incomplete": 57,
                "scoring_context_incomplete": 180,
                "scoring_evidence_blocked": 1277,
                "calibration_only_missing": 834,
            },
            counts["exclusive_decomposition"],
        )
        self.assertEqual(
            {"calibration_required": 834, "unavailable": 1532},
            counts["score_status_counts"],
        )

    def test_all_three_videos_and_thirteen_indicators_are_decomposed(self) -> None:
        self.assertEqual(3, len(self.audit["videos"]))
        self.assertEqual(13, len(self.audit["indicators"]))
        self.assertEqual(
            self.audit["scope"]["indicator_ids"],
            [row["indicator_id"] for row in self.audit["indicators"]],
        )

    def test_typed_reasons_and_single_block_recovery_are_source_replayed(self) -> None:
        priority = self.audit["scoring_block_recovery_priority"]
        self.assertTrue(priority)
        self.assertEqual(
            "keypoint_jump_candidates_present", priority[0]["flag"]
        )
        self.assertEqual(
            645,
            priority[0][
                "sole_block_to_calibration_required_indicator_instance_count"
            ],
        )
        swap = next(
            row
            for row in priority
            if row["flag"] == "left_right_swap_candidates_present"
        )
        self.assertEqual(
            173,
            swap["sole_block_to_calibration_required_indicator_instance_count"],
        )
        self.assertTrue(
            self.audit["assertions"]["typed_reason_mapping_consistent"]
        )
        for row in self.audit["typed_reason_metrics"]:
            self.assertEqual(
                row["indicator_instance_count"],
                row["supported_indicator_instance_count"],
            )

    def test_typed_reason_or_recovery_claim_tampering_fails_closed(self) -> None:
        forged = copy.deepcopy(self.audit)
        forged["typed_reason_metrics"][0][
            "supported_indicator_instance_count"
        ] -= 1
        with self.assertRaisesRegex(ValueError, "typed score blocker"):
            validate_multivideo_scoring_readiness_decomposition(forged)

        forged = copy.deepcopy(self.audit)
        forged["assertions"]["recovery_counterfactual_changes_scores"] = True
        with self.assertRaisesRegex(ValueError, "unsafe"):
            validate_multivideo_scoring_readiness_decomposition(forged)
        self.assertEqual(
            2366,
            sum(
                row["counts"]["indicator_event_instances"]
                for row in self.audit["videos"]
            ),
        )

    def test_strong_validator_rejects_rebalanced_self_reported_counts(self) -> None:
        forged = copy.deepcopy(self.audit)
        forged["counts"]["exclusive_decomposition"][
            "measurement_vector_incomplete"
        ] -= 1
        forged["counts"]["exclusive_decomposition"][
            "calibration_only_missing"
        ] += 1
        validate_multivideo_scoring_readiness_decomposition(forged)
        with self.assertRaisesRegex(ValueError, "differs from replay"):
            validate_multivideo_scoring_readiness_decomposition(
                forged, verify_sources=True
            )

    def test_accuracy_gate_or_zero_fill_claim_fails_closed(self) -> None:
        for field in (
            "missing_values_zero_filled",
            "candidate_events_are_ground_truth",
            "feature_accuracy_claim",
            "ready_for_calibration_means_scored",
        ):
            with self.subTest(field=field):
                forged = copy.deepcopy(self.audit)
                forged["safety"][field] = True
                with self.assertRaisesRegex(ValueError, "unsafe"):
                    validate_multivideo_scoring_readiness_decomposition(forged)

    def test_build_is_immutable_and_latest_is_strongly_loadable(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "readiness"
            built = build_multivideo_scoring_readiness_decomposition(
                coverage_latest_path=COVERAGE_LATEST,
                output_root=output,
                audit_id="test-audit",
                generated_at="2026-08-22T00:00:00+00:00",
            )
            loaded = load_latest_multivideo_scoring_readiness_decomposition(
                output / "latest.json"
            )
            self.assertEqual(built, loaded)
            with self.assertRaises(FileExistsError):
                build_multivideo_scoring_readiness_decomposition(
                    coverage_latest_path=COVERAGE_LATEST,
                    output_root=output,
                    audit_id="test-audit",
                )

    def test_machine_schema_accepts_real_audit(self) -> None:
        try:
            import jsonschema
        except ImportError:
            self.skipTest("jsonschema unavailable")
        schema = json.loads(
            (
                ROOT
                / "contracts"
                / "multivideo-scoring-readiness-decomposition.schema.json"
            ).read_text(encoding="utf-8")
        )
        jsonschema.Draft202012Validator(schema).validate(self.audit)


if __name__ == "__main__":
    unittest.main()
