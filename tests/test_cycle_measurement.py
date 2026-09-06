from __future__ import annotations

import json
import shutil
import tempfile
import unittest
from copy import deepcopy
from pathlib import Path

try:
    from jsonschema import Draft202012Validator
except ModuleNotFoundError:
    Draft202012Validator = None

from rallymate_scoring.cycle_measurement import (
    CycleMeasurementError,
    build_scoring_cycle_measurement,
    validate_scoring_cycle_measurement,
)
from rallymate_vision.validation import OutputValidationError, validate_run_artifacts


ROOT = Path(__file__).resolve().parents[1]
FULL = ROOT / "reports" / "fs09-pose-wave-v2-m42" / "850cb0006b406c7176eeda8d711cd065"
SHORT = ROOT / "runs" / "rtmpose-m-halpe26-default-m42-scoring-vector-smoke"


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


class CycleMeasurementTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.registry = json.loads(
            (ROOT / "metric-feasibility-pose-wave-v2.json").read_text(encoding="utf-8")
        )

    def _build(self, directory: Path, video_id: str) -> dict:
        return build_scoring_cycle_measurement(
            registry=self.registry,
            events=_jsonl(directory / "events.jsonl"),
            indicator_records=_jsonl(directory / "indicator-features.jsonl"),
            scores=_jsonl(directory / "scores.jsonl"),
            video_id=video_id,
            sources=_sources(),
        )

    def test_real_full_video_has_25_coherent_complete_cycles(self) -> None:
        report = self._build(FULL, "850cb0006b406c7176eeda8d711cd065")
        self.assertEqual("complete_cycle_available", report["status"])
        self.assertEqual(34, report["summary"]["linked_cycle_count"])
        self.assertEqual(25, report["summary"]["complete_cycle_count"])
        self.assertEqual(13, report["summary"]["best_cycle_measured_indicator_count"])
        self.assertEqual(0, report["summary"]["unmatched_event_count"])
        selected = report["representative_cycle"]
        self.assertEqual(["FS01", "FS02", "FS09"], [item["event_code"] for item in selected["events"]])
        self.assertEqual(13, selected["measured_indicator_count"])
        self.assertEqual(12, selected["scoring_gate_passed_indicator_count"])
        self.assertEqual(13, len(selected["indicator_measurements"]))
        self.assertEqual(0, sum(item["grade"] is not None for item in selected["indicator_measurements"]))

    def test_short_video_has_one_complete_cycle_without_cross_cycle_mixing(self) -> None:
        report = self._build(SHORT, "850cb0006b406c7176eeda8d711cd065")
        self.assertEqual("complete_cycle_available", report["status"])
        self.assertEqual(3, report["summary"]["linked_cycle_count"])
        self.assertEqual(1, report["summary"]["complete_cycle_count"])
        self.assertEqual(13, report["summary"]["best_cycle_measured_indicator_count"])
        self.assertFalse(report["selection_policy"]["cross_cycle_feature_mixing_used"])

    def test_linking_requires_exact_shared_phase_not_event_id_pattern(self) -> None:
        events = _jsonl(SHORT / "events.jsonl")
        fs09 = next(item for item in events if item["event_code"] == "FS09")
        fs09["key_phases_ms"]["peak_speed_ms"] += 1
        report = build_scoring_cycle_measurement(
            registry=self.registry,
            events=events,
            indicator_records=_jsonl(SHORT / "indicator-features.jsonl"),
            scores=_jsonl(SHORT / "scores.jsonl"),
            video_id="850cb0006b406c7176eeda8d711cd065",
            sources=_sources(),
        )
        self.assertEqual(2, report["summary"]["linked_cycle_count"])
        self.assertEqual(3, report["summary"]["unmatched_event_count"])

    def test_selection_is_invariant_to_feature_values_and_validator_rejects_claims(self) -> None:
        events = _jsonl(FULL / "events.jsonl")
        records = _jsonl(FULL / "indicator-features.jsonl")
        scores = _jsonl(FULL / "scores.jsonl")
        baseline = build_scoring_cycle_measurement(
            registry=self.registry,
            events=events,
            indicator_records=records,
            scores=scores,
            video_id="850cb0006b406c7176eeda8d711cd065",
            sources=_sources(),
        )
        changed = deepcopy(records)
        for record in changed:
            for feature in record["features"]:
                if feature["valid"]:
                    feature["value"] = 999999.0
        rebuilt = build_scoring_cycle_measurement(
            registry=self.registry,
            events=events,
            indicator_records=changed,
            scores=scores,
            video_id="850cb0006b406c7176eeda8d711cd065",
            sources=_sources(),
        )
        self.assertEqual(
            baseline["representative_cycle"]["cycle_id"],
            rebuilt["representative_cycle"]["cycle_id"],
        )
        tampered = deepcopy(baseline)
        tampered["selection_policy"]["feature_values_used_for_selection"] = True
        with self.assertRaisesRegex(CycleMeasurementError, "unsafe cycle selection"):
            validate_scoring_cycle_measurement(tampered)
        tampered = deepcopy(baseline)
        tampered["representative_cycle"]["indicator_measurements"][0]["grade"] = "A"
        with self.assertRaisesRegex(CycleMeasurementError, "grade or threshold"):
            validate_scoring_cycle_measurement(tampered)
        tampered = deepcopy(baseline)
        tampered["cycles"][0]["events"][1]["link_phases_ms"]["peak_speed_ms"] += 1
        with self.assertRaisesRegex(CycleMeasurementError, "phase/time evidence"):
            validate_scoring_cycle_measurement(tampered)
        tampered = deepcopy(baseline)
        tampered["representative_cycle"]["indicator_measurements"][0]["feature_vector"].pop()
        with self.assertRaisesRegex(CycleMeasurementError, "feature contract"):
            validate_scoring_cycle_measurement(tampered)

    def test_run_validator_rejects_stale_cycle_report_source_binding(self) -> None:
        # This checked-in M42 run is an immutable historical snapshot.  Its
        # derived report points at the mutable root registry and must fail
        # closed after the registry advances instead of being silently
        # reinterpreted as a current run.
        with self.assertRaisesRegex(OutputValidationError, "SHA-256 mismatch"):
            validate_run_artifacts(SHORT)

    def test_schema_accepts_materialized_real_reports(self) -> None:
        if Draft202012Validator is None:
            self.skipTest("jsonschema unavailable")
        schema = json.loads(
            (ROOT / "contracts" / "scoring-cycle-measurement.schema.json").read_text(
                encoding="utf-8"
            )
        )
        Draft202012Validator.check_schema(schema)
        validator = Draft202012Validator(schema)
        for path in (
            FULL / "scoring-cycle-measurement.json",
            SHORT / "scoring-cycle-measurement.json",
        ):
            artifact = json.loads(path.read_text(encoding="utf-8"))
            self.assertEqual([], list(validator.iter_errors(artifact)))


if __name__ == "__main__":
    unittest.main()
