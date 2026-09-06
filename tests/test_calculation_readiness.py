from __future__ import annotations

import json
import unittest
from copy import deepcopy
from pathlib import Path

try:
    from jsonschema import Draft202012Validator
except ModuleNotFoundError:  # Runtime validation does not depend on jsonschema.
    Draft202012Validator = None

from rallymate_scoring.calculation_readiness import (
    CalculationReadinessError,
    build_indicator_calculation_readiness,
    validate_indicator_calculation_readiness,
)


ROOT = Path(__file__).resolve().parents[1]


def _jsonl(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line]


def _sources() -> dict[str, dict[str, str]]:
    return {
        name: {"path": name, "sha256": "A" * 64}
        for name in ("feasibility_registry", "events", "indicator_features", "scores")
    }


class CalculationReadinessTests(unittest.TestCase):
    def test_missing_event_and_measured_indicator_are_not_conflated(self) -> None:
        registry = {
            "registry_version": "test",
            "indicators": [
                {
                    "indicator_id": "FS01-M01",
                    "feasibility_level": "F2",
                    "required_events": ["FS01.preload"],
                    "required_features": ["feature_a"],
                },
                {
                    "indicator_id": "FS02-M01",
                    "feasibility_level": "F2",
                    "required_events": ["FS02.initiation"],
                    "required_features": ["feature_b"],
                },
            ],
        }
        event = {
            "event_id": "event-1",
            "event_code": "FS01",
            "video_id": "video",
            "person_track_id": 1,
            "start_ms": 100,
            "end_ms": 200,
        }
        gate = {
            "hard_fail_flags": [],
            "scoring_block_flags": ["keypoint_jump_candidates_present"],
        }
        record = {
            "video_id": "video",
            "event_id": "event-1",
            "event_code": "FS01",
            "person_track_id": 1,
            "indicator_id": "FS01-M01",
            "feature_status": "measured",
            "scoring_status": "unavailable",
            "features": [
                {
                    "feature_name": "feature_a",
                    "valid": True,
                    "reason": "valid",
                    "source_frames": [3, 4],
                }
            ],
            "quality_gate": gate,
        }
        score = {
            "video_id": "video",
            "event_id": "event-1",
            "indicator_id": "FS01-M01",
            "status": "unavailable",
        }
        report = build_indicator_calculation_readiness(
            registry=registry,
            events=[event],
            indicator_records=[record],
            scores=[score],
            video_id="video",
            sources=_sources(),
        )
        self.assertEqual("some_registry_indicators_have_no_measured_candidate", report["status"])
        self.assertEqual(1, report["summary"]["indicator_with_measured_candidate_count"])
        self.assertEqual("measured_on_at_least_one_candidate", report["per_indicator"][0]["calculation_status"])
        self.assertEqual("required_event_candidate_missing", report["per_indicator"][1]["calculation_status"])
        self.assertEqual({}, report["per_indicator"][0]["feature_failure_reason_counts"])
        self.assertIn("keypoint_jump_candidates_present", report["per_indicator"][0]["scoring_block_flag_counts"])

    def test_validator_rejects_fake_completeness_or_gate_relaxation(self) -> None:
        full = self._real_report(
            ROOT / "reports" / "fs09-pose-wave-v2-m42" / "850cb0006b406c7176eeda8d711cd065"
        )
        tampered = deepcopy(full)
        tampered["summary"]["indicator_with_measured_candidate_count"] = 12
        with self.assertRaisesRegex(CalculationReadinessError, "summary accounting"):
            validate_indicator_calculation_readiness(tampered)
        tampered = deepcopy(full)
        tampered["measurement_remediation"][0]["automatic_quality_gate_relaxation_allowed"] = True
        with self.assertRaisesRegex(CalculationReadinessError, "weakens"):
            validate_indicator_calculation_readiness(tampered)
        tampered = deepcopy(full)
        tampered["sources"]["events"]["sha256"] = "not-a-sha256"
        with self.assertRaisesRegex(CalculationReadinessError, "source lineage"):
            validate_indicator_calculation_readiness(tampered)

    def _real_report(self, directory: Path) -> dict:
        registry = json.loads((ROOT / "metric-feasibility-pose-wave-v2.json").read_text(encoding="utf-8"))
        summary = json.loads((directory / "scoring-loop-summary.json").read_text(encoding="utf-8"))
        return build_indicator_calculation_readiness(
            registry=registry,
            events=_jsonl(directory / "events.jsonl"),
            indicator_records=_jsonl(directory / "indicator-features.jsonl"),
            scores=_jsonl(directory / "scores.jsonl"),
            video_id=summary["video_id"],
            sources=_sources(),
        )

    def test_real_default_smoke_reports_all_13_measured_indicators(self) -> None:
        run = ROOT / "runs" / "rtmpose-m-halpe26-default-m42-scoring-vector-smoke"
        report = self._real_report(run)
        self.assertEqual(13, report["summary"]["indicator_with_measured_candidate_count"])
        self.assertEqual(0, report["summary"]["indicator_without_measured_candidate_count"])
        self.assertFalse(report["summary"]["formal_scoring_ready"])
        missing = {
            item["indicator_id"]
            for item in report["per_indicator"]
            if item["calculation_status"] != "measured_on_at_least_one_candidate"
        }
        self.assertEqual(set(), missing)
        materialized = json.loads((run / "calculation-readiness.json").read_text(encoding="utf-8"))
        validate_indicator_calculation_readiness(materialized)
        summary = json.loads((run / "summary.json").read_text(encoding="utf-8"))
        self.assertEqual("calculation-readiness.json", summary["artifacts"]["calculation_readiness_json"])
        self.assertEqual(13, summary["calculation_readiness"]["indicator_with_measured_candidate_count"])
        self.assertIn(
            "calculation-readiness.json",
            (run / "analysis-report.html").read_text(encoding="utf-8"),
        )

    def test_real_full_video_has_effectively_calculated_all_13(self) -> None:
        report = self._real_report(
            ROOT / "reports" / "fs09-pose-wave-v2-m42" / "850cb0006b406c7176eeda8d711cd065"
        )
        self.assertEqual("all_registry_indicators_have_measured_candidate", report["status"])
        self.assertEqual(13, report["summary"]["indicator_with_measured_candidate_count"])
        self.assertEqual(403, report["summary"]["measured_indicator_event_instance_count"])
        self.assertEqual(39, report["summary"]["unavailable_indicator_event_instance_count"])
        self.assertTrue(report["summary"]["all_registry_indicators_effectively_calculated_on_this_video"])
        self.assertFalse(report["summary"]["formal_scoring_ready"])

    def test_summary_schema_accepts_registry_driven_13_indicator_run(self) -> None:
        if Draft202012Validator is None:
            self.skipTest("jsonschema unavailable")
        schema = json.loads(
            (ROOT / "contracts" / "summary.schema.json").read_text(encoding="utf-8")
        )
        Draft202012Validator.check_schema(schema)
        summary = json.loads(
            (
                ROOT
                / "runs"
                / "rtmpose-m-halpe26-default-m42-scoring-vector-smoke"
                / "summary.json"
            ).read_text(encoding="utf-8")
        )
        errors = sorted(Draft202012Validator(schema).iter_errors(summary), key=str)
        self.assertEqual([], errors)
        self.assertEqual(13, summary["scoring_state"]["target_indicator_count"])


if __name__ == "__main__":
    unittest.main()
