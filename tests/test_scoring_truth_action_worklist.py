from __future__ import annotations

import copy
import importlib.util
import json
import tempfile
import unittest
from pathlib import Path

from rallymate_annotation.scoring_action_worklist import (
    build_scoring_truth_action_worklist,
    validate_scoring_truth_action_worklist,
    write_scoring_truth_action_csv,
    write_scoring_truth_action_html,
)


ROOT = Path(__file__).resolve().parents[1]
RUN = ROOT / "reports/scoring-candidate-multivideo-m53/runs/850cb0006b406c7176eeda8d711cd065"
AUDIT = ROOT / "reports/scoring-blocker-audit-halpe256-full-m53.json"
QUEUE = ROOT / "reports/pose-diagnostic-review/halpe26-full-m53/queue.json"
VIDEO = ROOT / "FULL-TEST/850cb0006b406c7176eeda8d711cd065.mp4"
CONTEXT = ROOT / "data/annotations/scoring-reference-context-v1/850cb0006b406c7176eeda8d711cd065-fs02-target-directions.json"
REGISTRY = ROOT / "metric-feasibility-pose-wave-v2.json"


def _build(**overrides):
    inputs = {
        "scores_path": RUN / "scores.jsonl",
        "events_path": RUN / "events.jsonl",
        "summary_path": RUN / "scoring-loop-summary.json",
        "blocker_audit_path": AUDIT,
        "diagnostic_queue_path": QUEUE,
        "review_video_path": VIDEO,
        "reference_context_path": CONTEXT,
        "registry_path": REGISTRY,
    }
    inputs.update(overrides)
    return build_scoring_truth_action_worklist(**inputs)


class ScoringTruthActionWorklistTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.report = _build()

    def test_current_worklist_covers_every_unavailable_instance(self) -> None:
        report = self.report
        validate_scoring_truth_action_worklist(report)
        counts = report["counts"]
        self.assertEqual(counts["indicator_record_count"], 442)
        self.assertEqual(counts["calibration_required_indicator_instances"], 176)
        self.assertEqual(counts["unavailable_indicator_instances"], 266)
        self.assertEqual(counts["actionable_unavailable_indicator_instances"], 266)
        self.assertEqual(counts["work_items"], 168)
        self.assertEqual(counts["pose_diagnostic_items_missing_queue_task"], 0)
        self.assertEqual(counts["accepted_annotations"], 0)
        self.assertFalse(report["safety"]["grades_generated"])
        self.assertFalse(report["safety"]["thresholds_generated"])

    def test_measurement_context_and_pose_causes_are_not_conflated(self) -> None:
        counts = self.report["counts"]
        self.assertEqual(
            counts["by_review_type"],
            {
                "manual_event_or_semantic_truth": 31,
                "manual_keypoint_feature_truth": 11,
                "pose_diagnostic_truth": 92,
                "scoring_reference_context": 34,
            },
        )
        target_items = [
            item
            for item in self.report["items"]
            if item["truth_requirement"] == "manual_target_direction_semantics"
        ]
        self.assertEqual(len(target_items), 34)
        self.assertTrue(
            all(item["event_code"] == "FS02" for item in target_items)
        )
        self.assertTrue(
            all(
                any(
                    feature["feature_name"]
                    == "target_direction_alignment_error_deg"
                    for feature in item["invalid_features"]
                )
                for item in target_items
            )
        )
        measurement_items = [
            item
            for item in self.report["items"]
            if item["review_type"] == "manual_keypoint_feature_truth"
        ]
        self.assertTrue(measurement_items)
        self.assertTrue(
            all(
                feature["feature_name"] != "target_direction_alignment_error_deg"
                for item in measurement_items
                for feature in item["invalid_features"]
            )
        )

    def test_source_hash_drift_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            changed = Path(directory) / "audit.json"
            payload = json.loads(AUDIT.read_text(encoding="utf-8"))
            payload["source"]["scores"]["sha256"] = "0" * 64
            changed.write_text(json.dumps(payload), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "does not bind supplied scores"):
                _build(blocker_audit_path=changed)

    def test_validator_rejects_dropped_instance_action(self) -> None:
        changed = copy.deepcopy(self.report)
        changed["items"][0]["affected_indicator_instances"].pop()
        changed["items"][0]["affected_indicator_count"] -= 1
        with self.assertRaisesRegex(
            ValueError, "instance action link count mismatch|actionable coverage"
        ):
            validate_scoring_truth_action_worklist(changed)

    def test_html_and_csv_are_video_first_and_non_scoring(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            html_path = root / "index.html"
            csv_path = root / "worklist.csv"
            write_scoring_truth_action_html(self.report, html_path, VIDEO)
            write_scoring_truth_action_csv(self.report, csv_path)
            rendered = html_path.read_text(encoding="utf-8")
            self.assertIn("<video", rendered)
            self.assertIn("266 条 unavailable 已全部分配", rendered)
            self.assertNotIn("grade=A", rendered)
            self.assertTrue(csv_path.read_text(encoding="utf-8-sig").startswith("priority_rank,"))

    @unittest.skipUnless(
        importlib.util.find_spec("jsonschema") is not None,
        "jsonschema unavailable",
    )
    def test_machine_schema_accepts_real_worklist(self) -> None:
        import jsonschema

        schema = json.loads(
            (ROOT / "contracts/scoring-truth-action-worklist.schema.json").read_text(
                encoding="utf-8"
            )
        )
        jsonschema.Draft202012Validator.check_schema(schema)
        jsonschema.Draft202012Validator(schema).validate(self.report)


if __name__ == "__main__":
    unittest.main()
