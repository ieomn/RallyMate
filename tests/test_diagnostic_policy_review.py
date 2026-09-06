from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from rallymate_evaluation.pose_diagnostic_truth import (
    DIAGNOSTIC_TYPES,
    coverage_csv_header,
    evaluate_pose_diagnostic_truth,
    positives_csv_header,
)
from rallymate_scoring.diagnostic_policy_review import (
    DiagnosticPolicyReviewError,
    review_pose_diagnostic_quality_gate,
    validate_diagnostic_gate_acceptance_protocol,
    validate_diagnostic_policy_review,
)
from tests.test_pose_diagnostic_truth import (
    PoseDiagnosticTruthTests,
    _csv_text,
)


ROOT = Path(__file__).resolve().parents[1]


class DiagnosticPolicyReviewTests(unittest.TestCase):
    def _fixture(self, directory: Path, *, matching: bool = True) -> tuple[dict, dict]:
        truth_fixture = PoseDiagnosticTruthTests()
        manifest, output = truth_fixture._blank_pack(
            directory, include_ambiguity=True
        )
        coverage_text = truth_fixture._coverage(accepted_types=set(DIAGNOSTIC_TYPES))
        if matching:
            common = {
                "video_id": "video-test",
                "from_source_track_id": "",
                "to_source_track_id": "",
                "review_status": "accepted",
                "annotator_ids": "coach-a;coach-b",
                "adjudicator_id": "reviewer-c",
                "adjudicated_at": "2026-08-21T21:00:00Z",
                "notes": "synthetic diagnostic policy contract test",
            }
            positives_text = _csv_text(
                positives_csv_header(),
                [
                    {
                        **common,
                        "truth_id": "truth-jump",
                        "diagnostic_type": "keypoint_jump",
                        "source_frame_index": 1,
                        "joint": "left_hip",
                        "left_joint": "",
                        "right_joint": "",
                    },
                    {
                        **common,
                        "truth_id": "truth-swap",
                        "diagnostic_type": "left_right_swap",
                        "source_frame_index": 2,
                        "joint": "",
                        "left_joint": "left_wrist",
                        "right_joint": "right_wrist",
                    },
                    {
                        **common,
                        "truth_id": "truth-ambiguity",
                        "diagnostic_type": "primary_identity_ambiguity",
                        "source_frame_index": 1,
                        "joint": "",
                        "left_joint": "",
                        "right_joint": "",
                    },
                    {
                        **common,
                        "truth_id": "truth-switch",
                        "diagnostic_type": "source_track_switch",
                        "source_frame_index": 2,
                        "joint": "",
                        "left_joint": "",
                        "right_joint": "",
                        "from_source_track_id": 1,
                        "to_source_track_id": 2,
                    },
                ],
            )
        else:
            positives_text = truth_fixture._positives()
        coverage_path = output / "pose-diagnostic-coverage.csv"
        positives_path = output / "pose-diagnostic-positives.csv"
        # Preserve the exact CRLF bytes emitted by ``_csv_text``.  Text-mode
        # writes on Windows would translate the existing LF a second time and
        # correctly trip the evaluator's content-binding guard.
        coverage_path.write_bytes(coverage_text.encode("utf-8"))
        positives_path.write_bytes(positives_text.encode("utf-8"))
        coverage_text = coverage_path.read_text(encoding="utf-8")
        positives_text = positives_path.read_text(encoding="utf-8")
        evaluation = evaluate_pose_diagnostic_truth(
            manifest=manifest,
            coverage_csv_text=coverage_text,
            positives_csv_text=positives_text,
            coverage_path=coverage_path,
            positives_path=positives_path,
            generated_at="2026-08-22T00:00:00Z",
        )
        evaluation_path = output / "evaluation-complete.json"
        evaluation_path.write_text(
            json.dumps(evaluation, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        return {
            "manifest": manifest,
            "evaluation": evaluation,
            "manifest_path": output / "manifest.json",
            "evaluation_path": evaluation_path,
        }, output

    def _protocol(self, bundle: dict, *, minimum_rate: float = 1.0) -> dict:
        manifest = bundle["manifest"]
        evaluation = bundle["evaluation"]
        return {
            "schema_version": "1.0.0",
            "protocol_version": "pose-diagnostic-gate-acceptance-protocol-v1.0.0",
            "protocol_id": "synthetic-preregistered-diagnostic-policy-v1",
            "artifact_scope": "external_preregistered_diagnostic_gate_protocol",
            "created_at": "2026-08-21T22:00:00Z",
            "registered_at": "2026-08-21T23:00:00Z",
            "registered_by": {
                "reviewer_id": "synthetic-protocol-reviewer",
                "role": "contract-test-only",
            },
            "registration": {
                "registry_id": "synthetic-external-registry",
                "source_kind": "external_protocol_registry",
                "source_sha256": "A" * 64,
            },
            "quality_policy_version_under_review": "indicator-event-quality-v1.6.0",
            "evaluation_version": "pose-diagnostic-evaluation-v1.0.0",
            "matching_protocol_version": "exact-source-frame-and-diagnostic-scope-v1",
            "diagnostic_types": list(DIAGNOSTIC_TYPES),
            "expected_scopes": [
                {
                    "scope_id": "synthetic-video-test-scope",
                    "video_id": manifest["video_id"],
                    "truth_pack_manifest_content_sha256": evaluation["source"][
                        "truth_pack_manifest_content_sha256"
                    ],
                    "truth_pack_source_queue_sha256": manifest["source_queue"][
                        "sha256"
                    ],
                    "queue_artifact_binding_sha256": manifest["source_queue"][
                        "artifact_binding_sha256"
                    ],
                }
            ],
            "requirements": {
                "require_all_expected_scopes": True,
                "per_diagnostic_type": [
                    {
                        "diagnostic_type": diagnostic_type,
                        "require_full_timeline_per_scope": True,
                        "minimum_evaluated_scopes": 1,
                        "minimum_total_truth_positives": 1,
                        "minimum_precision": minimum_rate,
                        "minimum_recall": minimum_rate,
                        "minimum_f1": minimum_rate,
                    }
                    for diagnostic_type in DIAGNOSTIC_TYPES
                ],
            },
            "safety": {
                "thresholds_are_A_to_E_scoring_thresholds": False,
                "automatic_quality_policy_change_allowed": False,
                "result_revealed_before_registration": False,
            },
        }

    def test_real_blank_bundles_require_annotation_and_external_protocol(self) -> None:
        bundles = []
        for name in ("halpe26-same-window-v1", "wholebody133-same-window-v1"):
            directory = ROOT / "reports" / "pose-diagnostic-truth" / name
            bundles.append(
                {
                    "manifest": json.loads(
                        (directory / "manifest.json").read_text(encoding="utf-8")
                    ),
                    "evaluation": json.loads(
                        (directory / "evaluation.json").read_text(encoding="utf-8")
                    ),
                    "manifest_path": directory / "manifest.json",
                    "evaluation_path": directory / "evaluation.json",
                }
            )
        report = review_pose_diagnostic_quality_gate(evaluation_bundles=bundles)
        self.assertEqual(report["status"], "annotation_and_protocol_required")
        self.assertIn(
            "external_preregistered_acceptance_protocol_missing", report["blockers"]
        )
        self.assertIn("full_timeline_diagnostic_truth_missing", report["blockers"])
        self.assertFalse(report["policy_decision"]["quality_policy_change_applied"])
        self.assertTrue(
            all(row["criteria"] is None for row in report["by_diagnostic_type"].values())
        )

    def test_complete_preregistered_evidence_is_only_eligible_for_human_review(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            bundle, directory = self._fixture(Path(temporary), matching=True)
            protocol = self._protocol(bundle)
            protocol_path = directory / "protocol.json"
            protocol_path.write_text(json.dumps(protocol), encoding="utf-8")
            report = review_pose_diagnostic_quality_gate(
                evaluation_bundles=[bundle],
                protocol=protocol,
                protocol_path=protocol_path,
                generated_at="2026-08-22T01:00:00Z",
            )
            self.assertEqual(report["status"], "eligible_for_human_policy_review")
            self.assertTrue(
                all(
                    row["status"]
                    == "criteria_passed_pending_human_policy_review"
                    for row in report["by_diagnostic_type"].values()
                )
            )
            self.assertFalse(report["policy_decision"]["automatic_policy_change_allowed"])
            self.assertIsNone(report["policy_decision"]["next_quality_policy_version"])
            validate_diagnostic_policy_review(report)

    def test_failed_metric_stays_fail_closed(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            bundle, directory = self._fixture(Path(temporary), matching=False)
            protocol = self._protocol(bundle, minimum_rate=1.0)
            protocol_path = directory / "protocol.json"
            protocol_path.write_text(json.dumps(protocol), encoding="utf-8")
            report = review_pose_diagnostic_quality_gate(
                evaluation_bundles=[bundle],
                protocol=protocol,
                protocol_path=protocol_path,
            )
            self.assertEqual(report["status"], "acceptance_criteria_failed")
            self.assertEqual(
                report["by_diagnostic_type"]["left_right_swap"]["status"],
                "acceptance_criteria_failed",
            )
            self.assertFalse(report["safety"]["quality_gate_modified"])

    def test_protocol_must_predate_results_and_template_is_not_executable(self) -> None:
        template = json.loads(
            (
                ROOT
                / "examples"
                / "pose-diagnostic-gate-acceptance-protocol.template.json"
            ).read_text(encoding="utf-8")
        )
        with self.assertRaisesRegex(DiagnosticPolicyReviewError, "not externally"):
            validate_diagnostic_gate_acceptance_protocol(template)
        with tempfile.TemporaryDirectory() as temporary:
            bundle, directory = self._fixture(Path(temporary), matching=True)
            protocol = self._protocol(bundle)
            protocol["registered_at"] = "2026-08-22T02:00:00Z"
            protocol_path = directory / "late-protocol.json"
            protocol_path.write_text(json.dumps(protocol), encoding="utf-8")
            with self.assertRaisesRegex(DiagnosticPolicyReviewError, "before evaluation"):
                review_pose_diagnostic_quality_gate(
                    evaluation_bundles=[bundle],
                    protocol=protocol,
                    protocol_path=protocol_path,
                )

    def test_evaluation_is_recomputed_from_bound_csv_before_policy_review(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            bundle, output = self._fixture(Path(temporary), matching=True)
            positives = output / "pose-diagnostic-positives.csv"
            positives.write_text(
                positives.read_text(encoding="utf-8").replace("truth-jump", "truth-changed"),
                encoding="utf-8",
            )
            with self.assertRaisesRegex(
                DiagnosticPolicyReviewError, "does not exactly match recomputed"
            ):
                review_pose_diagnostic_quality_gate(evaluation_bundles=[bundle])

    def test_cli_emits_safe_real_empty_review_and_refuses_overwrite(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            output = Path(temporary) / "review.json"
            command = [
                sys.executable,
                str(ROOT / "scripts" / "review_pose_diagnostic_quality_gate.py"),
            ]
            for name in ("halpe26-same-window-v1", "wholebody133-same-window-v1"):
                directory = ROOT / "reports" / "pose-diagnostic-truth" / name
                command.extend(
                    [
                        "--bundle",
                        str(directory / "manifest.json"),
                        str(directory / "evaluation.json"),
                    ]
                )
            command.extend(["--output", str(output)])
            first = subprocess.run(
                command, cwd=ROOT, capture_output=True, text=True, check=False
            )
            self.assertEqual(first.returncode, 0, first.stderr)
            report = json.loads(output.read_text(encoding="utf-8"))
            self.assertEqual(report["status"], "annotation_and_protocol_required")
            second = subprocess.run(
                command, cwd=ROOT, capture_output=True, text=True, check=False
            )
            self.assertNotEqual(second.returncode, 0)
            self.assertIn("refusing to overwrite", second.stderr)


if __name__ == "__main__":
    unittest.main()
