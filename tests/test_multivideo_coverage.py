from __future__ import annotations

import copy
import json
import tempfile
import unittest
from pathlib import Path

from rallymate_scoring.multivideo_coverage import (
    build_multivideo_indicator_calculation_coverage,
    load_latest_multivideo_indicator_calculation_coverage,
    validate_multivideo_indicator_calculation_coverage,
)


ROOT = Path(__file__).resolve().parents[1]
LATEST = ROOT / "reports" / "multivideo-indicator-calculation-coverage" / "latest.json"
MEASUREMENT_LATEST = (
    ROOT / "reports" / "scoring-truth-measurement-sources" / "latest.json"
)
REGISTRY = ROOT / "metric-feasibility-pose-wave-v2.json"
SOURCE_REPORTS = sorted(
    (ROOT / "reports" / "scoring-candidate-multivideo-m63" / "reports").glob(
        "*.json"
    )
)


class MultivideoIndicatorCalculationCoverageTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        if not LATEST.is_file():
            raise unittest.SkipTest("published M50 multivideo coverage is missing")
        cls.manifest = load_latest_multivideo_indicator_calculation_coverage(LATEST)

    def test_real_three_video_portfolio_computes_all_thirteen_in_every_video(self) -> None:
        counts = self.manifest["counts"]
        self.assertEqual(counts["videos"], 3)
        self.assertEqual(counts["frames"], 15868)
        self.assertEqual(counts["candidate_events"], 546)
        self.assertEqual(counts["indicator_event_instances"], 2366)
        self.assertEqual(counts["measured_feature_vectors"], 2291)
        self.assertEqual(counts["unavailable_feature_vectors"], 75)
        self.assertEqual(counts["indicators"], 13)
        self.assertEqual(counts["indicators_with_measured_vector"], 13)
        self.assertEqual(counts["indicators_measured_in_every_video"], 13)
        self.assertEqual(
            counts["score_status_counts"],
            {"calibration_required": 834, "unavailable": 1532},
        )
        self.assertEqual(counts["grade_count"], 0)
        self.assertEqual(counts["threshold_version_count"], 0)
        self.assertTrue(
            all(item["indicators_with_measured_vector"] == 13 for item in self.manifest["videos"])
        )

    def test_each_indicator_has_traceable_real_feature_vector_per_video(self) -> None:
        registry = json.loads(REGISTRY.read_text(encoding="utf-8"))
        required = {
            item["indicator_id"]: item.get(
                "measurement_features", item["required_features"]
            )
            for item in registry["indicators"]
        }
        self.assertEqual(
            {item["indicator_id"] for item in self.manifest["indicators"]},
            set(required),
        )
        for item in self.manifest["indicators"]:
            self.assertTrue(item["measured_in_every_video"])
            self.assertEqual(item["videos_with_measured_vector"], 3)
            self.assertEqual(item["candidate_instances"], 182)
            self.assertGreater(item["measured_feature_vectors"], 0)
            self.assertEqual(len(item["by_video"]), 3)
            for video in item["by_video"]:
                evidence = video["first_measured_evidence"]
                self.assertIsNotNone(evidence)
                self.assertEqual(
                    [feature["feature_name"] for feature in evidence["features"]],
                    required[item["indicator_id"]],
                )
                self.assertTrue(
                    all(
                        feature["valid"]
                        and feature["value"] is not None
                        and feature["unit"]
                        and feature["feature_version"]
                        and feature["source_frames"]
                        for feature in evidence["features"]
                    )
                )

    def test_missing_video_report_cannot_build_portfolio(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            with self.assertRaisesRegex(ValueError, "exactly cover"):
                build_multivideo_indicator_calculation_coverage(
                    measurement_sources_latest_path=MEASUREMENT_LATEST,
                    feasibility_registry_path=REGISTRY,
                    source_report_paths=SOURCE_REPORTS[:2],
                    output_root=Path(temporary),
                    coverage_id="missing-one-video",
                )

    def test_replayed_indicator_count_cannot_be_forged(self) -> None:
        forged = copy.deepcopy(self.manifest)
        forged["indicators"][0]["measured_feature_vectors"] += 1
        with self.assertRaisesRegex(ValueError, "differs from replay: indicators"):
            validate_multivideo_indicator_calculation_coverage(
                forged, verify_sources=True
            )

    def test_accuracy_or_grade_claim_fails_closed_without_replay(self) -> None:
        forged = copy.deepcopy(self.manifest)
        forged["safety"]["feature_accuracy_claim"] = True
        with self.assertRaisesRegex(ValueError, "unsafe"):
            validate_multivideo_indicator_calculation_coverage(forged)
        forged = copy.deepcopy(self.manifest)
        forged["counts"]["grade_count"] = 1
        with self.assertRaisesRegex(ValueError, "completeness"):
            validate_multivideo_indicator_calculation_coverage(forged)

    def test_machine_schema_accepts_real_coverage(self) -> None:
        try:
            import jsonschema
        except ImportError:
            self.skipTest("jsonschema unavailable")
        schema = json.loads(
            (
                ROOT
                / "contracts"
                / "multivideo-indicator-calculation-coverage.schema.json"
            ).read_text(encoding="utf-8")
        )
        jsonschema.Draft202012Validator.check_schema(schema)
        jsonschema.Draft202012Validator(schema).validate(self.manifest)


if __name__ == "__main__":
    unittest.main()
