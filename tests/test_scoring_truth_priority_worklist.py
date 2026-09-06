from __future__ import annotations

import importlib.util
import json
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def _load_script():
    path = ROOT / "scripts" / "build_scoring_truth_priority_worklist.py"
    spec = importlib.util.spec_from_file_location("build_scoring_truth_priority_worklist", path)
    if spec is None or spec.loader is None:
        raise RuntimeError(path)
    module = importlib.util.module_from_spec(spec); spec.loader.exec_module(module); return module


class ScoringTruthPriorityWorklistTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.module = _load_script()

    def test_real_worklist_is_hash_bound_video_first_and_non_scoring(self) -> None:
        report = self.module.build_truth_priority_worklist(
            scores_path=ROOT / "reports/fs09-pose-wave-v2/850cb0006b406c7176eeda8d711cd065/scores.jsonl",
            events_path=ROOT / "reports/fs09-pose-wave-v2/850cb0006b406c7176eeda8d711cd065/events.jsonl",
            summary_path=ROOT / "reports/fs09-pose-wave-v2/850cb0006b406c7176eeda8d711cd065/scoring-loop-summary.json",
            blocker_audit_path=ROOT / "reports/scoring-blocker-audit-halpe256-full.json",
            diagnostic_queue_path=ROOT / "reports/pose-diagnostic-review/halpe26-full/queue.json",
            review_video_path=ROOT / "reports/pose-scoring-ab/yolo-vs-rtmpose-scoring-comparison-browser.mp4",
        )
        self.module.validate_truth_priority_worklist(report)
        self.assertEqual(227, report["counts"]["eligible_complete_feature_score_only_indicator_instances"])
        self.assertEqual(0, report["counts"]["accepted_annotations"])
        self.assertFalse(report["safety"]["grades_generated"])
        self.assertEqual("calibration_required", report["safety"]["maximum_post_review_status_without_calibration"])
        self.assertEqual(list(range(1, len(report["items"]) + 1)), [item["priority_rank"] for item in report["items"]])

    def test_hash_mismatch_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            changed = Path(directory) / "audit.json"
            payload = json.loads((ROOT / "reports/scoring-blocker-audit-halpe256-full.json").read_text(encoding="utf-8"))
            payload["source"]["scores"]["sha256"] = "0" * 64
            changed.write_text(json.dumps(payload), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "does not bind"):
                self.module.build_truth_priority_worklist(
                    scores_path=ROOT / "reports/fs09-pose-wave-v2/850cb0006b406c7176eeda8d711cd065/scores.jsonl",
                    events_path=ROOT / "reports/fs09-pose-wave-v2/850cb0006b406c7176eeda8d711cd065/events.jsonl",
                    summary_path=ROOT / "reports/fs09-pose-wave-v2/850cb0006b406c7176eeda8d711cd065/scoring-loop-summary.json",
                    blocker_audit_path=changed,
                    diagnostic_queue_path=ROOT / "reports/pose-diagnostic-review/halpe26-full/queue.json",
                    review_video_path=ROOT / "reports/pose-scoring-ab/yolo-vs-rtmpose-scoring-comparison-browser.mp4",
                )


if __name__ == "__main__": unittest.main()
