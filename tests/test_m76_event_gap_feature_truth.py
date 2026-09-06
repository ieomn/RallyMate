from __future__ import annotations

import csv
import json
import math
import tempfile
import unittest
from pathlib import Path

from rallymate_evaluation.event_gap_feature_truth import (
    EventGapFeatureTruthError,
    _numeric_signed_error,
    evaluate_event_gap_feature_truth,
    validate_event_gap_feature_truth_report,
    validate_event_gap_feature_truth_report_sources,
)
from rallymate_evaluation.event_gap_keypoint_truth import (
    build_event_gap_truth_pack,
    compile_event_gap_truth_pack,
)
from rallymate_evaluation.small_roi_keypoint_truth import (
    ADJUDICATION_FIELDS,
    ANNOTATION_FIELDS,
)


ROOT = Path(__file__).resolve().parents[1]
M74_REPORT = (
    ROOT
    / "reports"
    / "measurement-recovery-m74"
    / "event-bounded-gap-audit-v1"
    / "report.json"
)
ASSETS = ROOT / "src" / "rallymate_annotation" / "assets"
REAL_PACK = ROOT / "data" / "annotations" / "event-bounded-pose-gap-truth-m75-v1"
REAL_REPORT = (
    ROOT
    / "reports"
    / "measurement-recovery-m76"
    / "event-gap-feature-error-empty.json"
)


def _jsonl(path: Path) -> list[dict]:
    return [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line
    ]


def _write_csv(path: Path, fields: tuple[str, ...], rows: list[dict]) -> None:
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(fields))
        writer.writeheader()
        writer.writerows(rows)


def _complete_pack(pack: Path, *, invisible_task_id: str | None = None) -> None:
    predictions = {
        row["task_id"]: row
        for row in _jsonl(pack / "sealed-interpolation-predictions.jsonl")
    }
    annotations: list[dict] = []
    adjudications: list[dict] = []
    for task_id, prediction in sorted(predictions.items()):
        visible = task_id != invisible_task_id
        x = str(prediction["x_normalized"]) if visible else ""
        y = str(prediction["y_normalized"]) if visible else ""
        reason = "" if visible else "synthetic_occluded_for_contract_test"
        source_ids: list[str] = []
        for annotator_id in ("synthetic-annotator-a", "synthetic-annotator-b"):
            annotation_id = f"{annotator_id}:{task_id}"
            source_ids.append(annotation_id)
            annotations.append(
                {
                    "annotation_id": annotation_id,
                    "task_id": task_id,
                    "annotator_id": annotator_id,
                    "visible": str(visible).lower(),
                    "x_normalized": x,
                    "y_normalized": y,
                    "visibility_reason": reason,
                    "annotated_at": "2026-08-22T00:00:00Z",
                }
            )
        adjudications.append(
            {
                "adjudication_id": f"synthetic-decision:{task_id}",
                "task_id": task_id,
                "source_annotation_ids": ";".join(source_ids),
                "reviewer_id": "synthetic-reviewer-c",
                "visible": str(visible).lower(),
                "x_normalized": x,
                "y_normalized": y,
                "visibility_reason": reason,
                "adjudicated_at": "2026-08-22T01:00:00Z",
                "status": "accepted",
            }
        )
    _write_csv(pack / "annotations.csv", ANNOTATION_FIELDS, annotations)
    _write_csv(pack / "adjudications.csv", ADJUDICATION_FIELDS, adjudications)


