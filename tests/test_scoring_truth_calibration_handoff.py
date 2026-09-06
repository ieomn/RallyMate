from __future__ import annotations

import copy
import json
import tempfile
import unittest
from pathlib import Path

from rallymate_scoring.calibration_fitting import (
    CalibrationFitError,
    validate_prepared_dataset,
)
from rallymate_scoring.calibration_handoff import (
    build_scoring_truth_calibration_handoff,
    load_latest_scoring_truth_calibration_handoff,
    validate_scoring_truth_calibration_handoff,
)


ROOT = Path(__file__).resolve().parents[1]
TRUTH_REFRESH_LATEST = ROOT / "reports" / "scoring-truth-refresh" / "latest.json"
HANDOFF_LATEST = (
    ROOT / "reports" / "scoring-truth-calibration-handoff" / "latest.json"
)


class ScoringTruthCalibrationHandoffTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls._temporary = tempfile.TemporaryDirectory()
        cls.addClassCleanup(cls._temporary.cleanup)
        cls.output_root = Path(cls._temporary.name) / "handoffs"
        cls.manifest = build_scoring_truth_calibration_handoff(
            truth_refresh_latest_path=TRUTH_REFRESH_LATEST,
            output_root=cls.output_root,
            handoff_id="synthetic-m48-empty",
            generated_at="2026-08-22T00:00:00+00:00",
        )

    def test_empty_truth_builds_all_thirteen_nonready_contracts_without_scoring(self) -> None:
        manifest = self.manifest
        self.assertEqual(manifest["status"], "annotation_required")
        self.assertEqual(manifest["counts"]["indicators"], 13)
        self.assertEqual(manifest["counts"]["accepted_manual_events"], 0)
        self.assertEqual(manifest["counts"]["manual_feature_records"], 0)
        self.assertEqual(manifest["counts"]["calibration_samples"], 0)
        self.assertEqual(manifest["counts"]["prepared_indicators"], 13)
        self.assertEqual(manifest["counts"]["fit_ready_indicators"], 0)
        self.assertFalse(manifest["safety"]["fit_executed"])
        self.assertFalse(manifest["safety"]["thresholds_generated"])
        self.assertFalse(manifest["safety"]["grades_generated"])
        self.assertTrue(all(not item["fit_ready"] for item in manifest["indicator_readiness"]))
        self.assertTrue(
            all(item["fit_blocker"] for item in manifest["indicator_readiness"])
        )
        scores_path = Path(
            manifest["artifacts"]["manual_event_features"]["scores"]["path"]
        )
        self.assertEqual(scores_path.read_text(encoding="utf-8"), "")

    def test_latest_pointer_and_all_sources_replay(self) -> None:
        loaded = load_latest_scoring_truth_calibration_handoff(
            self.output_root / "latest.json"
        )
        self.assertEqual(loaded, self.manifest)
        validate_scoring_truth_calibration_handoff(loaded, verify_sources=True)

    def test_prepared_contracts_are_valid_but_fit_gate_rejects_every_indicator(self) -> None:
        for indicator_id, binding in self.manifest["artifacts"][
            "prepared_by_indicator"
        ].items():
            payload = json.loads(Path(binding["path"]).read_text(encoding="utf-8"))
            audit = validate_prepared_dataset(payload, require_fit_ready=False)
            self.assertEqual(audit["indicator_id"], indicator_id)
            with self.assertRaisesRegex(
                CalibrationFitError,
                "upstream readiness blockers|verified authorized truth intake",
            ):
                validate_prepared_dataset(payload, require_fit_ready=True)

    def test_replayed_fit_readiness_cannot_be_forged(self) -> None:
        forged = copy.deepcopy(self.manifest)
        forged["indicator_readiness"][0]["fit_ready"] = True
        forged["indicator_readiness"][0]["fit_blocker"] = None
        forged["counts"]["fit_ready_indicators"] = 1
        with self.assertRaisesRegex(ValueError, "differs from replay"):
            validate_scoring_truth_calibration_handoff(forged, verify_sources=True)

    def test_existing_target_is_immutable(self) -> None:
        with self.assertRaises(FileExistsError):
            build_scoring_truth_calibration_handoff(
                truth_refresh_latest_path=TRUTH_REFRESH_LATEST,
                output_root=self.output_root,
                handoff_id="synthetic-m48-empty",
            )

    def test_machine_schema_accepts_generated_manifest(self) -> None:
        try:
            import jsonschema
        except ImportError:
            self.skipTest("jsonschema unavailable")
        schema = json.loads(
            (
                ROOT
                / "contracts"
                / "scoring-truth-calibration-handoff.schema.json"
            ).read_text(encoding="utf-8")
        )
        jsonschema.Draft202012Validator.check_schema(schema)
        jsonschema.Draft202012Validator(schema).validate(self.manifest)

    def test_current_published_handoff_is_fail_closed(self) -> None:
        if not HANDOFF_LATEST.is_file():
            self.skipTest("real M48 handoff has not been published yet")
        manifest = load_latest_scoring_truth_calibration_handoff(HANDOFF_LATEST)
        self.assertEqual(manifest["handoff_id"], "m53-empty-registry-17-v1")
        self.assertEqual(manifest["status"], "annotation_required")
        self.assertEqual(manifest["counts"]["fit_ready_indicators"], 0)
        self.assertFalse(manifest["safety"]["candidate_written"])


if __name__ == "__main__":
    unittest.main()
