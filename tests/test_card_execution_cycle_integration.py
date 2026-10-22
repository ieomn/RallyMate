"""Media-free merge/cache contracts; mock results do not prove video accuracy."""
from collections import Counter
from copy import deepcopy
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from rallymate_scoring.card_evaluation import summarize_card_scores
import rallymate_service.card_execution as execution
from rallymate_service.footwork_cycle_artifacts import compute_verified_cycle_snapshot
from tests.test_footwork_cycle_artifacts import fixture
from tests.test_card_execution import real_fixture


def other_cards(report):
    return [row for row in report["cards"] if row["event_code"] not in {"FS03", "FS04"}]


class CycleUnifiedContracts(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        sequence, events, timeline = real_fixture()
        cls.baseline = execution.build_card_execution_from_sequence(sequence, events, timeline)
        arguments = fixture()
        cls.document = arguments[-1]
        cls.cycle = compute_verified_cycle_snapshot(*arguments)

    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.directory = Path(temporary.name)
        self.report = deepcopy(self.baseline)
        self.cycle = deepcopy(self.cycle)
        # Only the production function is mocked; these flags are a wiring
        # fixture and never enter saved real-video validation counts.
        self.cycle["source_run_id"] = self.report["source_run_id"]
        self.cycle["input"].update(service_eligible=True, artifact_files_verified=True, decoder_verified_from_original_video=True)

    def annotate(self):
        path = self.directory / "footwork-cycle-annotations.json"
        path.write_text(json.dumps(self.document), encoding="utf-8")
        return path

    def test_absence_leaves_every_card_unchanged(self):
        before = deepcopy(self.report)
        with patch("rallymate_service.footwork_cycle_artifacts.build_footwork_cycle_artifact_report") as production:
            execution._apply_optional_cycle_annotations(self.report, self.directory)
        production.assert_not_called()
        self.assertEqual(self.report, before)

    def test_verified_result_only_replaces_ten_cycle_cards_and_recounts_all_298(self):
        self.annotate()
        with patch("rallymate_service.footwork_cycle_artifacts.build_footwork_cycle_artifact_report", return_value=self.cycle):
            execution._apply_optional_cycle_annotations(self.report, self.directory)
        self.assertEqual(len(self.report["cards"]), 298)
        self.assertEqual(other_cards(self.report), other_cards(self.baseline))
        self.assertEqual(self.report["cycle_windows"], self.cycle["cycle_windows"])
        self.assertEqual(self.report["input"]["footwork_cycle_execution"]["status"], "executed")
        instances = [instance for row in self.report["cards"] for instance in row["instances"]]
        self.assertEqual(self.report["coverage"]["computed_card_instance_count"], len(instances))
        self.assertEqual(self.report["coverage"]["run_card_status_counts"], dict(Counter(row["run_status"] for row in self.report["cards"])))
        self.assertEqual(self.report["summary"], summarize_card_scores(instances, [row["indicator_id"] for row in self.report["cards"]]))
        self.assertEqual(self.report["summary"]["channels"]["original"]["scored_cards"], 0)
        self.assertEqual(self.report["overall_score"], self.report["summary"]["channels"]["formal"]["score"])

    def test_failure_keeps_previously_computed_fs_and_gs_cards(self):
        self.annotate()
        self.report["input"]["gs_execution"] = {"status": "executed"}
        self.report["gs_stage_windows"] = [{"existing_stage": "preserve"}]
        with patch("rallymate_service.footwork_cycle_artifacts.build_footwork_cycle_artifact_report", side_effect=ValueError("cycle_source_window_frames_missing")):
            execution._apply_optional_cycle_annotations(self.report, self.directory)
        self.assertEqual(other_cards(self.report), other_cards(self.baseline))
        self.assertEqual(self.report["summary"], self.baseline["summary"])
        self.assertEqual(self.report["input"]["gs_execution"], {"status": "executed"})
        self.assertEqual(self.report["gs_stage_windows"], [{"existing_stage": "preserve"}])
        self.assertTrue(all(row["run_status"] == "unavailable" and not row["instances"]
            for row in self.report["cards"] if row["event_code"] in {"FS03", "FS04"}))

    def test_unverified_foreign_run_rule_or_incomplete_inventory_cannot_merge(self):
        self.annotate()
        for target in ("service_eligible", "artifact_files_verified", "decoder_verified_from_original_video", "source_run_id", "rule_version", "cards"):
            with self.subTest(target=target):
                report, cycle = deepcopy(self.baseline), deepcopy(self.cycle)
                if target in cycle["input"]:
                    cycle["input"][target] = False
                elif target == "cards":
                    cycle["cards"].pop()
                else:
                    cycle[target] = "another-value"
                with patch("rallymate_service.footwork_cycle_artifacts.build_footwork_cycle_artifact_report", return_value=cycle):
                    execution._apply_optional_cycle_annotations(report, self.directory)
                self.assertEqual(report["input"]["footwork_cycle_execution"]["status"], "unavailable")
                self.assertEqual(other_cards(report), other_cards(self.baseline))

    def test_original_video_and_annotation_content_bind_cache_even_with_preserved_mtime(self):
        for name in ("frames.jsonl", "events.jsonl", "primary-player.jsonl"):
            (self.directory / name).write_text("{}\n", encoding="utf-8")
        video = self.directory / "source.mp4"
        video.write_bytes(b"video-content-for-cache-contract-only")
        (self.directory / "summary.json").write_text(json.dumps({"input": {"video_path": str(video)}}), encoding="utf-8")
        path = self.annotate()
        before = execution._input_signature(self.directory)
        metadata = path.stat()
        content = path.read_bytes()
        changed = content.replace(b"contract-test-v1", b"contract-test-v2")
        self.assertNotEqual(content, changed)
        self.assertEqual(len(content), len(changed))
        path.write_bytes(changed)
        os.utime(path, ns=(metadata.st_atime_ns, metadata.st_mtime_ns))
        after = execution._input_signature(self.directory)
        self.assertNotEqual(before, after)
        metadata = video.stat()
        video.write_bytes(video.read_bytes().replace(b"video", b"VIDEO"))
        os.utime(video, ns=(metadata.st_atime_ns, metadata.st_mtime_ns))
        self.assertNotEqual(after, execution._input_signature(self.directory))


if __name__ == "__main__":
    unittest.main()