class EventGapFeatureTruthTests(unittest.TestCase):
    def test_real_empty_report_is_source_replayable_and_fail_closed(self) -> None:
        report = json.loads(REAL_REPORT.read_text(encoding="utf-8"))

        validate_event_gap_feature_truth_report(report)
        validate_event_gap_feature_truth_report_sources(report)
        self.assertEqual("annotation_required", report["status"])
        self.assertEqual(142, report["scope"]["interpolation_candidate_joint_tasks"])
        self.assertEqual(
            11, report["scope"]["indicator_instances_with_interpolation_points"]
        )
        self.assertEqual(47, report["scope"]["required_feature_records"])
        self.assertEqual(25, report["scope"]["unique_required_features"])
        self.assertEqual(
            2, report["scope"]["phase_only_residual_indicator_instances_excluded"]
        )
        self.assertIsNone(report["metrics"])
        self.assertEqual([], report["details"])
        self.assertFalse(report["acceptance"]["production_interpolation_allowed"])
        self.assertFalse(report["safety"]["grade_generated"])
        self.assertFalse(report["safety"]["threshold_generated"])

    def test_synthetic_truth_replays_47_features_with_zero_difference(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            pack = Path(temp) / "pack"
            build_event_gap_truth_pack(
                m74_report_path=M74_REPORT,
                output_dir=pack,
                asset_directory=ASSETS,
            )
            _complete_pack(pack)
            validation = compile_event_gap_truth_pack(pack)
            report = evaluate_event_gap_feature_truth(pack)

            self.assertEqual("ready_for_interpolation_error_evaluation", validation["status"])
            self.assertEqual(
                "evaluated_external_acceptance_protocol_required", report["status"]
            )
            self.assertEqual(47, report["counts"]["feature_records"])
            self.assertEqual(47, len(report["details"]))
            self.assertEqual(
                7, report["counts"]["candidate_complete_indicator_instances"]
            )
            self.assertEqual(
                7, report["counts"]["truth_conditioned_complete_indicator_instances"]
            )
            self.assertEqual(0.0, report["metrics"]["numeric"]["mae"])
            self.assertEqual(0.0, report["metrics"]["numeric"]["p95_absolute_error"])
            self.assertEqual(
                0.0, report["metrics"]["numeric"]["bias_candidate_minus_truth"]
            )
            self.assertEqual(
                1.0, report["metrics"]["categorical_code"]["agreement_rate"]
            )
            self.assertEqual(25, len(report["per_feature"]))
            self.assertEqual(6, len(report["per_indicator"]))
            self.assertFalse(report["acceptance"]["f2_to_f3_allowed"])
            self.assertFalse(report["safety"]["production_enabled"])
            validate_event_gap_feature_truth_report_sources(report)

    def test_invisible_truth_is_missing_not_zero(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            pack = Path(temp) / "pack"
            build_event_gap_truth_pack(
                m74_report_path=M74_REPORT,
                output_dir=pack,
                asset_directory=ASSETS,
            )
            first_task_id = _jsonl(pack / "tasks.jsonl")[0]["task_id"]
            _complete_pack(pack, invisible_task_id=first_task_id)
            validation = compile_event_gap_truth_pack(pack)
            report = evaluate_event_gap_feature_truth(pack)

            self.assertEqual("ready_for_interpolation_error_evaluation", validation["status"])
            self.assertGreater(report["counts"]["invisible_gap_truth_references"], 0)
            self.assertTrue(
                all(
                    row["truth_conditioned"]["reason"] != "missing_encoded_as_zero"
                    for row in report["details"]
                )
            )
            self.assertTrue(
                any(
                    not row["truth_conditioned"]["valid"]
                    or row["truth_conditioned"]["value"]
                    != row["candidate"]["value"]
                    for row in report["details"]
                )
            )

    def test_circular_direction_error_wraps_at_360(self) -> None:
        error, semantics = _numeric_signed_error("launch_direction_deg", 359.0, 1.0)
        self.assertEqual(-2.0, error)
        self.assertEqual("wrapped_candidate_minus_truth_deg", semantics)
        linear, linear_semantics = _numeric_signed_error("other", 359.0, 1.0)
        self.assertEqual(358.0, linear)
        self.assertEqual("candidate_minus_truth", linear_semantics)

    def test_rehashed_report_metric_tampering_fails_source_replay(self) -> None:
        report = json.loads(REAL_REPORT.read_text(encoding="utf-8"))
        report["scope"]["comparison_semantics"] += ";tampered"
        with self.assertRaisesRegex(EventGapFeatureTruthError, "source replay mismatch"):
            validate_event_gap_feature_truth_report_sources(report)


if __name__ == "__main__":
    unittest.main()
