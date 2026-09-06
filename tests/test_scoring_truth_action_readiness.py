from __future__ import annotations

import copy
import csv
import importlib.util
import io
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from rallymate_annotation.scoring_action_readiness import (
    build_scoring_truth_action_readiness,
    validate_scoring_truth_action_readiness,
)
from rallymate_evaluation.pose_diagnostic_truth import (
    DIAGNOSTIC_TYPES,
    coverage_csv_header,
    evaluate_pose_diagnostic_truth,
    positives_csv_header,
)


ROOT = Path(__file__).resolve().parents[1]
WORKLIST = ROOT / "reports/scoring-truth-action-worklist-halpe256-full-m53/worklist.json"
TRUTH = ROOT / "data/annotations/scoring-truth-pack-v1/compiled"
SCORING_EVALUATION = ROOT / "reports/truth-pack-empty-evaluation-m53.json"
POSE_TRUTH = ROOT / "reports/pose-diagnostic-truth/halpe26-full-m53-v1"
CONTEXT = ROOT / "data/annotations/scoring-reference-context-v1/850cb0006b406c7176eeda8d711cd065-fs02-target-directions.json"


def _inputs(**overrides):
    values = {
        "worklist_path": WORKLIST,
        "truth_validation_path": TRUTH / "validation-report.json",
        "manual_events_path": TRUTH / "manual-events.jsonl",
        "manual_keypoints_path": TRUTH / "manual-keypoints.jsonl",
        "manual_semantics_path": TRUTH / "manual-semantics.jsonl",
        "scoring_truth_evaluation_path": SCORING_EVALUATION,
        "pose_truth_manifest_path": POSE_TRUTH / "manifest.json",
        "pose_truth_evaluation_path": POSE_TRUTH / "evaluation.json",
        "reference_context_path": CONTEXT,
        "generated_at": "2026-08-22T12:00:00Z",
    }
    values.update(overrides)
    return values


def _csv_text(header: list[str], rows: list[dict[str, object]]) -> str:
    buffer = io.StringIO(newline="")
    writer = csv.DictWriter(buffer, fieldnames=header, lineterminator="\r\n")
    writer.writeheader()
    writer.writerows(rows)
    return buffer.getvalue()


class ScoringTruthActionReadinessTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.report = build_scoring_truth_action_readiness(**_inputs())

    def test_real_empty_truth_remains_fully_fail_closed(self) -> None:
        report = self.report
        validate_scoring_truth_action_readiness(report)
        self.assertEqual(report["status"], "annotation_required")
        self.assertEqual(
            report["counts"]["by_status"],
            {
                "evidence_satisfied": 0,
                "review_in_progress_not_adjudicated": 0,
                "annotation_required": 168,
            },
        )
        self.assertEqual(
            report["counts"]["indicator_instances_by_status"],
            {
                "evidence_satisfied": 0,
                "review_in_progress_not_adjudicated": 0,
                "annotation_required": 266,
            },
        )
        self.assertEqual(report["counts"]["matched_manual_events"], 0)
        self.assertEqual(report["counts"]["pose_full_timeline_diagnostic_types"], 0)
        self.assertFalse(report["safety"]["scoring_state_modified"])
        self.assertFalse(report["safety"]["grades_generated"])

    def test_full_timeline_pose_truth_satisfies_only_pose_work_items(self) -> None:
        manifest = json.loads((POSE_TRUTH / "manifest.json").read_text(encoding="utf-8"))
        common = {
            "video_id": manifest["video_id"],
            "start_source_frame_index": manifest["frame_domain"]["first_source_frame_index"],
            "end_source_frame_index": manifest["frame_domain"]["last_source_frame_index"],
            "coverage_scope": "all_model_relevant_scopes",
            "review_status": "accepted",
            "annotator_ids": "synthetic-a;synthetic-b",
            "adjudicator_id": "synthetic-reviewer",
            "adjudicated_at": "2026-08-22T10:00:00Z",
            "null_reason": "",
            "notes": "contract test only",
        }
        coverage = _csv_text(
            coverage_csv_header(),
            [
                {
                    **common,
                    "coverage_id": f"coverage-{diagnostic_type}",
                    "diagnostic_type": diagnostic_type,
                }
                for diagnostic_type in DIAGNOSTIC_TYPES
            ],
        )
        positives = _csv_text(positives_csv_header(), [])
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            coverage_path = directory / "coverage.csv"
            positives_path = directory / "positives.csv"
            evaluation_path = directory / "evaluation.json"
            coverage_path.write_bytes(coverage.encode("utf-8"))
            positives_path.write_bytes(positives.encode("utf-8"))
            evaluation = evaluate_pose_diagnostic_truth(
                manifest=manifest,
                coverage_csv_text=coverage_path.read_text(encoding="utf-8"),
                positives_csv_text=positives_path.read_text(encoding="utf-8"),
                coverage_path=coverage_path,
                positives_path=positives_path,
                generated_at="2026-08-22T11:00:00Z",
            )
            evaluation_path.write_text(json.dumps(evaluation), encoding="utf-8")
            report = build_scoring_truth_action_readiness(
                **_inputs(pose_truth_evaluation_path=evaluation_path)
            )
        self.assertEqual(report["status"], "review_in_progress")
        self.assertEqual(report["counts"]["by_status"]["evidence_satisfied"], 92)
        self.assertEqual(report["counts"]["by_status"]["annotation_required"], 76)
        self.assertEqual(report["counts"]["pose_full_timeline_diagnostic_types"], 4)
        self.assertTrue(
            all(
                item["status"] == "evidence_satisfied"
                for item in report["items"]
                if item["review_type"] == "pose_diagnostic_truth"
            )
        )
        self.assertTrue(
            all(
                item["status"] == "annotation_required"
                for item in report["items"]
                if item["review_type"] != "pose_diagnostic_truth"
            )
        )

    def test_exact_manual_event_and_phase_evidence_satisfies_phase_item(self) -> None:
        worklist = json.loads(WORKLIST.read_text(encoding="utf-8"))
        target = next(
            item
            for item in worklist["items"]
            if item["truth_requirement"] == "manual_landing_phase_boundary"
        )
        predictions_path = Path(worklist["source"]["events"]["path"])
        prediction = next(
            json.loads(line)
            for line in predictions_path.read_text(encoding="utf-8").splitlines()
            if line.strip() and json.loads(line)["event_id"] == target["event_id"]
        )
        manual = {
            "schema_version": "1.0.0",
            "video_id": worklist["source"]["video_id"],
            "event_id": "manual-landing-contract-test",
            "person_track_id": prediction["person_track_id"],
            "event_code": prediction["event_code"],
            "start_ms": prediction["start_ms"],
            "end_ms": prediction["end_ms"],
            "key_phases_ms": dict(prediction["key_phases_ms"]),
            "confidence": 0.9,
            "boundary_uncertainty_ms": 20,
            "quality_flags": [],
            "view_group": "synthetic-fixed-view",
            "annotation_source": "manual",
            "annotator_id": "synthetic-event-annotator",
            "reviewer_id": "synthetic-event-reviewer",
            "adjudication_status": "accepted",
            "provenance": {
                "truth_pack_version": "synthetic-contract-test-only",
                "annotation_method": "synthetic_exact_event_contract_test",
            },
        }
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            manual_events = directory / "manual-events.jsonl"
            manual_keypoints = directory / "manual-keypoints.jsonl"
            manual_semantics = directory / "manual-semantics.jsonl"
            validation_path = directory / "validation-report.json"
            evaluation_path = directory / "scoring-truth-evaluation.json"
            manual_events.write_text(json.dumps(manual) + "\n", encoding="utf-8")
            manual_keypoints.write_text("", encoding="utf-8")
            manual_semantics.write_text("", encoding="utf-8")
            validation = json.loads(
                (TRUTH / "validation-report.json").read_text(encoding="utf-8")
            )
            validation["counts"]["manual_events"] = 1
            validation["outputs"]["manual_events"] = str(manual_events)
            validation["outputs"]["manual_keypoints"] = str(manual_keypoints)
            validation["outputs"]["manual_semantics"] = str(manual_semantics)
            validation_path.write_text(json.dumps(validation), encoding="utf-8")
            command = [
                sys.executable,
                str(ROOT / "scripts/evaluate_scoring_truth.py"),
                "--frames",
                str(ROOT / "reports/fs09-pose-wave-v2-m42/850cb0006b406c7176eeda8d711cd065/frames.jsonl"),
                "--primary-timeline",
                str(ROOT / "reports/pose-scoring-current-inputs/primary-player-v0.3.0/850cb0006b406c7176eeda8d711cd065/primary-player.jsonl"),
                "--predicted-events",
                str(predictions_path),
                "--manual-events",
                str(manual_events),
                "--manual-keypoints",
                str(manual_keypoints),
                "--manual-semantics",
                str(manual_semantics),
                "--registry",
                str(ROOT / "metric-feasibility-pose-wave-v2.json"),
                "--output",
                str(evaluation_path),
            ]
            environment = dict(os.environ)
            environment["PYTHONPATH"] = str(ROOT / "src")
            completed = subprocess.run(
                command,
                cwd=ROOT,
                env=environment,
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertEqual(completed.returncode, 0, completed.stderr)
            report = build_scoring_truth_action_readiness(
                **_inputs(
                    truth_validation_path=validation_path,
                    manual_events_path=manual_events,
                    manual_keypoints_path=manual_keypoints,
                    manual_semantics_path=manual_semantics,
                    scoring_truth_evaluation_path=evaluation_path,
                )
            )
        result = next(
            item for item in report["items"] if item["work_item_id"] == target["work_item_id"]
        )
        self.assertEqual(result["status"], "evidence_satisfied")
        self.assertEqual(result["matched_manual_event_id"], manual["event_id"])
        self.assertEqual(result["segment_iou"], 1.0)
        self.assertEqual(result["evidence_outcome"], "accepted_manual_phase_available")

    def test_self_declared_pose_evaluation_is_recomputed(self) -> None:
        changed = json.loads((POSE_TRUTH / "evaluation.json").read_text(encoding="utf-8"))
        changed["by_diagnostic_type"]["keypoint_jump"]["precision"] = 1.0
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "changed.json"
            path.write_text(json.dumps(changed), encoding="utf-8")
            with self.assertRaises(ValueError):
                build_scoring_truth_action_readiness(
                    **_inputs(pose_truth_evaluation_path=path)
                )

    def test_self_declared_feature_evaluation_is_recomputed(self) -> None:
        changed = json.loads(SCORING_EVALUATION.read_text(encoding="utf-8"))
        changed["feature_evaluation"]["error_budget"]["pose_error"] = {"mae": 0.0}
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "changed.json"
            path.write_text(json.dumps(changed), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "differs from recomputation"):
                build_scoring_truth_action_readiness(
                    **_inputs(scoring_truth_evaluation_path=path)
                )

    def test_validator_rejects_forged_satisfied_item(self) -> None:
        changed = copy.deepcopy(self.report)
        changed["items"][0]["status"] = "evidence_satisfied"
        changed["items"][0]["missing_evidence"] = []
        with self.assertRaisesRegex(ValueError, "status counts mismatch"):
            validate_scoring_truth_action_readiness(changed)

    @unittest.skipUnless(
        importlib.util.find_spec("jsonschema") is not None,
        "jsonschema unavailable",
    )
    def test_machine_schema_accepts_real_report(self) -> None:
        import jsonschema

        schema = json.loads(
            (ROOT / "contracts/scoring-truth-action-readiness.schema.json").read_text(
                encoding="utf-8"
            )
        )
        jsonschema.Draft202012Validator.check_schema(schema)
        jsonschema.Draft202012Validator(schema).validate(self.report)


if __name__ == "__main__":
    unittest.main()
