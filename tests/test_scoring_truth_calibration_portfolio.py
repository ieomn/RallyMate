from __future__ import annotations

import copy
import json
import unittest
from pathlib import Path

from rallymate_scoring.calibration_fitting import (
    CalibrationFitError,
    validate_prepared_dataset,
)
from rallymate_scoring.calibration_portfolio import (
    load_latest_scoring_truth_calibration_portfolio,
    validate_scoring_truth_calibration_portfolio,
)


ROOT = Path(__file__).resolve().parents[1]
LATEST = ROOT / "reports" / "scoring-truth-calibration-portfolio" / "latest.json"


class ScoringTruthCalibrationPortfolioTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        if not LATEST.is_file():
            raise unittest.SkipTest("published M49 calibration portfolio is missing")
        cls.manifest = load_latest_scoring_truth_calibration_portfolio(LATEST)

    def test_empty_truth_portfolio_is_complete_but_fail_closed(self) -> None:
        manifest = self.manifest
        self.assertEqual(manifest["status"], "annotation_required")
        self.assertEqual(manifest["counts"]["indicators"], 13)
        self.assertEqual(manifest["counts"]["truth_manifest_videos"], 3)
        self.assertEqual(manifest["counts"]["measurement_source_videos"], 3)
        self.assertEqual(manifest["counts"]["measurement_source_frames"], 15868)
        self.assertEqual(manifest["counts"]["accepted_manual_events"], 0)
        self.assertEqual(manifest["counts"]["manual_feature_records"], 0)
        self.assertEqual(manifest["counts"]["calibration_samples"], 0)
        self.assertEqual(manifest["counts"]["prepared_indicators"], 13)
        self.assertEqual(manifest["counts"]["fit_ready_indicators"], 0)
        self.assertTrue(
            all(not item["fit_ready"] for item in manifest["indicator_readiness"])
        )
        self.assertFalse(manifest["safety"]["gpu_inference_executed"])
        self.assertFalse(manifest["safety"]["fit_executed"])
        self.assertFalse(manifest["safety"]["grades_generated"])

    def test_all_thirteen_prepared_contracts_validate_but_fit_reject(self) -> None:
        prepared = self.manifest["artifacts"]["prepared_by_indicator"]
        self.assertEqual(set(prepared), set(self.manifest["registry"]["indicator_ids"]))
        for indicator_id, binding in prepared.items():
            payload = json.loads(Path(binding["path"]).read_text(encoding="utf-8"))
            audit = validate_prepared_dataset(payload, require_fit_ready=False)
            self.assertEqual(audit["indicator_id"], indicator_id)
            with self.assertRaisesRegex(
                CalibrationFitError, "upstream readiness blockers"
            ):
                validate_prepared_dataset(payload, require_fit_ready=True)

    def test_status_cannot_be_forged_after_source_replay(self) -> None:
        forged = copy.deepcopy(self.manifest)
        forged["status"] = "all_indicators_prepared_requires_external_protocol"
        with self.assertRaisesRegex(ValueError, "status differs from replay"):
            validate_scoring_truth_calibration_portfolio(
                forged, verify_sources=True
            )

    def test_unsafe_claim_is_rejected_without_source_replay(self) -> None:
        forged = copy.deepcopy(self.manifest)
        forged["safety"]["grades_generated"] = True
        with self.assertRaisesRegex(ValueError, "unsafe"):
            validate_scoring_truth_calibration_portfolio(forged)

    def test_machine_schema_accepts_published_manifest(self) -> None:
        try:
            import jsonschema
        except ImportError:
            self.skipTest("jsonschema unavailable")
        schema = json.loads(
            (
                ROOT
                / "contracts"
                / "scoring-truth-calibration-portfolio.schema.json"
            ).read_text(encoding="utf-8")
        )
        jsonschema.Draft202012Validator.check_schema(schema)
        jsonschema.Draft202012Validator(schema).validate(self.manifest)


if __name__ == "__main__":
    unittest.main()
