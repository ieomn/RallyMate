from __future__ import annotations

import copy
import json
from pathlib import Path
import subprocess
import sys
import unittest

from rallymate_evaluation.f2_error_budget_readiness import (
    validate_f2_error_budget_readiness,
    validate_f2_error_budget_readiness_sources,
)


ROOT = Path(__file__).resolve().parents[1]
REPORT = ROOT / "reports" / "f2-error-budget-readiness-m64.json"


class F2ErrorBudgetReadinessTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.report = json.loads(REPORT.read_text(encoding="utf-8"))

    def test_real_report_replays_current_registry_and_three_video_sources(self) -> None:
        validate_f2_error_budget_readiness_sources(self.report)
        counts = self.report["counts"]
        self.assertEqual(counts["videos"], 3)
        self.assertEqual(counts["indicators"], 13)
        self.assertEqual(counts["required_pose_features"], 51)
        self.assertEqual(counts["smoothing_computed_records"], 9965)
        self.assertEqual(counts["smoothing_valid_numeric_records"], 9971)
        self.assertEqual(counts["smoothing_counterfactual_coverage"], 0.99939825)

    def test_every_indicator_separates_smoothing_from_missing_truth(self) -> None:
        rows = {row["indicator_id"]: row for row in self.report["indicators"]}
        self.assertEqual(len(rows), 13)
        complete = {
            indicator_id
            for indicator_id, row in rows.items()
            if row["smoothing_counterfactual"]["status"] == "complete"
        }
        self.assertEqual(len(complete), 10)
        self.assertEqual(
            set(rows) - complete,
            {"FS01-M04", "FS02-M04", "FS02-M05"},
        )
        for row in rows.values():
            self.assertEqual(row["calculation"]["status"], "measured_in_every_video")
            self.assertEqual(row["event_error"]["status"], "ground_truth_required")
            for component in (
                "feature_error",
                "pose_error",
                "event_boundary_error",
                "truth_conditioned_smoothing_error",
                "missing_value_error_impact",
            ):
                self.assertEqual(row[component]["status"], "ground_truth_required")
            self.assertEqual(
                row["grade_gap_assessment"]["status"],
                "external_assessment_required",
            )
            self.assertFalse(row["f2_to_f3_ready"])

    def test_empty_truth_cannot_be_relabelled_as_accuracy_or_promotion(self) -> None:
        self.assertEqual(self.report["status"], "ground_truth_required")
        self.assertEqual(self.report["counts"]["manual_event_records"], 0)
        self.assertEqual(self.report["counts"]["manual_keypoint_records"], 0)
        self.assertEqual(self.report["counts"]["manual_semantic_records"], 0)
        self.assertEqual(self.report["counts"]["indicators_ready_for_f2_to_f3"], 0)
        forged = copy.deepcopy(self.report)
        forged["safety"]["feature_accuracy_claim"] = True
        with self.assertRaisesRegex(ValueError, "unsafe"):
            validate_f2_error_budget_readiness(forged)
        promoted = copy.deepcopy(self.report)
        promoted["indicators"][0]["f2_to_f3_ready"] = True
        with self.assertRaisesRegex(ValueError, "F2-to-F3"):
            validate_f2_error_budget_readiness(promoted)

    def test_machine_schema_accepts_real_report(self) -> None:
        try:
            import jsonschema
        except ImportError:
            self.skipTest("jsonschema unavailable")
        schema = json.loads(
            (ROOT / "contracts" / "f2-error-budget-readiness.schema.json").read_text(
                encoding="utf-8"
            )
        )
        jsonschema.Draft202012Validator.check_schema(schema)
        jsonschema.Draft202012Validator(schema).validate(self.report)

    def test_cli_refuses_to_overwrite_current_report(self) -> None:
        command = [
            sys.executable,
            str(ROOT / "scripts" / "build_f2_error_budget_readiness.py"),
            "--registry",
            str(ROOT / "metric-feasibility-pose-wave-v2.json"),
            "--smoothing-coverage",
            str(ROOT / "reports" / "smoothing-counterfactual-coverage-m63.json"),
            "--calculation-coverage",
            str(
                ROOT
                / "reports"
                / "multivideo-indicator-calculation-coverage"
                / "m63-halpe26-three-video-fs02-m05-first-step-evidence-v1"
                / "coverage.json"
            ),
            "--run-directory",
            str(ROOT / "reports" / "scoring-candidate-multivideo-m63" / "runs" / "3ae77ee3271d67de171585a5c39ddd69"),
            "--manual-events",
            str(ROOT / "data" / "annotations" / "scoring-truth-pack-v1" / "compiled" / "manual-events.jsonl"),
            "--manual-keypoints",
            str(ROOT / "data" / "annotations" / "scoring-truth-pack-v1" / "compiled" / "manual-keypoints.jsonl"),
            "--manual-semantics",
            str(ROOT / "data" / "annotations" / "scoring-truth-pack-v1" / "compiled" / "manual-semantics.jsonl"),
            "--output",
            str(REPORT),
        ]
        completed = subprocess.run(
            command,
            cwd=ROOT,
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(completed.returncode, 2)
        self.assertIn("output already exists", completed.stderr)


if __name__ == "__main__":
    unittest.main()
