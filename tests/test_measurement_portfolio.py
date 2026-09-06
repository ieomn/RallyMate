from __future__ import annotations

import json
import shutil
import tempfile
import unittest
from copy import deepcopy
from pathlib import Path

try:
    from jsonschema import Draft202012Validator
except ModuleNotFoundError:  # Runtime validation has no jsonschema dependency.
    Draft202012Validator = None

from rallymate_scoring.measurement_portfolio import (
    MeasurementPortfolioError,
    build_indicator_measurement_portfolio,
    validate_indicator_measurement_portfolio,
)
from rallymate_vision.validation import OutputValidationError, validate_run_artifacts


ROOT = Path(__file__).resolve().parents[1]


def _jsonl(path: Path) -> list[dict]:
    return [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def _sources() -> dict[str, dict[str, str]]:
    return {
        name: {"path": name, "sha256": "A" * 64}
        for name in ("feasibility_registry", "events", "indicator_features", "scores")
    }


class MeasurementPortfolioTests(unittest.TestCase):
    def _real_portfolio(self, directory: Path) -> dict:
        registry = json.loads(
            (ROOT / "metric-feasibility-pose-wave-v2.json").read_text(
                encoding="utf-8"
            )
        )
        summary = json.loads(
            (directory / "scoring-loop-summary.json").read_text(encoding="utf-8")
        )
        return build_indicator_measurement_portfolio(
            registry=registry,
            events=_jsonl(directory / "events.jsonl"),
            indicator_records=_jsonl(directory / "indicator-features.jsonl"),
            scores=_jsonl(directory / "scores.jsonl"),
            video_id=summary["video_id"],
            sources=_sources(),
        )

    def test_representative_is_selected_by_observation_quality_not_value(self) -> None:
        registry = {
            "registry_version": "test",
            "indicators": [
                {
                    "indicator_id": "FS01-M01",
                    "feasibility_level": "F2",
                    "required_events": ["FS01.preload"],
                    "required_features": ["feature_a"],
                }
            ],
        }
        events = [
            {
                "event_id": "low-value-high-confidence",
                "event_code": "FS01",
                "video_id": "video",
                "person_track_id": 1,
                "start_ms": 0,
                "end_ms": 100,
                "confidence": 0.7,
            },
            {
                "event_id": "high-value-low-confidence",
                "event_code": "FS01",
                "video_id": "video",
                "person_track_id": 1,
                "start_ms": 200,
                "end_ms": 300,
                "confidence": 0.9,
            },
        ]
        records = []
        scores = []
        for event, value, confidence in zip(events, (1.0, 999.0), (0.9, 0.4)):
            feature = {
                "feature_name": "feature_a",
                "feature_version": "v1",
                "value": value,
                "unit": "body",
                "valid": True,
                "confidence": confidence,
                "reason": "valid",
                "source_frames": [1],
            }
            records.append(
                {
                    "video_id": "video",
                    "event_id": event["event_id"],
                    "event_code": "FS01",
                    "person_track_id": 1,
                    "indicator_id": "FS01-M01",
                    "feature_status": "measured",
                    "scoring_status": "calibration_required",
                    "features": [feature],
                    "quality_gate": {"status": "pass"},
                }
            )
            scores.append(
                {
                    "video_id": "video",
                    "event_id": event["event_id"],
                    "indicator_id": "FS01-M01",
                    "status": "calibration_required",
                    "grade": None,
                    "threshold_version": None,
                    "model_versions": {"pose": "test"},
                    "evidence": [{"video_sha256": "B" * 64}],
                }
            )
        report = build_indicator_measurement_portfolio(
            registry=registry,
            events=events,
            indicator_records=records,
            scores=scores,
            video_id="video",
            sources=_sources(),
        )
        selected = report["indicators"][0]["representative_measurement"]
        self.assertEqual("low-value-high-confidence", selected["event_id"])
        self.assertEqual(1.0, selected["feature_vector"][0]["value"])
        self.assertFalse(report["selection_policy"]["feature_values_used_for_selection"])

    def test_real_short_and_full_portfolios_preserve_honest_scope(self) -> None:
        short = self._real_portfolio(
            ROOT / "runs" / "rtmpose-m-halpe26-default-m42-scoring-vector-smoke"
        )
        self.assertEqual("all_registry_indicators_measured", short["status"])
        self.assertEqual(13, short["summary"]["measured_indicator_count"])
        missing = {
            item["indicator_id"]
            for item in short["indicators"]
            if item["measurement_status"] == "unavailable"
        }
        self.assertEqual(set(), missing)
        full = self._real_portfolio(
            ROOT
            / "reports"
            / "fs09-pose-wave-v2-m42"
            / "850cb0006b406c7176eeda8d711cd065"
        )
        self.assertEqual("all_registry_indicators_measured", full["status"])
        self.assertEqual(13, full["summary"]["measured_indicator_count"])
        self.assertEqual(403, full["summary"]["measured_indicator_event_instance_count"])
        for item in full["indicators"]:
            representative = item["representative_measurement"]
            self.assertIsNotNone(representative)
            self.assertEqual(
                item["required_features"],
                [entry["feature_name"] for entry in representative["feature_vector"]],
            )

    def test_validator_rejects_grade_value_selection_or_invalid_representative(self) -> None:
        report = self._real_portfolio(
            ROOT
            / "reports"
            / "fs09-pose-wave-v2-m42"
            / "850cb0006b406c7176eeda8d711cd065"
        )
        tampered = deepcopy(report)
        tampered["selection_policy"]["feature_values_used_for_selection"] = True
        with self.assertRaisesRegex(MeasurementPortfolioError, "unsafe representative"):
            validate_indicator_measurement_portfolio(tampered)
        tampered = deepcopy(report)
        tampered["indicators"][0]["representative_measurement"]["grade"] = "A"
        with self.assertRaisesRegex(MeasurementPortfolioError, "grade or threshold"):
            validate_indicator_measurement_portfolio(tampered)
        tampered = deepcopy(report)
        tampered["indicators"][0]["representative_measurement"]["feature_vector"][0]["valid"] = False
        with self.assertRaisesRegex(MeasurementPortfolioError, "unavailable feature"):
            validate_indicator_measurement_portfolio(tampered)

    def test_schema_accepts_real_materialized_portfolios(self) -> None:
        if Draft202012Validator is None:
            self.skipTest("jsonschema unavailable")
        schema = json.loads(
            (
                ROOT
                / "contracts"
                / "indicator-measurement-portfolio.schema.json"
            ).read_text(encoding="utf-8")
        )
        Draft202012Validator.check_schema(schema)
        for path in (
            ROOT
            / "runs"
            / "rtmpose-m-halpe26-default-m42-scoring-vector-smoke"
            / "indicator-measurement-portfolio.json",
            ROOT / "reports" / "indicator-measurement-portfolio-halpe256-full.json",
        ):
            if not path.exists():
                self.skipTest("materialized M39 portfolio not generated yet")
            artifact = json.loads(path.read_text(encoding="utf-8"))
            self.assertEqual([], list(Draft202012Validator(schema).iter_errors(artifact)))

    def test_run_validator_rejects_stale_declared_derived_source_hashes(self) -> None:
        source = ROOT / "runs" / "rtmpose-m-halpe26-default-m42-scoring-vector-smoke"
        # M42 is intentionally immutable.  Once the root registry advances,
        # the absolute source hash captured by its derived reports is stale;
        # validation must fail closed rather than treating it as current.
        with self.assertRaisesRegex(
            OutputValidationError,
            "derived artifact source SHA-256 mismatch",
        ):
            validate_run_artifacts(source)


if __name__ == "__main__":
    unittest.main()
